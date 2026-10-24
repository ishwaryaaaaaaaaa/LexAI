# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Every statement below was checked against the current code on 2026-10-24 (previous full check
2026-10-14). Anything that couldn't be checked by reading code is marked "unverified".

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
    │   ├── lexai_engine.py      The entire RAG pipeline as one class (LexAIEngine). Saved as UTF-8 with BOM.
    │   ├── llm_client.py        LLMClient: per-call timeout, primary -> fallback failover, circuit breaker,
    │   │                         LLMUnavailableError. No model loading, so importable/testable offline.
    │   ├── Evaluvate.py         RAGAS evaluation script — note the spelling, that's the actual filename
    │   ├── requirements.txt     Curated/aspirational; see "requirements.txt" note below
    │   ├── .env                 API keys etc. (gitignored) — never read or print this file
    │   ├── .env.example         Template with the real var names (does not yet list LLM_BREAKER_SECONDS)
    │   ├── smoke_test.py        Standalone API smoke test (upload + answerable/unanswerable query, --debug)
    │   ├── ragas_results.csv    Per-question output of the last Evaluvate.py run
    │   ├── chroma_store/        ChromaDB persistent data (gitignored, auto-created)
    │   ├── chroma_store.bak_20260625_151714/   A manual backup folder, now untracked and gitignored
    │   │                         (chroma_store*/); still present on disk.
    │   ├── scratch/              One-off dev scripts and offline tests (whole folder is gitignored, so new
    │   │                         files here never show in `git status`): llm_fallback_test.py,
    │   │                         p6_engine_test.py, rerank_check.py, model_test*.py, model_probe.py,
    │   │                         make_test_pdf.py, test_doc.pdf, baseline_hashes*.txt
    │   ├── *.bak                 Evaluvate.py.bak, lexai_engine.py.bak — manual backups (gitignored), left in place
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
# optional: --debug   sends debug=true on /query and pretty-prints the returned "debug" block
```

Offline tests (no server, no network, no real LLM calls; run from `lexai/backend/`):

```powershell
.\.venv\Scripts\python.exe scratch\llm_fallback_test.py   # LLMClient failover + circuit breaker (fakes only)
.\.venv\Scripts\python.exe scratch\p6_engine_test.py      # verify fix + upload dedupe; loads the cached
                                                          # embedder/reranker on CPU, uses a temp Chroma dir
                                                          # under scratch/ and deletes it afterwards
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

There is no formal Python test suite (no pytest); the two scratch scripts above are plain PASS/FAIL
scripts. `ragas` in requirements.txt is for `Evaluvate.py`, which runs against the live backend.

## Architecture

`lexai/backend/api.py` is a thin FastAPI wrapper around a single `LexAIEngine` instance constructed once
at startup. **All retrieval/generation logic lives in `lexai/backend/lexai_engine.py`**, which delegates
every LLM call to `LLMClient` in `lexai/backend/llm_client.py`. The engine is intentionally one readable
file with no LlamaIndex, implementing each "layer" as a method, even though `requirements.txt` still lists
`llama-index-*` packages — they are not imported anywhere in the backend code. The `supabase` Python
package is in the same situation: listed in requirements.txt, never imported by `api.py` or
`lexai_engine.py`. All Supabase use is client-side, from the frontend, via `supabaseClient.js`.

1. **Ingestion** (`add_document` → `_extract_pdf` / `_extract_txt`, `_strip_references`) — PyMuPDF
   column-aware extraction (imported as `import pymupdf`); bibliography section is cut before chunking.
   **Upload dedupe:** before extracting, `add_document` computes SHA-256 of the uploaded file's bytes and
   looks up existing Chroma chunks with the same `owner_id` and `file_hash`. If any exist, nothing is
   ingested and it returns `{"file": <the filename those chunks are stored under>, "chunks": <existing
   count>, "duplicate": true}`. A normal ingest returns `{"file", "chunks"}` as before (no `duplicate` key).
   Dedupe is per owner and per exact bytes: different bytes under the same filename are ingested normally
   (alongside the old chunks — nothing is replaced), and chunks stored before `file_hash` existed have no
   such metadata and are never matched. Existing data was not migrated.
