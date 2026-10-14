# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Every statement below was checked against the current code on 2026-10-14. Anything that couldn't be
checked by reading code is marked "unverified".

## Repo layout

```
D:\LexAI/
├── .gitignore
├── CLAUDE.md
├── PROJECT_LOG.md        Append-only work log. Read it before starting work; add an entry when you finish.
├── README.md             Mostly accurate project writeup; see "README vs. actual implementation" below.
├── package.json           Stray — only dependency is @supabase/supabase-js, added by the "added
├── package-lock.json       authentication" commit. No src/node_modules at root. Looks like `npm install`
│                           was accidentally run from the repo root instead of lexai/frontend/. Harmless
│                           but not part of either app; nothing at root requires it.
└── lexai/
    ├── backend/
    │   ├── api.py              FastAPI server — the real entry point (/health, /upload, /query, /papers, /reset)
    │   ├── lexai_engine.py      The entire RAG pipeline as one class (LexAIEngine)
    │   ├── Evaluvate.py         RAGAS evaluation script — note the spelling, that's the actual filename
    │   ├── requirements.txt     Curated/aspirational; see "requirements.txt" note below
    │   ├── .env                 NVIDIA_API_KEY etc. (gitignored) — never read or print this file
    │   ├── .env.example         Template with the real var names (filled in 2026-10-14)
    │   ├── smoke_test.py        Standalone API smoke test (upload + answerable/unanswerable query)
    │   ├── ragas_results.csv    Per-question output of the last Evaluvate.py run
    │   ├── chroma_store/        ChromaDB persistent data (gitignored, auto-created)
    │   ├── chroma_store.bak_20260625_151714/   A manual backup folder. 4 of its files are tracked in
    │   │                         git — the gitignore pattern chroma_store*/ (added 2026-10-14) covers new
    │   │                         commits but does NOT untrack what's already tracked; untracking it needs
    │   │                         an explicit `git rm --cached`, left for a manual decision.
    │   ├── scratch/              One-off dev scripts, moved out of the backend root on 2026-10-14:
    │   │                         model_test.py, model_test2.py, model_probe.py, make_test_pdf.py, test_doc.pdf
    │   ├── *.bak                 Evaluvate.py.bak, lexai_engine.py.bak — manual backups, left in place
    │   └── mcp_server/           Separate MCP server exposing the backend as 3 Claude Desktop tools
    │       ├── server.py         Thin HTTP proxy to api.py (ask_lexai, upload_to_lexai, list_lexai_documents)
    │       ├── .env / .env.example   LEXAI_BACKEND_URL, LEXAI_OWNER_ID (single fixed owner, no per-call auth)
    │       └── README.md         Setup + Claude Desktop config instructions
    └── frontend/
        ├── src/
        │   ├── App.jsx            Top-level component — session/profile gate, chat, upload, scope selector
        │   ├── SignIn.jsx          Google OAuth sign-in via Supabase (institutional SSO button is disabled/unverified)
        │   ├── Onboarding.jsx      Collects preferred_name / birthday / purpose, upserts into Supabase `profiles`
        │   ├── Warning.jsx         Disclaimer screen ("text only", "no outside knowledge"); sets `accepted_warning`
        │   ├── Library.jsx         File + collection manager (list/delete files, create/delete collections,
        │   │                       assign files to collections, pick a query scope)
        │   ├── libraryApi.js       All Supabase calls for Library (files, collections, file_collections tables)
        │   ├── supabaseClient.js   One shared Supabase client (reads VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY)
        │   └── main.jsx            Entry point
        └── ...                    Vite + React 19, no router — App.jsx gates views by hand (session → profile →
                                     warning → chat/library)
```

## Commands

Backend (run from `lexai/backend/`; the project already has a `.venv` with Python 3.11):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Smoke test (needs the backend already running, and a PDF to upload):

```powershell
.\.venv\Scripts\python.exe smoke_test.py --pdf path\to\file.pdf `
    --q-in "a question the PDF answers" --q-out "a question it cannot answer"
# optional: --base http://127.0.0.1:8000 --owner some-owner-id
```

RAGAS evaluation (needs the backend already running, at least one doc uploaded, test set edited in
Evaluvate.py to match that doc):

```powershell
.\.venv\Scripts\python.exe Evaluvate.py
```

Frontend (run from `lexai/frontend/`):

```bash
npm install
npm run dev       # Vite dev server, expects backend on http://localhost:8000
npm run build
npm run lint
```

Frontend also needs its own `.env` with `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (read by
`supabaseClient.js`); there's no committed `.env.example` for the frontend — unverified whether one
exists outside this repo.

There is no Python test suite. `ragas` in requirements.txt is for `Evaluvate.py` (see above), which
exists and runs against the live backend — this corrects the previous version of this file, which said
no such script existed.

## Architecture

`lexai/backend/api.py` is a thin FastAPI wrapper around a single `LexAIEngine` instance constructed once
at startup. **All actual logic lives in `lexai/backend/lexai_engine.py`** — it is intentionally one
readable file with no LlamaIndex, implementing each "layer" as a method, even though `requirements.txt`
still lists `llama-index-*` packages and they are installed in `.venv` — they are not imported anywhere
in the backend code. The `supabase` Python package is in the same situation: installed, listed in
requirements.txt, never imported by `api.py` or `lexai_engine.py`. All Supabase use is client-side, from
the frontend, via `supabaseClient.js`.

