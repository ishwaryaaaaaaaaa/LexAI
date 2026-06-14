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

from fastapi import FastAPI, UploadFile, File
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


@app.get("/health")
def health():
    return {"status": "ok", "papers": len(engine.list_papers()), "chunks": len(engine.chunks)}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    # Save the uploaded file to a temp path, then hand it to the engine.
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = engine.add_document(tmp_path, file.filename)
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/query")
def query(body: QueryIn):
    return engine.query(body.question)


@app.get("/papers")
def papers():
    return {"papers": engine.list_papers()}


@app.post("/reset")
def reset():
    return engine.reset()

