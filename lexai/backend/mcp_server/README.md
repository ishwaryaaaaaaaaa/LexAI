# LexAI MCP Server

Exposes your existing LexAI FastAPI backend as [Model Context Protocol](https://modelcontextprotocol.io) tools, so Claude Desktop (or any MCP-compatible client) can query, upload, and list documents in your LexAI library directly from a conversation.

This is a **thin HTTP proxy** — it contains no RAG logic. Every tool call simply forwards to the existing `api.py` backend over HTTP, the same way the React frontend does.

---

## Single-user / demo mode

This server is intentionally configured for **one fixed user**. The `LEXAI_OWNER_ID` in `.env` is used for every tool call. There is no per-request authentication.

> **For multi-user support** you would add `owner_id: str` as an explicit parameter to each tool, remove the hardcoded env-var lookup, and have each MCP client supply its own identity at call time — likely derived from a verified auth token passed through the MCP transport layer.

---

## Tools exposed

| Tool | What it does |
|---|---|
| `ask_lexai(question)` | Queries the 7-layer RAG pipeline and returns the answer, source citation, confidence score, and verification status |
| `upload_to_lexai(file_path)` | Reads a local `.pdf` or `.txt` file and indexes it into your LexAI library |
| `list_lexai_documents()` | Returns the list of documents currently indexed under your owner ID |

---

## Setup

**1. Create a virtual environment and install dependencies**
```bash
cd lexai/backend/mcp_server
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac / Linux
pip install -r requirements.txt
```

**2. Configure `.env`**
```bash
cp .env.example .env
```

Edit `.env`:
```env
LEXAI_BACKEND_URL=http://localhost:8000
LEXAI_OWNER_ID=your-owner-id-here
```

To find your `owner_id`, run this against the chroma store SQLite:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('../chroma_store/chroma.sqlite3')
cur = conn.cursor()
cur.execute(\"SELECT DISTINCT string_value FROM embedding_metadata WHERE key='owner_id'\")
for row in cur.fetchall(): print(row[0])
conn.close()
"
```

**3. Make sure the LexAI backend is running**
```bash
cd lexai/backend
.venv\Scripts\uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

**4. Test the server manually**
```bash
cd mcp_server
python server.py
```
You should see the FastMCP startup message. Press `Ctrl+C` to stop — Claude Desktop will manage the process lifecycle.

---

## Pointing Claude Desktop at this server

Open your Claude Desktop config file:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the `lexai` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "lexai": {
      "command": "C:\\dev\\new_rag\\lexai\\backend\\mcp_server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\dev\\new_rag\\lexai\\backend\\mcp_server\\server.py"]
    }
  }
}
```

> **Windows path note:** use double backslashes (`\\`) in the JSON, or forward slashes (`/`) — both work.

If you already have other MCP servers configured, add `"lexai": { ... }` alongside them inside the existing `"mcpServers"` object — don't replace the whole file.

Restart Claude Desktop after saving the config. The three LexAI tools will appear in the tool picker in any new conversation.

---

## How a conversation looks

```
You: What did MatRisk AI rank at the EXCAVATE hackathon?

Claude: [calls ask_lexai("What did MatRisk AI rank at the EXCAVATE hackathon?")]

Answer: MatRisk AI ranked Top 9 nationally at the EXCAVATE Hackathon.
Source: SOP.pdf · p.1
Confidence: 82% (High)
Verified: yes
```

```
You: Upload my new paper — C:\Users\me\papers\attention_is_all_you_need.pdf

Claude: [calls upload_to_lexai("C:\\Users\\me\\papers\\attention_is_all_you_need.pdf")]

'attention_is_all_you_need.pdf' indexed — 47 chunks added to your LexAI library.
```

---

## File structure

```
mcp_server/
├── server.py          # MCP server — three tools, nothing else
├── requirements.txt   # mcp[cli], httpx, python-dotenv
├── .env               # your config (gitignored)
├── .env.example       # template
└── README.md
```

---

## What this does NOT do

- Does not modify `api.py` or `lexai_engine.py` — the existing backend is untouched
- Does not handle authentication beyond the fixed owner_id
- Does not stream answers — returns the full response once the backend call completes
- Does not expose the `/reset` endpoint as a tool (intentional — destructive operations should not be one accidental tool call away)