1. **Ingestion** (`_extract_pdf` / `_extract_txt`, `_strip_references`) — PyMuPDF column-aware extraction;
   bibliography section is cut before chunking. As of 2026-10-14 the module is imported as `import pymupdf`
   (not `import fitz`) and all calls use `pymupdf.open(...)`; the installed package's name is `pymupdf`
   (version 1.28.2 in the backend `.venv`), which still exposes a `fitz` compatibility alias, but the code
   no longer uses it.
2. **Chunking** (`_chunk_page`) — sentence-based, ~400 words per chunk with ~40-word overlap (constants
   `CHUNK_WORDS` / `OVERLAP_WORDS`), tagged with `{file, page, owner_id}` metadata.
3. **Hybrid retrieval** (`_semantic_search` + `_keyword_search`) — ChromaDB vector search (`bge-small-en-v1.5`
   via `sentence-transformers`) run alongside an in-memory BM25 index (`rank_bm25`), rebuilt from Chroma on
   every ingest/reset via `_rebuild_from_store` (Chroma is the single source of truth — `self.chunks` and
   the BM25 index are always derived from it, never the reverse). Every chunk carries an `owner_id`, and
   both retrieval paths filter on it unconditionally, plus an optional `allowed_files` list for Library's
   per-file/collection scoping.
4. **RRF fusion** (`_rrf`) — `1/(rank+60)` merge of the two ranked lists.
5. **Reranking** (`_rerank`) — `bge-reranker-base` cross-encoder; raw logits are passed through `_sigmoid`
   *before* threshold comparison (this ordering was previously a bug — keep it sigmoid-then-threshold).
6. **Confidence gate** (`query`, compares `best` against `CONF_THRESHOLD` = 0.30 from `.env`) — below
   threshold the query is refused rather than sent to the LLM.
7. **Generation** (`_generate` → `_ask_llm`) via an OpenAI-compatible client pointed at NVIDIA NIM or Groq
   (`_PROVIDERS` dict, switched with `LLM_PROVIDER`). The current model (both providers' entries live in
   `_PROVIDERS` in `lexai_engine.py`) is **`openai/gpt-oss-20b`** on the `nvidia` provider — it replaced
   `meta/llama-3.1-8b-instruct` after that model returned `410 Gone` (NVIDIA end-of-lifed it 2026-08-26;
   see PROJECT_LOG.md). `gpt-oss-20b` is a reasoning model: `_ask_llm` uses `max_tokens=2048` (a small
   budget makes reasoning models return empty content) and guards against a `None` `.content` with
   `(resp.choices[0].message.content or "")`. An optional `LLM_MODEL` env var (added 2026-10-14) overrides
   the selected provider's default model; unset, behavior is unchanged. The `groq` provider entry
   (`llama-3.1-8b-instant`) exists in `_PROVIDERS` but is **untested** — nobody has run a query through it.
8. **Faithfulness check** (`_verify`) — a second LLM call that verdicts YES/NO on whether the answer is
   fully supported by the retrieved chunks; failures of the check itself default to "verified" rather than
   blocking the answer.

Two query paths exist, chosen by `_is_meta` (keyword sniffing for "summarise/overview/tldr/..."):
- **Meta path** (`_meta_answer`) skips retrieval entirely and samples first/middle/last chunk for an
  overview-style answer.
- **Lookup path** runs the full retrieval → RRF → rerank → gate → generate → verify pipeline.

Multi-turn follow-ups are condensed (`_condense`) into standalone questions using the last 2 turns of
`self.history` *before* retrieval runs, so pronouns ("explain it more") resolve correctly against the
vector store.

**Auth / owner_id:** the frontend signs users in with Supabase (Google OAuth only, via `SignIn.jsx`), and
everything about that identity — the profile, the disclaimer-acceptance flag, the file/collection
library — lives in Supabase tables (`profiles`, `files`, `collections`, `file_collections`), read and
written directly from the frontend (`libraryApi.js`), never through the backend. `App.jsx` then sends
`session.user.id` as a plain `owner_id` field on `/upload` and `/query` requests. **`api.py` does not
verify a JWT or any other credential** — it trusts whatever `owner_id` string the client sends, verbatim
(see the `TODO` comment above the `/upload` handler). Anyone who can call the API directly can read or
write any owner's documents by supplying their `owner_id`. This is also why the MCP server
(`mcp_server/server.py`) can get away with one fixed `LEXAI_OWNER_ID` for all calls — there's no server-
side check that would reject it.

The frontend's view stack, gated by `App.jsx` with no router: `loading` → `!session` → `SignIn` →
`!profile` → `Onboarding` → `!profile.accepted_warning` → `Warning` → `view === "library"` → `Library` →
otherwise the chat view (upload + ask + answer/sources). Keep `App.jsx` and the dict returned by
`LexAIEngine.query()` in sync if changing either — the UI renders `refused`, `answer`, `citation`,
`confidence`, `label`, `verified`, `sources[]` directly.

