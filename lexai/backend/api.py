"""
api.py
======
The FastAPI server. It exposes the LexAI engine over HTTP so the
React frontend can talk to it.

Endpoints:
  GET    /health             -> liveness check
  POST   /upload             -> add a .pdf or .txt document          (auth)
  POST   /query              -> ask a question                       (auth)
  GET    /papers             -> list the caller's documents          (auth)
  DELETE /papers/{filename}  -> delete one of the caller's documents (auth)
  POST   /reset              -> clear everything                     (X-Admin-Key)

"(auth)" routes take the owner from a verified Supabase access token
(Authorization: Bearer ...), see auth.py. With AUTH_DISABLED=true (local
development only) they use the owner_id sent by the client instead.

Run it:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

import hmac
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

import auth
from auth import get_user_id
from lexai_engine import LexAIEngine
from llm_client import LLMUnavailableError

load_dotenv(Path(__file__).parent / ".env")

# Refuse to start if auth is enabled but not configured (raises AuthConfigError).
auth.check_startup_config()

app = FastAPI(title="LexAI")

# Allow the frontend (Vite dev server, and later the deployed URL) to call us.
origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)

# Build the engine once, when the server starts.
engine = LexAIEngine()


class HistoryTurn(BaseModel):
    q: str
    a: str

    # Truncated, not rejected — a too-long turn still condenses, just clipped.
    @field_validator("q", "a")
    @classmethod
    def _truncate(cls, v):
        return v[:1500]


class QueryIn(BaseModel):
    question: str
    owner_id: Optional[str] = None  # used only when AUTH_DISABLED=true; ignored otherwise
    files: Optional[List[str]] = None  # scope to specific filenames (Library "ask this file/collection")
    debug: Optional[bool] = False  # opt-in diagnostic tracing; see lexai_engine.query()
    # Recent turns for follow-up condensing. The engine itself keeps no chat
    # history between requests (see lexai_engine.query()'s `history` arg); the
    # client resends whatever turns it wants considered. More than 6 -> 422.
    history: Optional[List[HistoryTurn]] = Field(default=None, max_length=6)


def resolve_owner(user_id, client_owner_id):
    """The verified token subject when auth is on; the client's owner_id only
    when auth is disabled (local development)."""
    if user_id is not None:
        return user_id
    if not auth.auth_disabled():
        raise HTTPException(status_code=401, detail=auth.GENERIC_401)
    if not client_owner_id:
        raise HTTPException(status_code=400, detail="owner_id is required when auth is disabled.")
    return client_owner_id


@app.get("/health")
def health():
    total_papers = len({c["file"] for c in engine.chunks})
    return {"status": "ok", "papers": total_papers, "chunks": len(engine.chunks)}


@app.post("/upload")
async def upload(file: UploadFile = File(...), owner_id: Optional[str] = Form(None),
                 user_id: Optional[str] = Depends(get_user_id)):
    owner = resolve_owner(user_id, owner_id)
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = engine.add_document(tmp_path, file.filename, owner)
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/query")
def query(body: QueryIn, user_id: Optional[str] = Depends(get_user_id)):
    owner = resolve_owner(user_id, body.owner_id)
    history = [{"q": h.q, "a": h.a} for h in body.history] if body.history else None
    try:
        return engine.query(body.question, owner, allowed_files=body.files, debug=body.debug,
                            history=history)
    except LLMUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="The language model service is temporarily unavailable. Please try again.",
        )


@app.get("/papers")
def papers(owner_id: Optional[str] = None, user_id: Optional[str] = Depends(get_user_id)):
    owner = resolve_owner(user_id, owner_id)
    return {"papers": engine.list_papers(owner)}


@app.delete("/papers/{filename:path}")
def delete_paper(filename: str, owner_id: Optional[str] = None,
                 user_id: Optional[str] = Depends(get_user_id)):
    owner = resolve_owner(user_id, owner_id)
    deleted = engine.delete_document(owner, filename)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="No such file.")
    return {"file": filename, "deleted_chunks": deleted}


@app.post("/reset")
def reset(x_admin_key: Optional[str] = Header(default=None)):
    # Wipes every owner's documents, so it is locked behind ADMIN_KEY (never
    # behind a user token). Unset/empty ADMIN_KEY disables it.
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=403, detail="/reset is disabled.")
    if not x_admin_key or not hmac.compare_digest(x_admin_key.encode("utf-8"),
                                                  admin_key.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Forbidden.")
    return engine.reset()