2. **Chunking** (`_chunk_page`) — sentence-based, ~400 words per chunk with ~40-word overlap (constants
   `CHUNK_WORDS` / `OVERLAP_WORDS`). Chunk metadata is `{file, page, owner_id, file_hash}`; chunk ids are
   `f"{filename}_{collection.count() + i}"`.
3. **Hybrid retrieval** (`_semantic_search` + `_keyword_search`) — ChromaDB vector search (`bge-small-en-v1.5`
   via `sentence-transformers`) run alongside an in-memory BM25 index (`rank_bm25`), rebuilt from Chroma on
   every ingest/reset via `_rebuild_from_store` (Chroma is the single source of truth — `self.chunks` and
   the BM25 index are always derived from it, never the reverse; a duplicate upload changes neither).
   Every chunk carries an `owner_id`, and both retrieval paths filter on it unconditionally, plus an
   optional `allowed_files` list for Library's per-file/collection scoping.
4. **RRF fusion** (`_rrf`) — `1/(rank+60)` merge of the two ranked lists.
5. **Reranking** (`_rerank`) — `bge-reranker-base` cross-encoder. `CrossEncoder.predict()` applies a
   sigmoid by default for this num_labels=1 model, so the code calls
   `predict(pairs, activation_fn=torch.nn.Identity())` to get the raw logit and then applies `_sigmoid`
   **exactly once** before the threshold comparison. (Before 2026-10-14 the score was double-sigmoided,
   which confined every score to ~[0.5, 0.731] and made the gate unable to refuse — see PROJECT_LOG.md.
   Confidence numbers from before that fix are not comparable to current ones.)
6. **Confidence gate** (`query`, compares `best` against `CONF_THRESHOLD`, from env `CONFIDENCE_THRESHOLD`,
   default 0.30) — below threshold the query is refused rather than sent to the LLM. **0.30 is
   uncalibrated**: it predates the single-sigmoid fix and has never been tuned on labeled
   relevant/irrelevant queries against the current scoring.
7. **Generation** (`_generate` → `_ask_llm` → `LLMClient.complete`) — see "LLM client" below. Default model
   on both providers is `openai/gpt-oss-20b`, a reasoning model: `max_tokens=2048` per call (a small budget
   makes reasoning models return empty content) and a `None` `.content` becomes `""`.
8. **Faithfulness check** (`_verify`) — a second LLM call asked to reply YES or NO on whether the answer
   is fully supported by the retrieved chunks. The verdict is parsed with `re.match(r"\W*(YES|NO)\b", ...)`
   on the upper-cased text (leading punctuation/markdown ignored). `verified` is `True` **only** for a
   clear YES: a NO, an ambiguous/empty verdict, or **any exception from the verify call (including
   `LLMUnavailableError`)** gives `verified: false`. The answer text is never edited or blocked by this
   check, and a verify failure does not turn into a 503. With `debug=true`, `debug.verification` carries
   `status` (`supported` / `not_supported` / `ambiguous` / `check_could_not_run`), `check_ran`, a `note`
   saying the check could not run when that happened, `verdict_text`, and `verify_call_exception`.

Two query paths exist, chosen by `_is_meta` (keyword sniffing for "summarise/overview/tldr/..."):
- **Meta path** (`_meta_answer`) skips retrieval and the gate, samples first/middle/last chunk for an
  overview-style answer, and makes one LLM call (no verify, no `verified` key in the response).
- **Lookup path** runs the full retrieval → RRF → rerank → gate → generate → verify pipeline.

Multi-turn follow-ups are condensed (`_condense`) into standalone questions using the last 2 turns of
`self.history[owner_id]` *before* retrieval runs. `self.history` is an in-memory dict on the single engine
instance: it persists across requests for the life of the server process (shared by everyone sending
that `owner_id`), is lost on restart, is never trimmed, and is cleared by `/reset`.

### LLM client (`llm_client.py`)

- **Primary** provider = `LLM_PROVIDER` (default `nvidia`), model overridable by `LLM_MODEL`.
  **Fallback** provider = `LLM_FALLBACK_PROVIDER` (default `groq`; empty string disables it), model
  overridable by `LLM_FALLBACK_MODEL`. The fallback is only used if its API key env var
  (`NVIDIA_API_KEY` / `GROQ_API_KEY`) is non-empty. Provider table (`_PROVIDERS`) lives only in this file.