Real backend env vars (see `lexai/backend/.env.example`, filled in 2026-10-14): `LLM_PROVIDER`,
`NVIDIA_API_KEY`, `GROQ_API_KEY`, `LLM_MODEL` (optional override, new), `EMBED_MODEL`, `RERANK_MODEL`,
`CHROMA_PERSIST_DIR`, `CONFIDENCE_THRESHOLD`, `FRONTEND_ORIGIN`. Frontend needs `VITE_SUPABASE_URL` and
`VITE_SUPABASE_ANON_KEY` in its own `.env` (not covered by the backend `.env.example`). `mcp_server/` has
its own separate `.env` / `.env.example` with `LEXAI_BACKEND_URL` and `LEXAI_OWNER_ID`.

## README vs. actual implementation

The top-level `README.md` is a close match to the actual architecture — FastAPI + React + ChromaDB + BM25
+ RRF + reranker + confidence gate + faithfulness check, no LlamaIndex, no Streamlit, no `main.py`/`app.py`
(the previous version of this file claimed the README described an aspirational Streamlit/LlamaIndex/
`main.py` design and that a stray empty `main.py` existed at repo root; neither is true of the current
README or repo). Known-stale details in README.md, as of 2026-10-14:

- The NVIDIA NIM badge and the architecture-diagram/Stack-table "LLM" rows still say "LLaMA 3.1" /
  `LLaMA 3.1-8b-instruct` — the actual model is `openai/gpt-oss-20b` (see Architecture above).
- "RAGAS Evaluation" section states the judge LLM as `meta/llama-3.1-8b-instruct`; the judge in
  `Evaluvate.py` is now `openai/gpt-oss-20b`. The four printed scores (faithfulness 0.5476, answer
  relevancy 0.6770, context precision 0.3679, context recall 0.4667) were measured with the old judge and
  have **not** been re-run with the new one — don't quote them as current without re-running `Evaluvate.py`.
- "Project Structure" only lists `App.jsx`, `main.jsx`, `index.css` under `frontend/src/` — it's missing
  `SignIn.jsx`, `Onboarding.jsx`, `Warning.jsx`, `Library.jsx`, `libraryApi.js`, `supabaseClient.js`, and
  their `.css` files. It also doesn't mention `PROJECT_LOG.md`, `lexai/backend/scratch/`, the `.bak` files,
  or the stray root `package.json`/`package-lock.json`.
- The `mcp_server` Claude Desktop config example path (`C:\dev\new_rag\...`) is a stale dev-machine path,
  not this repo's location.
- Doesn't document that `api.py` has no JWT verification, or the confidence-gate issue below.

## Known issues

- **Confidence gate doesn't always refuse off-topic queries.** Per the 2026-10-13 smoke test
  (PROJECT_LOG.md), an unanswerable question got `confidence: 50, refused: false, sources: 1`, even though
  the LLM's own answer text said the documents didn't contain it. A confidence of exactly 50 looks like a
  fallback/blended value rather than a raw reranker score — not yet diagnosed, root cause unknown.
- **First-query latency.** The same smoke test's first query took ~106s (cold start — model loading),
  versus ~20s for the second query. Unverified whether this is fully mitigated by anything short of a
  warm-up request at startup.
- **`chroma_store.bak_20260625_151714/` is partially tracked in git** (4 files — vector index binaries; the
  SQLite file with the actual chunk text is gitignored via `*.sqlite3`). The 2026-10-14 `.gitignore`
  addition (`chroma_store*/`) stops new backups like this from being tracked, but does not retroactively
  untrack this one.
- **`lexai/backend/.env.example` was empty** until 2026-10-14; it's now filled in from the code's
  `os.getenv` calls (see Architecture above).
- **No backend JWT verification** — see "Auth / owner_id" above. Anyone with API access and a guessed/
  known `owner_id` can read or write that owner's documents.
- **Groq provider untested** — the `_PROVIDERS["groq"]` entry has never been exercised end-to-end.
- **`requirements.txt` lists unused packages** — `llama-index-*` and `supabase` are installed in `.venv`
  per `requirements.txt` but not imported anywhere in `api.py` or `lexai_engine.py`. Unverified whether
  this is intentional (future use) or leftover from an earlier design.

## Working agreements

- Working branch: `sriharinesh_local`.
- The agent (Claude Code) never runs `git add`, `git commit`, or `git push` in this repo — the human
  commits manually after reviewing.
- Every task session ends with a new, append-only entry in `PROJECT_LOG.md` (never edit or delete earlier
  entries; no commit hashes — `git log` is the source of truth for those).
- Never read, print, or open any `.env` file (any directory — backend, frontend, or `mcp_server`).
- Any RAGAS number quoted anywhere (docs, chat, commit messages) must state its judge model, because the
  judge has changed at least once already and old numbers silently go stale otherwise.
- Documentation in this repo (this file, PROJECT_LOG.md, README.md) must stay factually true to the code;
  prefer marking something "unverified" over asserting it from a prompt, memory, or an old doc.
