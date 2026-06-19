# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo layout

The actual project lives under `lexai/`. The repo root only has a stray empty `main.py` and the top-level `README.md` (which documents the `lexai` project — see "README vs. actual implementation" below).

```
lexai/
├── backend/
│   ├── api.py            FastAPI server — the real entry point
│   ├── lexai_engine.py   The entire RAG pipeline as one class (LexAIEngine)
│   ├── requirements.txt
│   ├── .env              NVIDIA_API_KEY etc. (gitignored)
│   └── chroma_store/      ChromaDB persistent data (gitignored, auto-created)
└── frontend/
    ├── src/App.jsx        Single-component React UI (upload + ask)
    └── ...                Vite + React 19, no router/state library
```

## Commands

Backend (run from `lexai/backend/`):
```bash
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Frontend (run from `lexai/frontend/`):
```bash
npm install
npm run dev       # Vite dev server, expects backend on http://localhost:8000
npm run build
npm run lint
```

There is no test suite in this repo currently. `ragas` is listed in `requirements.txt` for future offline faithfulness evaluation but no `evaluate.py` exists yet.

## Architecture

`lexai/backend/api.py` is a thin FastAPI wrapper (`/health`, `/upload`, `/query`, `/papers`, `/reset`) around a single `LexAIEngine` instance constructed once at startup. **All actual logic lives in `lexai/backend/lexai_engine.py`** — it is intentionally one readable file with no LlamaIndex, implementing each "layer" as a method:

1. **Ingestion** (`_extract_pdf` / `_extract_txt`, `_strip_references`) — PyMuPDF column-aware extraction; bibliography section is cut before chunking.
2. **Chunking** (`_chunk_page`) — sentence-based, ~400 words per chunk with ~40-word overlap (constants `CHUNK_WORDS` / `OVERLAP_WORDS`), tagged with `{file, page}` metadata.
3. **Hybrid retrieval** (`_semantic_search` + `_keyword_search`) — ChromaDB vector search (`bge-small-en-v1.5` via `sentence-transformers`) run alongside an in-memory BM25 index (`rank_bm25`), rebuilt from Chroma on every ingest/reset via `_rebuild_from_store` (Chroma is the single source of truth — `self.chunks` and the BM25 index are always derived from it, never the reverse).
4. **RRF fusion** (`_rrf`) — `1/(rank+60)` merge of the two ranked lists.
5. **Reranking** (`_rerank`) — `bge-reranker-base` cross-encoder; raw logits are passed through `_sigmoid` *before* threshold comparison (this ordering was previously a bug — keep it sigmoid-then-threshold).
6. **Confidence gate** (`query`, compares `best` against `CONF_THRESHOLD` = 0.30 from `.env`) — below threshold the query is refused rather than sent to the LLM.
7. **Generation** (`_generate`) via an OpenAI-compatible client pointed at NVIDIA NIM or Groq (`_PROVIDERS` dict, switched with `LLM_PROVIDER` env var — this is the one config switch for swapping LLM providers).
8. **Faithfulness check** (`_verify`) — a second LLM call that verdicts YES/NO on whether the answer is fully supported by the retrieved chunks; failures of the check itself default to "verified" rather than blocking the answer.

Two query paths exist, chosen by `_is_meta` (keyword sniffing for "summarise/overview/tldr/..."):
- **Meta path** (`_meta_answer`) skips retrieval entirely and samples first/middle/last chunk for an overview-style answer.
- **Lookup path** runs the full retrieval → RRF → rerank → gate → generate → verify pipeline.

Multi-turn follow-ups are condensed (`_condense`) into standalone questions using the last 2 turns of `self.history` *before* retrieval runs, so pronouns ("explain it more") resolve correctly against the vector store.

The frontend (`lexai/frontend/src/App.jsx`) is a single component with no routing: file upload posts to `/upload`, the question box posts to `/query`, and the response shape (`refused`, `answer`, `citation`, `confidence`, `label`, `verified`, `sources[]`) is rendered directly — keep `App.jsx` and the dict returned by `LexAIEngine.query()` in sync if changing either.

## README vs. actual implementation

The top-level `README.md` documents an earlier/aspirational design (`main.py` + `app.py` Streamlit UI + LlamaIndex orchestration + Supabase auth). The actual, current implementation in `lexai/backend/` and `lexai/frontend/` is a hand-rolled pipeline (no LlamaIndex) behind FastAPI with a React frontend — no Streamlit UI, no Supabase auth exist in the codebase. When the README and the code disagree, trust the code; the README's architecture diagrams and "why" rationale for each layer (ingestion, chunking, RRF, reranker/sigmoid, confidence gate, faithfulness check) still accurately describe the *intent* behind `lexai_engine.py`, just not the file/UI layout.