- Each OpenAI-compatible client is built with `timeout=LLM_TIMEOUT_S` (default 30s, float) and
  `max_retries=0`.
- On a primary timeout, connection error, 429 or other non-400 `APIStatusError` (e.g. 5xx), the same call
  is retried once on the fallback. A 400 `BadRequestError` is re-raised immediately (never failed over).
  If the fallback also fails or doesn't exist, `LLMUnavailableError` is raised; `api.py`'s `/query` turns
  that into **HTTP 503** "The language model service is temporarily unavailable. Please try again."
- **Circuit breaker:** after a primary failure of the failover kind, and only if a usable fallback exists,
  calls skip the primary and go straight to the fallback for `LLM_BREAKER_SECONDS` (default 60, float).
  After the window, exactly one call probes the primary (others keep using the fallback meanwhile): probe
  success closes the breaker; probe failure fails over for that call and reopens the window. A 400, or a
  failure with no fallback configured, never opens it. State is guarded by a `threading.Lock` (uvicorn runs
  sync routes in a thread pool). Skipped primary attempts are recorded with outcome
  `"skipped:breaker_open"`. The breaker is per-process, in memory. `LLM_BREAKER_SECONDS` is not in
  `.env.example`.
- Every attempt is appended to `LLMClient.attempts` (provider, model, purpose, attempt, outcome,
  duration_ms, tokens, finish_reason); provider switches and breaker open/close are logged at WARNING.
  No key values or URLs are logged or put in exception messages.
- Live status (from PROJECT_LOG.md, not re-run by Claude Code): a Groq-primary smoke test passed 4/4 on
  2026-10-24. The NVIDIA free endpoint's multi-minute stalls are only contained (timeout + failover +
  breaker), not fixed at the source.

### Debug tracing

`POST /query` accepts an optional `debug: bool` (default false); `LexAIEngine.query(..., debug=True)` adds
a `"debug"` key with per-stage timings (`stages_ms`), retrieval counts, reranker top-5 raw logits and
sigmoid scores, the gate comparison, `confidence_inputs`, an `llm_calls` list (one entry per
`LLMClient` attempt, including failed/skipped ones), `verification`, duplicate-chunk-text flags, and a
`refusal_path` string. `smoke_test.py --debug` sends the flag and prints the block. With debug off the
response has no `debug` key.

### API routes (`api.py`)

`GET /health`, `POST /upload` (multipart `file` + `owner_id`; returns `add_document`'s dict — see
dedupe above), `POST /query` (`question`, `owner_id`, optional `files`, optional `debug`; 503 on
`LLMUnavailableError`), `GET /papers?owner_id=...`, and `POST /reset`. **`/reset` takes no arguments and
calls `engine.reset()` with no `owner_id`, which deletes the whole Chroma collection for every owner and
clears all chat history.** The engine's `reset(owner_id)` per-owner mode exists but no route exposes it.

**Auth / owner_id:** the frontend signs users in with Supabase (Google OAuth only, via `SignIn.jsx`), and
everything about that identity — the profile, the disclaimer-acceptance flag, the file/collection
library — lives in Supabase tables (`profiles`, `files`, `collections`, `file_collections`), read and
written directly from the frontend (`libraryApi.js`), never through the backend. `App.jsx` then sends
`session.user.id` as a plain `owner_id` field on `/upload` and `/query` requests. **`api.py` does not
verify a JWT or any other credential** — it trusts whatever `owner_id` string the client sends, verbatim
(see the `TODO` comment above the `/upload` handler). Anyone who can call the API directly can read or
write any owner's documents by supplying their `owner_id`, and anyone can wipe everything via `/reset`.
This is also why the MCP server (`mcp_server/server.py`) can get away with one fixed `LEXAI_OWNER_ID`.

