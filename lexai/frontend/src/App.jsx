import { useState } from "react";
import "./App.css";

// Where the backend lives. (Phase 0 server running on port 8000.)
const API = "http://localhost:8000";

export default function App() {
  const [papers, setPapers] = useState([]);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [status, setStatus] = useState("");

  // ---- Upload a .pdf or .txt to the backend (/upload) ----
  async function handleUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setUploading(true);
    setStatus(`Uploading "${file.name}"...`);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API}/upload`, { method: "POST", body: form });
      const data = await res.json();
      setStatus(`Added "${data.file}" — ${data.chunks} chunks indexed.`);
      setPapers((prev) => [...new Set([...prev, data.file])]);
    } catch (err) {
      setStatus("Upload failed. Is the backend running on port 8000?");
    } finally {
      setUploading(false);
      e.target.value = ""; // allow re-uploading the same file
    }
  }

  // ---- Ask a question (/query) ----
  async function handleAsk() {
    if (!question.trim()) return;
    setAsking(true);
    setResult(null);
    try {
      const res = await fetch(`${API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      setResult(data);
    } catch (err) {
      setResult({ refused: true, answer: "Request failed. Is the backend running?" });
    } finally {
      setAsking(false);
    }
  }

  // Colour for the confidence label
  const labelColour = (label) =>
    label === "High" ? "#2e7d32" : label === "Medium" ? "#e08600" : "#c62828";

  return (
    <div className="app">
      <header className="header">
        <h1 className="wordmark">LEXAI</h1>
        <p className="tagline">Answers from your documents — cited, scored, verified.</p>
      </header>

      {/* Upload */}
      <section className="card">
        <label className="upload-btn">
          {uploading ? "Uploading..." : "Upload a .pdf or .txt"}
          <input type="file" accept=".pdf,.txt" onChange={handleUpload} hidden />
        </label>
        {papers.length > 0 && (
          <p className="papers">In your library: {papers.join(", ")}</p>
        )}
        {status && <p className="status">{status}</p>}
      </section>

      {/* Ask */}
      <section className="card">
        <textarea
          className="question"
          placeholder="Ask a question about your documents..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
        />
        <button className="ask-btn" onClick={handleAsk} disabled={asking}>
          {asking ? "Thinking..." : "Ask"}
        </button>
      </section>

      {/* Answer */}
      {result && (
        <section className="card answer-card">
          <div className="answer-text">{result.answer}</div>

          {!result.refused && result.citation && (
            <div className="meta-row">
              <span className="citation">{result.citation}</span>
              {result.confidence != null && (
                <span
                  className="confidence"
                  style={{ color: labelColour(result.label) }}
                >
                  {result.confidence}% — {result.label}
                </span>
              )}
              {result.verified && <span className="verified">✓ verified</span>}
            </div>
          )}

          {result.sources && result.sources.length > 0 && (
            <div className="sources">
              <h3>Supporting evidence</h3>
              {result.sources.map((s, i) => (
                <div className="source" key={i}>
                  <div className="source-head">
                    <span>{s.file} · p.{s.page}</span>
                    <span className="source-score">{s.score}%</span>
                  </div>
                  <p className="source-text">{s.text}…</p>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
