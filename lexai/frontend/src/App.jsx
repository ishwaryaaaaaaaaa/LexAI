import { useState, useEffect } from "react";
import { supabase } from "./supabaseClient";
import { recordFile, getFileIdsInCollection, listFiles } from "./libraryApi";
import SignIn from "./SignIn";
import Onboarding from "./Onboarding";
import Warning from "./Warning";
import Library from "./Library";
import "./App.css";

const API = "http://localhost:8000";

export default function App() {
  const [session, setSession] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);

  // Chat state
  const [papers, setPapers] = useState([]);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [status, setStatus] = useState("");

  // View + scope state (Phase 4)
  const [view, setView] = useState("chat"); // "chat" | "library"
  const [scope, setScope] = useState({ type: "all" }); // all | file | collection

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      if (data.session) loadProfile(data.session.user.id);
      else setLoading(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
      if (newSession) loadProfile(newSession.user.id);
      else {
        setProfile(null);
        setLoading(false);
      }
    });

    return () => listener.subscription.unsubscribe();
  }, []);

  async function loadProfile(userId) {
    const { data } = await supabase
      .from("profiles")
      .select("*")
      .eq("id", userId)
      .maybeSingle();
    setProfile(data);
    setLoading(false);
  }

  async function handleSignOut() {
    await supabase.auth.signOut();
  }

  async function handleUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setUploading(true);
    setStatus(`Uploading "${file.name}"...`);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("owner_id", session.user.id);
      const res = await fetch(`${API}/upload`, { method: "POST", body: form });
      const data = await res.json();
      setStatus(`Added "${data.file}" — ${data.chunks} chunks indexed.`);
      setPapers((prev) => [...new Set([...prev, data.file])]);

      try {
        await recordFile(session.user.id, data.file, file.type, data.chunks);
      } catch (libErr) {
        console.error("Could not save file record to library:", libErr);
        setStatus(`Added "${data.file}" — ${data.chunks} chunks indexed. (Not saved to library.)`);
      }
    } catch {
      setStatus("Upload failed. Is the backend running on port 8000?");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleAsk() {
    if (!question.trim()) return;
    setAsking(true);
    setResult(null);
    try {
      const body = { question, owner_id: session.user.id };
      if (scope.type === "file") {
        body.files = [scope.name];
      } else if (scope.type === "collection") {
        const fileIds = await getFileIdsInCollection(scope.id);
        const allFiles = await listFiles(session.user.id);
        body.files = allFiles
          .filter((f) => fileIds.includes(f.id))
          .map((f) => f.filename);
      }
      const res = await fetch(`${API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setResult(await res.json());
    } catch {
      setResult({ refused: true, answer: "Request failed. Is the backend running?" });
    } finally {
      setAsking(false);
    }
  }

  function handleSelectScope(newScope) {
    setScope(newScope);
    setView("chat");
  }

  const labelColour = (label) =>
    label === "High" ? "#2e7d32" : label === "Medium" ? "#e08600" : "#c62828";

  // ---- The gate ----
  if (loading) return <div className="app"><p>Loading...</p></div>;
  if (!session) return <SignIn />;

  if (!profile) {
    return <Onboarding session={session} onComplete={() => loadProfile(session.user.id)} />;
  }

  if (!profile.accepted_warning) {
    return <Warning session={session} onAccept={() => loadProfile(session.user.id)} />;
  }

  if (view === "library") {
    return (
      <Library
        session={session}
        onClose={() => setView("chat")}
        onSelectScope={handleSelectScope}
      />
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h1 className="wordmark">LEXAI</h1>
            <p className="tagline">Good to see you, {profile.preferred_name}.</p>
          </div>
          <div style={{ textAlign: "right" }}>
            <p style={{ fontSize: 13, color: "#6b6459", margin: 0 }}>{profile.preferred_name}</p>
            <button
              onClick={() => setView("library")}
              style={{ background: "none", border: "none", textDecoration: "underline",
                       color: "#1a1a1a", fontSize: 12, cursor: "pointer", padding: "0 8px 0 0",
                       fontWeight: 600 }}
            >
              Library
            </button>
            <button
              onClick={handleSignOut}
              style={{ background: "none", border: "none", textDecoration: "underline",
                       color: "#6b6459", fontSize: 12, cursor: "pointer", padding: 0 }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Current scope indicator */}
      <div style={{ fontSize: 13, color: "#6b6459", marginBottom: 10 }}>
        Asking: <strong>
          {scope.type === "all" ? "All my files" : scope.name}
        </strong>{" "}
        <button
          onClick={() => setView("library")}
          style={{ background: "none", border: "none", textDecoration: "underline",
                   color: "#1a1a1a", fontSize: 12, cursor: "pointer", padding: 0 }}
        >
          change
        </button>
      </div>

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