The frontend's view stack, gated by `App.jsx` with no router: `loading` → `!session` → `SignIn` →
`!profile` → `Onboarding` → `!profile.accepted_warning` → `Warning` → `view === "library"` → `Library` →
otherwise the chat view (upload + ask + answer/sources). Keep `App.jsx` and the dict returned by
`LexAIEngine.query()` in sync if changing either — the UI renders `refused`, `answer`, `citation`,
`confidence`, `label`, `verified` (shows "✓ verified" only when truthy), `sources[]` directly. On upload,
`App.jsx` reads `data.file` and `data.chunks` and passes them to `libraryApi.recordFile()`, which inserts
a new Supabase `files` row; it ignores `duplicate`, so a duplicate upload still adds a second Library row
for the same file (frontend unchanged; backend chunks are not duplicated).

Real backend env vars: `LLM_PROVIDER`, `NVIDIA_API_KEY`, `GROQ_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_S`,
`LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL`, `LLM_BREAKER_SECONDS` (not yet in `.env.example`),
`EMBED_MODEL`, `RERANK_MODEL`, `CHROMA_PERSIST_DIR`, `CONFIDENCE_THRESHOLD`, `FRONTEND_ORIGIN`. Frontend
needs `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` in its own `.env`. `mcp_server/` has its own
separate `.env` / `.env.example` with `LEXAI_BACKEND_URL` and `LEXAI_OWNER_ID`.

## README vs. actual implementation

The top-level `README.md` is a close match to the overall architecture — FastAPI + React + ChromaDB + BM25
+ RRF + reranker + confidence gate + faithfulness check, no LlamaIndex, no Streamlit. Known-stale details
in README.md, as of 2026-10-24:

- The NVIDIA NIM badge and the architecture-diagram/Stack-table "LLM" rows still say "LLaMA 3.1" /
  `LLaMA 3.1-8b-instruct` — the actual default model is `openai/gpt-oss-20b`, with Groq failover.
- "RAGAS Evaluation" section states the judge LLM as `meta/llama-3.1-8b-instruct`; the judge in
  `Evaluvate.py` is now `openai/gpt-oss-20b`. The four printed scores (faithfulness 0.5476, answer
  relevancy 0.6770, context precision 0.3679, context recall 0.4667; judge `meta/llama-3.1-8b-instruct`)
  were also produced before the reranker double-sigmoid fix and have **not** been re-run — don't quote
  them as current.
- "Project Structure" is missing the newer frontend files (`SignIn.jsx`, `Onboarding.jsx`, `Warning.jsx`,
  `Library.jsx`, `libraryApi.js`, `supabaseClient.js` and their `.css`), `llm_client.py`, `PROJECT_LOG.md`,
  `scratch/`, the `.bak` files, and the stray root `package.json`/`package-lock.json`.
- The `mcp_server` Claude Desktop config example path (`C:\dev\new_rag\...`) is a stale dev-machine path.
- Doesn't document the LLM failover/circuit breaker, upload dedupe, debug flag, the unauthenticated global
  `/reset`, or that `api.py` has no JWT verification.

## Known issues

- **No backend JWT verification** — see "Auth / owner_id" above. Anyone with API access and a guessed/
  known `owner_id` can read or write that owner's documents; `/reset` wipes all owners with no check.
- **`CONFIDENCE_THRESHOLD` = 0.30 is uncalibrated** against the current (single-sigmoid) reranker scores.
- **Live NVIDIA stalls not fixed at the source** — the free endpoint's intermittent multi-minute stalls
  (PROJECT_LOG.md 2026-10-15) are contained by the timeout/failover/breaker, not resolved.
- **First-query latency.** A 2026-10-13 smoke test's first query took ~106s (cold start) vs ~20s for the
  second. Unverified whether this still holds after the LLM client changes; no warm-up at startup exists.
- **Duplicate uploads still create duplicate Library rows** in Supabase (frontend ignores `duplicate`).
- **Chunk-id scheme can collide** (unverified in practice): ids are `filename_{collection.count()+i}`, so
  after chunks are deleted (e.g. engine-level per-owner `reset`) the count drops and a later upload of the
  same filename could reuse an existing id. Not exercised by any route today except via full `/reset`,
  which empties the collection.
- **README is stale** — see the section above.
- **`requirements.txt` lists unused packages** — `llama-index-*` and `supabase` are listed but not imported
  anywhere in `api.py`, `lexai_engine.py` or `llm_client.py`. Unverified whether intentional.
- **Stray root `package.json` / `package-lock.json`** — see Repo layout.

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
