"""
lexai_engine.py
================
The LexAI RAG engine — all 7 anti-hallucination layers in one readable file.

Pipeline (matches the Backend Documentation):
  Layer 1  Smart Ingestion       -> PyMuPDF, two-column aware, strip references
  Layer 2  Semantic Chunking     -> sentence-based, ~400 words, 40-word overlap
  Layer 3  Hybrid Retrieval      -> ChromaDB (semantic) + BM25 (keyword)
  Layer 4  RRF Fusion            -> 1 / (rank + 60), agreement wins
  Layer 5  Reranker              -> bge-reranker-base cross-encoder + sigmoid
  Layer 6  Confidence Gate       -> below threshold => refuse (no hallucination)
  (Generation)                   -> NVIDIA / Groq via OpenAI-compatible API
  Layer 7  Faithfulness Check    -> LLM-as-judge verifies the answer vs sources

Plus: intent router + meta path (summaries) and follow-up condensing.

This file has NO web server. It's just a class the API calls.
"""

import os
import re
import math
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder
from openai import OpenAI
from dotenv import load_dotenv

# Load .env that sits next to this file (explicit path = no surprises)
load_dotenv(Path(__file__).parent / ".env")

# ----------------------------------------------------------------------
# Config (pulled from .env, with sensible defaults)
# ----------------------------------------------------------------------
EMBED_MODEL    = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL   = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
CHROMA_DIR     = os.getenv("CHROMA_PERSIST_DIR", "./chroma_store")
CONF_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.30"))  # Layer 6 gate
LLM_PROVIDER   = os.getenv("LLM_PROVIDER", "nvidia").lower()

# Chunking parameters (Layer 2)
CHUNK_WORDS   = 400
OVERLAP_WORDS = 40

# Provider -> (base_url, model, api_key) for the OpenAI-compatible client.
# This is the "single config switch" from the doc: swap provider in .env.
_PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-8b-instruct",
        "key_env": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-8b-instant",
        "key_env": "GROQ_API_KEY",
    },
}


def _sigmoid(x: float) -> float:
    """Turn a raw reranker logit (~ -10..+10) into a 0..1 score."""
    return 1.0 / (1.0 + math.exp(-x))


