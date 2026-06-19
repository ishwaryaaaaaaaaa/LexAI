import { useState, useEffect } from "react";
import { supabase } from "./supabaseClient";
import SignIn from "./SignIn";
import "./App.css";

const API = "http://localhost:8000";

export default function App() {
  // ---- Auth state ----
  const [session, setSession] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);

  // ---- Chat state (unchanged from Phase 1) ----
  const [papers, setPapers] = useState([]);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [status, setStatus] = useState("");

  // Check for an existing session, and listen for sign-in/sign-out events.
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthLoading(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
    });

    return () => listener.subscription.unsubscribe();
  }, []);

  async function handleSignOut() {
    await supabase.auth.signOut();
  }

  // ---- Upload ----
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
      e.target.value = "";
    }
  }

  // ---- Ask ----
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

  const labelColour = (label) =>
    label === "High" ? "#2e7d32" : label === "Medium" ? "#e08600" : "#c62828";

  // ---- Render: gate everything behind auth ----
  if (authLoading) {
    return <div className="app"><p>Loading...</p></div>;
  }

  if (!session) {
    return <SignIn />;
  }

  const userName =
    session.user.user_metadata?.full_name || session.user.email;

  return (
    <div className="app">
      <header className="header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h1 className="wordmark">LEXAI</h1>
            <p className="tagline">Answers from your documents — cited, scored, verified.</p>
          </div>
          <div style={{ textAlign: "right" }}>
            <p style={{ fontSize: 13, color: "#6b6459", margin: 0 }}>{userName}</p>
            <button
              onClick={handleSignOut}
              style={{
                background: "none", border: "none", textDecoration: "underline",
                color: "#6b6459", fontSize: 12, cursor: "pointer", padding: 0,
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <section className="card">
        <label className="upload-btn">
          {uploading ? "Uploading..." : "Upload a .pdf or .txt"}
          <input type="file" accept=".pdf,.txt" onChange={handleUpload} hidden />
        </label>
        {papers.length > 0 && <p className="papers">In your library: {papers.join(", ")}</p>}
        {status && <p className="status">{status}</p>}
      </section>

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

      {result && (
        <section className="card answer-card">
          <div className="answer-text">{result.answer}</div>

          {!result.refused && result.citation && (
            <div className="meta-row">
              <span className="citation">{result.citation}</span>
              {result.confidence != null && (
                <span className="confidence" style={{ color: labelColour(result.label) }}>
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
