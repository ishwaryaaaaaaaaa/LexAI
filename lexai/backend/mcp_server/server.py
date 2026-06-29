"""
LexAI MCP Server
================
Exposes the existing LexAI FastAPI backend as MCP tools so Claude Desktop
(or any MCP client) can query, upload, and list documents in a LexAI library.

This is a thin HTTP proxy — it contains no RAG logic. All intelligence stays
in the existing lexai_engine.py / api.py backend running on LEXAI_BACKEND_URL.

Single-user / demo mode: every tool call uses the fixed LEXAI_OWNER_ID from
.env. For multi-user support you would pass owner_id as an additional tool
parameter and have the MCP client supply a per-user identity at call time.
"""

import os
import httpx
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).parent / ".env")

BACKEND_URL = os.getenv("LEXAI_BACKEND_URL", "http://localhost:8000").rstrip("/")
OWNER_ID    = os.getenv("LEXAI_OWNER_ID", "")

if not OWNER_ID:
    raise RuntimeError(
        "LEXAI_OWNER_ID is not set. Add it to mcp_server/.env before starting the server."
    )

mcp = FastMCP("LexAI")


# ---------------------------------------------------------------------------
# Tool 1 — ask_lexai
# ---------------------------------------------------------------------------

@mcp.tool()
def ask_lexai(question: str) -> str:
    """
    Ask a question about the documents in your LexAI library.

    Returns the answer with its source citation, confidence score, and whether
    the answer was verified against the source text. If the retrieved evidence
    is too weak, the system refuses rather than guessing — that refusal is also
    returned here so you know the document doesn't cover the question.
    """
    try:
        resp = httpx.post(
            f"{BACKEND_URL}/query",
            json={"question": question, "owner_id": OWNER_ID},
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        return (
            f"Cannot reach the LexAI backend at {BACKEND_URL}. "
            "Make sure the backend is running (uvicorn api:app --port 8000)."
        )
    except httpx.HTTPStatusError as e:
        return f"Backend error {e.response.status_code}: {e.response.text}"

    data = resp.json()

    if data.get("refused"):
        confidence = data.get("confidence")
        suffix = f" (best match found: {confidence}% confidence)" if confidence is not None else ""
        return f"No answer — the documents do not contain sufficient information for this question{suffix}."

    parts = [f"Answer: {data.get('answer', '').strip()}"]

    citation = data.get("citation")
    if citation:
        parts.append(f"Source: {citation}")

    confidence = data.get("confidence")
    label = data.get("label")
    if confidence is not None:
        conf_str = f"Confidence: {confidence}%"
        if label:
            conf_str += f" ({label})"
        parts.append(conf_str)

    verified = data.get("verified")
    if verified is not None:
        parts.append(
            "Verified: yes" if verified
            else "Verified: no — answer may contain claims not fully supported by the source"
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 2 — upload_to_lexai
# ---------------------------------------------------------------------------

@mcp.tool()
def upload_to_lexai(file_path: str) -> str:
    """
    Upload a local PDF or .txt file to LexAI for indexing.

    Provide the full absolute path to the file on disk. The file is read
    locally and sent to the backend, which chunks and embeds it. Large PDFs
    may take up to 60 seconds. Returns how many chunks were indexed.
    """
    path = Path(file_path)

    if not path.exists():
        return f"File not found: {file_path}"
    if not path.is_file():
        return f"Not a file: {file_path}"
    if path.suffix.lower() not in {".pdf", ".txt"}:
        return f"Unsupported file type '{path.suffix}' — only .pdf and .txt are accepted."

    try:
        with open(path, "rb") as fh:
            resp = httpx.post(
                f"{BACKEND_URL}/upload",
                files={"file": (path.name, fh, "application/octet-stream")},
                data={"owner_id": OWNER_ID},
                timeout=120.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        return (
            f"Cannot reach the LexAI backend at {BACKEND_URL}. "
            "Make sure the backend is running."
        )
    except httpx.HTTPStatusError as e:
        return f"Backend error {e.response.status_code}: {e.response.text}"

    data = resp.json()
    filename = data.get("file", path.name)
    chunks   = data.get("chunks", 0)
    note     = data.get("note", "")

    if chunks == 0:
        return f"Uploaded '{filename}' but no text was extracted. {note}".strip()

    return f"'{filename}' indexed — {chunks} chunks added to your LexAI library."


# ---------------------------------------------------------------------------
# Tool 3 — list_lexai_documents
# ---------------------------------------------------------------------------

@mcp.tool()
def list_lexai_documents() -> str:
    """
    List all documents currently indexed in your LexAI library.
    """
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/papers",
            params={"owner_id": OWNER_ID},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        return (
            f"Cannot reach the LexAI backend at {BACKEND_URL}. "
            "Make sure the backend is running."
        )
    except httpx.HTTPStatusError as e:
        return f"Backend error {e.response.status_code}: {e.response.text}"

    papers = resp.json().get("papers", [])

    if not papers:
        return "No documents indexed yet. Use upload_to_lexai to add a PDF or .txt file."

    lines = [f"{len(papers)} document(s) in your LexAI library:"]
    for i, name in enumerate(papers, 1):
        lines.append(f"  {i}. {name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
