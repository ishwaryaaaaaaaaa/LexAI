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

Every chunk is tagged with an owner_id at ingestion time, and every retrieval
path (semantic, keyword, meta) filters on it unconditionally — documents are
never visible across users. An optional allowed_files list narrows further
within that owner's own documents (Library's per-file/collection scope).

This file has NO web server. It's just a class the API calls.
"""

import os
import re
import math
import hashlib
import uuid
import time
from pathlib import Path

import pymupdf  # PyMuPDF
import numpy as np
import torch
import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder
from dotenv import load_dotenv

from llm_client import LLMClient

# Load .env that sits next to this file (explicit path = no surprises)
load_dotenv(Path(__file__).parent / ".env")

# ----------------------------------------------------------------------
# Config (pulled from .env, with sensible defaults)
# ----------------------------------------------------------------------
EMBED_MODEL    = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL   = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
CHROMA_DIR     = os.getenv("CHROMA_PERSIST_DIR", "./chroma_store")
CONF_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.30"))  # Layer 6 gate

# Chunking parameters (Layer 2)
CHUNK_WORDS   = 400
OVERLAP_WORDS = 40

# Provider config (base_url/model/api-key-env for NVIDIA and Groq, the
# LLM_PROVIDER/LLM_FALLBACK_PROVIDER switch, timeout + failover) now lives in
# llm_client.py's LLMClient, which this file delegates to via self.llm_client.


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

        # LLM client (generation + Layer 7): per-call timeout + NVIDIA -> Groq
        # failover, see llm_client.py. Logs its own "no API key" warning.
        self.llm_client = LLMClient()
        self.llm_model = self.llm_client.primary["model"] if self.llm_client.primary else None

        # In-memory state, rebuilt from Chroma so Chroma is the single source of truth.
        # self.chunks holds every owner's chunks; retrieval always filters by owner_id.
        self.chunks = []      # list of {"id", "text", "file", "page", "owner_id"}
        self.bm25 = None      # BM25 index (Layer 3 keyword side), built over all chunks
        self.history = {}     # owner_id -> list of {"q", "a"} turns, for follow-up condensing
        self._rebuild_from_store()

        total_papers = len({c["file"] for c in self.chunks})
        print(f"Engine ready. {total_papers} paper(s) total, {len(self.chunks)} chunks.")

    # ==================================================================
    # LAYER 1 — Smart Ingestion
    # ==================================================================
    def _extract_pdf(self, path):
        """Read a PDF in correct reading order, even for two-column layouts."""
        doc = pymupdf.open(path)
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
    def _chunk_page(self, page_text, file, page, owner_id):
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
        return [{"text": c, "file": file, "page": page, "owner_id": owner_id} for c in chunks]

    # ==================================================================
    # Ingest one document end-to-end (Layers 1 -> 2 -> store)
    # ==================================================================
    def add_document(self, path, filename, owner_id):
        ext = Path(filename).suffix.lower()
        if ext not in (".pdf", ".txt"):
            raise ValueError("Only .pdf and .txt are supported.")

        # De-duplicate on the exact file bytes, per owner. Chunks stored before
        # file_hash existed have no such metadata and are simply never matched.
        file_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        existing = self.collection.get(
            where={"$and": [{"owner_id": owner_id}, {"file_hash": file_hash}]},
            include=["metadatas"],
        )
        if existing["ids"]:
            # Report the name the content is already stored under, so a client
            # scoping queries to the returned filename finds those chunks.
            stored_name = existing["metadatas"][0].get("file", filename)
            return {"file": stored_name, "chunks": len(existing["ids"]), "duplicate": True}

        if ext == ".pdf":
            pages = self._extract_pdf(path)
        elif ext == ".txt":
            pages = self._extract_txt(path)
        else:
            raise ValueError("Only .pdf and .txt are supported.")

        new_chunks = []
        for pno, ptext in enumerate(pages, start=1):
            ptext = self._strip_references(ptext)
            new_chunks.extend(self._chunk_page(ptext, filename, pno, owner_id))

        if not new_chunks:
            return {"file": filename, "chunks": 0, "note": "No extractable text (scanned PDF?)."}

        # Embed and store in Chroma
        texts = [c["text"] for c in new_chunks]
        embeddings = self.embedder.encode(texts, normalize_embeddings=True).tolist()
        # Random ids: a count-based suffix could repeat an existing id after deletions.
        ids = [f"{filename}_{uuid.uuid4().hex}" for _ in new_chunks]
        metadatas = [{"file": c["file"], "page": c["page"], "owner_id": c["owner_id"],
                      "file_hash": file_hash}
                     for c in new_chunks]

        self.collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        self._rebuild_from_store()
        return {"file": filename, "chunks": len(new_chunks)}

    def _rebuild_from_store(self):
        """Reload all chunks (every owner) from Chroma and rebuild the BM25 index."""
        data = self.collection.get(include=["documents", "metadatas"])
        self.chunks = []
        for cid, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
            self.chunks.append({"id": cid, "text": doc,
                                "file": meta.get("file"), "page": meta.get("page"),
                                "owner_id": meta.get("owner_id")})
        if self.chunks:
            tokenized = [c["text"].lower().split() for c in self.chunks]
            self.bm25 = BM25Okapi(tokenized)
        else:
            self.bm25 = None

    # ==================================================================
    # LAYER 3 — Hybrid Retrieval (owner_id filtering is mandatory here)
    # ==================================================================
    def _semantic_search(self, query, owner_id, k=10, allowed_files=None):
        q_emb = self.embedder.encode(
            "Represent this sentence for searching relevant passages: " + query,
            normalize_embeddings=True,
        ).tolist()
        where = {"owner_id": owner_id}
        if allowed_files:
            where = {"$and": [{"owner_id": owner_id}, {"file": {"$in": allowed_files}}]}
        res = self.collection.query(query_embeddings=[q_emb], n_results=k, where=where)
        return res["ids"][0] if res["ids"] else []

    def _keyword_search(self, query, owner_id, k=10, allowed_files=None):
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(query.lower().split())
        order = np.argsort(scores)[::-1]
        ids = []
        for i in order:
            if scores[i] <= 0:
                break  # sorted descending, so nothing after this is usable
            c = self.chunks[i]
            if c["owner_id"] != owner_id:
                continue
            if allowed_files and c["file"] not in allowed_files:
                continue
            ids.append(c["id"])
            if len(ids) >= k:
                break
        return ids

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
    def _rerank(self, query, candidates, top=5, debug_info=None):
        if not candidates:
            if debug_info is not None:
                debug_info["reranker"] = {"top5_raw_logits": [], "top5_sigmoid_scores": [], "best_score": None}
            return []
        pairs = [[query, c["text"]] for c in candidates]
        # CrossEncoder.predict() applies nn.Sigmoid() by default for a num_labels=1
        # model like bge-reranker-base, so request the raw logit explicitly here —
        # otherwise _sigmoid() below double-sigmoids the score (confirmed 2026-10-14,
        # see PROJECT_LOG.md: this previously confined every score to ~[0.5, 0.731]).
        logits = self.reranker.predict(pairs, activation_fn=torch.nn.Identity())
        raw_by_id = {}
        for c, logit in zip(candidates, logits):
            raw = float(logit)
            c["score"] = _sigmoid(raw)  # normalize BEFORE the gate
            if debug_info is not None:
                raw_by_id[c["id"]] = raw
        ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)
        if debug_info is not None:
            top5 = ranked[:5]
            debug_info["reranker"] = {
                "top5_raw_logits": [raw_by_id[c["id"]] for c in top5],
                "top5_sigmoid_scores": [c["score"] for c in top5],
                "best_score": ranked[0]["score"] if ranked else None,
            }
        return ranked[:top]

    # ==================================================================
    # Generation
    # ==================================================================
    def _ask_llm(self, system, user, purpose=None, calls_log=None):
        start_idx = len(self.llm_client.attempts)
        content = self.llm_client.complete(system, user, purpose=purpose, max_tokens=2048)
        if calls_log is not None:
            calls_log.extend(self.llm_client.attempts[start_idx:])
        return content

    def _generate(self, query, chunks, calls_log=None):
        context = "\n\n".join(
            f"[{c['file']} p.{c['page']}] {c['text']}" for c in chunks
        )
        system = (
            "You are LexAI, a document QA assistant. Answer the question using ONLY "
            "the exact content of the provided sources.\n\n"
            "Rules you must follow without exception:\n"
            "1. Use ONLY information explicitly stated in the sources. "
            "Do NOT infer, extrapolate, or use any outside knowledge.\n"
            "2. If the sources do not contain enough information to answer, respond with exactly: "
            "'The provided documents do not contain this information.'\n"
            "3. Be concise and direct — answer in one to three sentences.\n"
            "4. Do NOT add disclaimers, caveats, or filler beyond what the sources say."
        )
        user = f"Sources:\n{context}\n\nQuestion: {query}\n\nAnswer:"
        return self._ask_llm(system, user, purpose="generate", calls_log=calls_log)

    # ==================================================================
    # LAYER 7 — Faithfulness Verification
    # ==================================================================
    def _verify(self, answer, chunks, calls_log=None, debug_out=None):
        context = "\n\n".join(c["text"] for c in chunks)
        system = "You check if an answer is fully supported by the sources. Reply only YES or NO."
        user = f"Sources:\n{context}\n\nAnswer:\n{answer}\n\nIs every claim supported? YES or NO:"
        try:
            raw = self._ask_llm(system, user, purpose="verify", calls_log=calls_log)
        except Exception as e:
            # The check never ran, so never claim it passed. The answer itself is
            # still returned (not blocked), just with verified=False.
            if debug_out is not None:
                debug_out["verdict_text"] = None
                debug_out["status"] = "check_could_not_run"
                debug_out["result"] = False
                debug_out["exception"] = f"{type(e).__name__}: {e}"
            return False
        # Leading punctuation/markdown (e.g. "**YES**") is ignored; anything that
        # isn't clearly YES or NO counts as not verified.
        m = re.match(r"\W*(YES|NO)\b", raw.upper())
        status = {"YES": "supported", "NO": "not_supported"}.get(m.group(1)) if m else "ambiguous"
        result = status == "supported"
        if debug_out is not None:
            debug_out["verdict_text"] = raw
            debug_out["status"] = status
            debug_out["result"] = result
            debug_out["exception"] = None
        return result

    # ==================================================================
    # Intent router + meta path (summaries bypass retrieval, NOT owner filtering)
    # ==================================================================
    def _is_meta(self, query):
        q = query.lower()
        return any(w in q for w in ["summar", "overview", "what is this", "what's this",
                                    "tldr", "main points", "about this"])

    def _meta_answer(self, query, owner_id, allowed_files=None, calls_log=None):
        owner_chunks = [c for c in self.chunks if c["owner_id"] == owner_id
                        and (not allowed_files or c["file"] in allowed_files)]
        if not owner_chunks:
            return None
        n = len(owner_chunks)
        sample = [owner_chunks[0], owner_chunks[n // 2], owner_chunks[-1]]
        answer = self._generate(query, sample, calls_log=calls_log)
        return {
            "refused": False, "meta": True, "answer": answer,
            "citation": None, "confidence": None, "label": None, "sources": [],
        }

    # ==================================================================
    # Follow-up condensing (runs BEFORE the gate — the ordering fix)
    # ==================================================================
    def _condense(self, query, owner_id, calls_log=None):
        history = self.history.get(owner_id, [])
        if not history:
            return query
        recent = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in history[-2:])
        system = "Rewrite the follow-up as a standalone question. Output only the question."
        user = f"Chat so far:\n{recent}\n\nFollow-up: {query}\n\nStandalone question:"
        try:
            return self._ask_llm(system, user, purpose="condense", calls_log=calls_log)
        except Exception:
            return query

    # ==================================================================
    # The full query path
    # ==================================================================
    def query(self, question, owner_id, allowed_files=None, debug=False):
        dbg = {"stages_ms": {}, "llm_calls": [], "counts": {}} if debug else None
        t_total = time.perf_counter() if debug else None

        if not self.list_papers(owner_id):
            result = {"refused": True, "answer": "No documents uploaded yet.",
                    "citation": None, "confidence": None, "label": None, "sources": []}
            if debug:
                dbg["refusal_path"] = "refused=True: no documents uploaded for this owner_id (list_papers empty)"
                dbg["stages_ms"]["total"] = (time.perf_counter() - t_total) * 1000
                result["debug"] = dbg
            return result

        # Meta/summary questions skip retrieval entirely, but still stay owner-scoped.
        if self._is_meta(question):
            t0 = time.perf_counter() if debug else None
            result = self._meta_answer(question, owner_id, allowed_files,
                                        calls_log=(dbg["llm_calls"] if debug else None))
            if result is None:
                result = {"refused": True, "answer": "No documents uploaded yet.",
                          "citation": None, "confidence": None, "label": None, "sources": []}
                refusal_note = "refused=True: meta path matched but owner has no chunks (allowed_files filter or no uploads)"
            else:
                refusal_note = "refused=False: meta path (_is_meta matched) bypasses the confidence gate entirely"
            self.history.setdefault(owner_id, []).append({"q": question, "a": result["answer"]})
            if debug:
                dbg["stages_ms"]["meta_generate"] = (time.perf_counter() - t0) * 1000
                dbg["refusal_path"] = refusal_note
                dbg["stages_ms"]["total"] = (time.perf_counter() - t_total) * 1000
                result["debug"] = dbg
            return result

        # Resolve pronouns in follow-ups FIRST, then retrieve.
        t0 = time.perf_counter() if debug else None
        resolved = self._condense(question, owner_id, calls_log=(dbg["llm_calls"] if debug else None))
        if debug:
            dbg["stages_ms"]["condense"] = (time.perf_counter() - t0) * 1000
            dbg["condensed_question"] = resolved

        t0 = time.perf_counter() if debug else None
        sem = self._semantic_search(resolved, owner_id, k=15, allowed_files=allowed_files)   # Layer 3a
        if debug:
            dbg["stages_ms"]["semantic_retrieval"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter() if debug else None
        kw = self._keyword_search(resolved, owner_id, k=15, allowed_files=allowed_files)     # Layer 3b
        if debug:
            dbg["stages_ms"]["bm25_retrieval"] = (time.perf_counter() - t0) * 1000
            dbg["counts"]["from_semantic"] = len(sem)
            dbg["counts"]["from_bm25"] = len(kw)

        t0 = time.perf_counter() if debug else None
        fused = self._rrf(sem, kw, top=15)               # Layer 4
        if debug:
            dbg["stages_ms"]["rrf"] = (time.perf_counter() - t0) * 1000
            dbg["counts"]["after_rrf"] = len(fused)
            dbg["counts"]["sent_to_reranker"] = len(fused)
            fused_texts = [c["text"] for c in fused]
            dbg["duplicate_chunk_texts_in_candidates"] = len(fused_texts) != len(set(fused_texts))

        t0 = time.perf_counter() if debug else None
        rerank_debug = {} if debug else None
        ranked = self._rerank(resolved, fused, top=5, debug_info=rerank_debug)    # Layer 5
        if debug:
            dbg["stages_ms"]["rerank"] = (time.perf_counter() - t0) * 1000
            dbg.update(rerank_debug)
            ranked_texts = [c["text"] for c in ranked]
            dbg["duplicate_chunk_texts_in_top5"] = len(ranked_texts) != len(set(ranked_texts))

        # Layer 6 — Confidence Gate
        t0 = time.perf_counter() if debug else None
        best = ranked[0]["score"] if ranked else 0.0
        gate_refuses = best < CONF_THRESHOLD
        if debug:
            dbg["stages_ms"]["gate"] = (time.perf_counter() - t0) * 1000
            dbg["gate"] = {
                "value_compared": best,
                "threshold": CONF_THRESHOLD,
                "comparison": f"best ({best}) < threshold ({CONF_THRESHOLD}) -> {gate_refuses}",
                "refuses": gate_refuses,
                "note": "value_compared (best) is the exact same number used to compute the returned confidence field",
            }
            dbg["confidence_inputs"] = {
                "ranked_nonempty": bool(ranked),
                "top_chunk_sigmoid_score": ranked[0]["score"] if ranked else None,
                "fallback_value_if_ranked_empty": 0.0,
                "best_value_used": best,
                "formula": "confidence = round(best * 100)",
            }

        if gate_refuses:
            self.history.setdefault(owner_id, []).append({"q": question, "a": "(refused)"})
            result = {"refused": True,
                    "answer": "I don't know — your documents don't contain this.",
                    "citation": None, "confidence": round(best * 100),
                    "label": "Low", "sources": []}
            if debug:
                dbg["refusal_path"] = "refused=True: confidence gate branch (best < CONF_THRESHOLD)"
                dbg["confidence_inputs"]["final_confidence"] = result["confidence"]
                dbg["stages_ms"]["total"] = (time.perf_counter() - t_total) * 1000
                result["debug"] = dbg
            return result

        # Drop chunks that scored too low to be useful — improves context precision.
        MIN_CHUNK_SCORE = 0.25
        grounded = [c for c in ranked if c["score"] >= MIN_CHUNK_SCORE] or ranked[:1]
        if debug:
            dbg["counts"]["grounded_chunks"] = len(grounded)
            grounded_texts = [c["text"] for c in grounded]
            dbg["duplicate_chunk_texts_in_grounded"] = len(grounded_texts) != len(set(grounded_texts))

        # Generate + verify (Layer 7)
        t0 = time.perf_counter() if debug else None
        answer = self._generate(resolved, grounded, calls_log=(dbg["llm_calls"] if debug else None))
        if debug:
            dbg["stages_ms"]["generate"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter() if debug else None
        verify_debug = {} if debug else None
        verified = self._verify(answer, grounded, calls_log=(dbg["llm_calls"] if debug else None),
                                 debug_out=verify_debug)
        if debug:
            dbg["stages_ms"]["verify"] = (time.perf_counter() - t0) * 1000
            dbg["verification"] = {
                "verdict_text": verify_debug.get("verdict_text"),
                "status": verify_debug.get("status"),
                "check_ran": verify_debug.get("status") != "check_could_not_run",
                "note": ("the verification check could not run (verify LLM call failed); "
                         "verified is False because nothing was checked"
                         if verify_debug.get("status") == "check_could_not_run" else None),
                "verify_call_exception": verify_debug.get("exception"),
                "result": verified,
                "effect_on_answer": ("verified flag is returned alongside the answer; "
                                     "the answer text itself is never edited, blocked, or regenerated based on this check"),
            }

        top = grounded[0]
        confidence = round(best * 100)
        label = "High" if best >= 0.7 else "Medium" if best >= 0.5 else "Low"

        self.history.setdefault(owner_id, []).append({"q": question, "a": answer})
        result = {
            "refused": False,
            "answer": answer,
            "verified": verified,
            "citation": f"{top['file']} · p.{top['page']}",
            "confidence": confidence,
            "label": label,
            "sources": [
                {"file": c["file"], "page": c["page"],
                 "score": round(c["score"] * 100), "text": c["text"][:400]}
                for c in grounded
            ],
        }
        if debug:
            dbg["refusal_path"] = "refused=False: gate passed (best >= CONF_THRESHOLD)"
            dbg["confidence_inputs"]["final_confidence"] = confidence
            dbg["stages_ms"]["total"] = (time.perf_counter() - t_total) * 1000
            result["debug"] = dbg
        return result

    # ==================================================================
    # Library / admin helpers
    # ==================================================================
    def list_papers(self, owner_id):
        return sorted({c["file"] for c in self.chunks if c["owner_id"] == owner_id})

    def delete_document(self, owner_id, filename):
        """Delete one owner's chunks for one filename; returns how many were deleted.
        Other owners' files with the same name are untouched. Chat history is left alone."""
        where = {"$and": [{"owner_id": owner_id}, {"file": filename}]}
        n = len(self.collection.get(where=where, include=["metadatas"])["ids"])
        if n:
            self.collection.delete(where=where)
            self._rebuild_from_store()
        return n

    def reset(self, owner_id=None):
        """Wipe everything (owner_id=None) or just one owner's documents + history."""
        if owner_id is None:
            self.chroma.delete_collection("lexai")
            self.collection = self.chroma.get_or_create_collection("lexai")
            self.history = {}
        else:
            existing = self.collection.get(where={"owner_id": owner_id})
            if existing["ids"]:
                self.collection.delete(ids=existing["ids"])
            self.history.pop(owner_id, None)
        self._rebuild_from_store()
        return {"status": "reset"}


