"""
api.py
======
The FastAPI server. It exposes the LexAI engine over HTTP so the
React frontend can talk to it.

Endpoints (matches the doc):
  GET  /health   -> liveness check
  POST /upload   -> add a .pdf or .txt document
  POST /query    -> ask a question
  GET  /papers   -> list uploaded documents
  POST /reset    -> clear everything

Run it:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from lexai_engine import LexAIEngine

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(title="LexAI")

# Allow the frontend (Vite dev server, and later the Vercel URL) to call us.
origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Build the engine once, when the server starts.
engine = LexAIEngine()


class QueryIn(BaseModel):
    question: str
    owner_id: str
    files: Optional[List[str]] = None  # scope to specific filenames (Library "ask this file/collection")


@app.get("/health")
def health():
    total_papers = len({c["file"] for c in engine.chunks})
    return {"status": "ok", "papers": total_papers, "chunks": len(engine.chunks)}


@app.post("/upload")
async def upload(file: UploadFile = File(...), owner_id: str = Form(...)):
    # TODO: once real auth is wired up, derive owner_id from a verified Supabase
    # JWT instead of trusting this form field.
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = engine.add_document(tmp_path, file.filename, owner_id)
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/query")
def query(body: QueryIn):
    return engine.query(body.question, body.owner_id, allowed_files=body.files)


@app.get("/papers")
def papers(owner_id: str):
    return {"papers": engine.list_papers(owner_id)}


@app.post("/reset")
def reset():
    return engine.reset()