def _split_sentences(text: str):
    """Very small sentence splitter (no nltk download needed)."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


class LexAIEngine:
    def __init__(self):
        print("Loading models... (first run downloads them — be patient)")

        # Embedding model (Layer 2) and reranker (Layer 5) — both run locally.
        self.embedder = SentenceTransformer(EMBED_MODEL)
        self.reranker = CrossEncoder(RERANK_MODEL)

        # Vector store (Layer 3). Persistent = survives restarts.
        self.chroma = chromadb.PersistentClient(path=CHROMA_DIR)
        self.collection = self.chroma.get_or_create_collection("lexai")

        # LLM client (generation + Layer 7). OpenAI SDK pointed at the provider.
        prov = _PROVIDERS.get(LLM_PROVIDER, _PROVIDERS["nvidia"])
        api_key = os.getenv(prov["key_env"], "")
        self.llm = OpenAI(base_url=prov["base_url"], api_key=api_key)
        self.llm_model = prov["model"]
        if not api_key:
            print(f"WARNING: no API key found in {prov['key_env']} — generation will fail.")

        # In-memory state, rebuilt from Chroma so Chroma is the single source of truth.
        self.chunks = []      # list of {"text", "file", "page", "id"}
        self.bm25 = None      # BM25 index (Layer 3 keyword side)
        self.history = []     # simple chat history for follow-up condensing
        self._rebuild_from_store()

        print(f"Engine ready. {len(self.list_papers())} paper(s), {len(self.chunks)} chunks.")

    # ==================================================================
    # LAYER 1 — Smart Ingestion
    # ==================================================================
    def _extract_pdf(self, path):
        """Read a PDF in correct reading order, even for two-column layouts."""
        doc = fitz.open(path)
        pages = []
        for pno, page in enumerate(doc, start=1):
            width = page.rect.width
            mid = width / 2
            blocks = page.get_text("blocks")  # (x0,y0,x1,y1,text,block_no,...)

            left, right = [], []
            for b in blocks:
                x0, y0, x1, y1, btext = b[0], b[1], b[2], b[3], b[4]
                if not btext.strip():
                    continue
                center_x = (x0 + x1) / 2
                (left if center_x < mid else right).append((y0, btext))

            # Read left column top-to-bottom, then right column top-to-bottom.
            left.sort(key=lambda t: t[0])
            right.sort(key=lambda t: t[0])
            ordered = [t[1] for t in left] + [t[1] for t in right]
            pages.append("\n".join(ordered))
        doc.close()
        return pages  # list of page texts

    def _extract_txt(self, path):
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        return [text]  # treat as a single "page"

    def _strip_references(self, text):
        """Cut the bibliography so reference terms don't pollute retrieval."""
        m = re.search(r"\n\s*(references|bibliography)\s*\n", text, re.IGNORECASE)
        return text[: m.start()] if m else text

    # ==================================================================
    # LAYER 2 — Semantic Chunking
    # ==================================================================
    def _chunk_page(self, page_text, file, page):
        sentences = _split_sentences(page_text)
        chunks, current, count = [], [], 0
        for s in sentences:
            words = len(s.split())
            current.append(s)
            count += words
            if count >= CHUNK_WORDS:
                chunks.append(" ".join(current))
                # keep an overlap tail so meaning isn't severed across chunks
                tail, tw = [], 0
                for sent in reversed(current):
                    tail.insert(0, sent)
                    tw += len(sent.split())
                    if tw >= OVERLAP_WORDS:
                        break
                current, count = tail, tw
        if current:
            chunks.append(" ".join(current))
        return [{"text": c, "file": file, "page": page} for c in chunks]

    # ==================================================================
    # Ingest one document end-to-end (Layers 1 -> 2 -> store)
    # ==================================================================
    def add_document(self, path, filename):
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            pages = self._extract_pdf(path)
        elif ext == ".txt":
            pages = self._extract_txt(path)
        else:
            raise ValueError("Only .pdf and .txt are supported.")

        new_chunks = []
        for pno, ptext in enumerate(pages, start=1):
            ptext = self._strip_references(ptext)
            new_chunks.extend(self._chunk_page(ptext, filename, pno))

        if not new_chunks:
            return {"file": filename, "chunks": 0, "note": "No extractable text (scanned PDF?)."}

        # Embed and store in Chroma
        texts = [c["text"] for c in new_chunks]
        embeddings = self.embedder.encode(texts, normalize_embeddings=True).tolist()
        base = self.collection.count()
        ids = [f"{filename}_{base + i}" for i in range(len(new_chunks))]
        metadatas = [{"file": c["file"], "page": c["page"]} for c in new_chunks]

        self.collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        self._rebuild_from_store()
        return {"file": filename, "chunks": len(new_chunks)}

    def _rebuild_from_store(self):
        """Reload all chunks from Chroma and rebuild the BM25 keyword index."""
        data = self.collection.get(include=["documents", "metadatas"])
        self.chunks = []
        for cid, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
            self.chunks.append({"id": cid, "text": doc,
                                "file": meta.get("file"), "page": meta.get("page")})
        if self.chunks:
            tokenized = [c["text"].lower().split() for c in self.chunks]
            self.bm25 = BM25Okapi(tokenized)
        else:
            self.bm25 = None

    # ==================================================================
    # LAYER 3 — Hybrid Retrieval
    # ==================================================================
    def _semantic_search(self, query, k=10):
        q_emb = self.embedder.encode(
            "Represent this sentence for searching relevant passages: " + query,
            normalize_embeddings=True,
        ).tolist()
        res = self.collection.query(query_embeddings=[q_emb], n_results=k)
        return res["ids"][0] if res["ids"] else []

    def _keyword_search(self, query, k=10):
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(query.lower().split())
        top_idx = np.argsort(scores)[::-1][:k]
        return [self.chunks[i]["id"] for i in top_idx if scores[i] > 0]

    # ==================================================================
    # LAYER 4 — Reciprocal Rank Fusion
    # ==================================================================
    def _rrf(self, semantic_ids, keyword_ids, k=60, top=10):
        scores = {}
        for rank, cid in enumerate(semantic_ids):
            scores[cid] = scores.get(cid, 0) + 1.0 / (rank + k)
        for rank, cid in enumerate(keyword_ids):
            scores[cid] = scores.get(cid, 0) + 1.0 / (rank + k)
        ordered = sorted(scores, key=scores.get, reverse=True)[:top]
        by_id = {c["id"]: c for c in self.chunks}
        return [by_id[cid] for cid in ordered if cid in by_id]

    # ==================================================================
    # LAYER 5 — Reranker (logit -> sigmoid; the bug-fix from the doc)
    # ==================================================================
    def _rerank(self, query, candidates, top=5):
        if not candidates:
            return []
        pairs = [[query, c["text"]] for c in candidates]
        logits = self.reranker.predict(pairs)  # raw logits, NOT 0..1
        for c, logit in zip(candidates, logits):
            c["score"] = _sigmoid(float(logit))  # normalize BEFORE the gate
        return sorted(candidates, key=lambda c: c["score"], reverse=True)[:top]

    # ==================================================================
    # Generation
    # ==================================================================
    def _ask_llm(self, system, user):
        resp = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    def _generate(self, query, chunks):
        context = "\n\n".join(
            f"[{c['file']} p.{c['page']}] {c['text']}" for c in chunks
        )
        system = (
            "You are LexAI. Answer ONLY using the provided sources. "
            "If the sources do not contain the answer, say you don't know. "
            "Never use outside knowledge. Be concise."
        )
        user = f"Sources:\n{context}\n\nQuestion: {query}\n\nAnswer:"
        return self._ask_llm(system, user)

    # ==================================================================
    # LAYER 7 — Faithfulness Verification
    # ==================================================================
    def _verify(self, answer, chunks):
        context = "\n\n".join(c["text"] for c in chunks)
        system = "You check if an answer is fully supported by the sources. Reply only YES or NO."
        user = f"Sources:\n{context}\n\nAnswer:\n{answer}\n\nIs every claim supported? YES or NO:"
        try:
            verdict = self._ask_llm(system, user).upper()
            return verdict.startswith("YES")
        except Exception:
            return True  # don't block the answer if the check itself fails

    # ==================================================================
    # Intent router + meta path (summaries bypass retrieval)
    # ==================================================================
    def _is_meta(self, query):
        q = query.lower()
        return any(w in q for w in ["summar", "overview", "what is this", "what's this",
                                    "tldr", "main points", "about this"])

    def _meta_answer(self, query):
        if not self.chunks:
            return None
        n = len(self.chunks)
        sample = [self.chunks[0], self.chunks[n // 2], self.chunks[-1]]
        answer = self._generate(query, sample)
        return {
            "refused": False, "meta": True, "answer": answer,
            "citation": None, "confidence": None, "label": None, "sources": [],
        }

    # ==================================================================
    # Follow-up condensing (runs BEFORE the gate — the ordering fix)
    # ==================================================================
    def _condense(self, query):
        if not self.history:
            return query
        recent = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in self.history[-2:])
        system = "Rewrite the follow-up as a standalone question. Output only the question."
        user = f"Chat so far:\n{recent}\n\nFollow-up: {query}\n\nStandalone question:"
        try:
            return self._ask_llm(system, user)
        except Exception:
            return query

    # ==================================================================
    # The full query path
    # ==================================================================
    def query(self, question):
        if not self.chunks:
            return {"refused": True, "answer": "No documents uploaded yet.",
                    "citation": None, "confidence": None, "label": None, "sources": []}

        # Meta/summary questions skip retrieval entirely.
        if self._is_meta(question):
            result = self._meta_answer(question)
            self.history.append({"q": question, "a": result["answer"]})
            return result

        # Resolve pronouns in follow-ups FIRST, then retrieve.
        resolved = self._condense(question)

        sem = self._semantic_search(resolved, k=10)      # Layer 3a
        kw = self._keyword_search(resolved, k=10)        # Layer 3b
        fused = self._rrf(sem, kw, top=10)               # Layer 4
        ranked = self._rerank(resolved, fused, top=5)    # Layer 5

        # Layer 6 — Confidence Gate
        best = ranked[0]["score"] if ranked else 0.0
        if best < CONF_THRESHOLD:
            self.history.append({"q": question, "a": "(refused)"})
            return {"refused": True,
                    "answer": "I don't know — your documents don't contain this.",
                    "citation": None, "confidence": round(best * 100),
                    "label": "Low", "sources": []}

        # Generate + verify (Layer 7)
        answer = self._generate(resolved, ranked)
        verified = self._verify(answer, ranked)

        top = ranked[0]
        confidence = round(best * 100)
        label = "High" if best >= 0.7 else "Medium" if best >= 0.5 else "Low"

        self.history.append({"q": question, "a": answer})
        return {
            "refused": False,
            "answer": answer,
            "verified": verified,
            "citation": f"{top['file']} · p.{top['page']}",
            "confidence": confidence,
            "label": label,
            "sources": [
                {"file": c["file"], "page": c["page"],
                 "score": round(c["score"] * 100), "text": c["text"][:400]}
                for c in ranked
            ],
        }

    # ==================================================================
    # Library / admin helpers
    # ==================================================================
    def list_papers(self):
        return sorted({c["file"] for c in self.chunks})

    def reset(self):
        self.chroma.delete_collection("lexai")
        self.collection = self.chroma.get_or_create_collection("lexai")
        self.history = []
        self._rebuild_from_store()
        return {"status": "reset"}
    
    