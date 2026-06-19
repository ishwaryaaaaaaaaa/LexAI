import { useState } from "react";
import { supabase } from "./supabaseClient";
import "./Warning.css";

export default function Warning({ session, onAccept }) {
  const [saving, setSaving] = useState(false);

  async function handleAccept() {
    setSaving(true);
    const { error } = await supabase
      .from("profiles")
      .update({ accepted_warning: true })
      .eq("id", session.user.id);
    setSaving(false);

    if (error) {
      console.error(error);
      alert("Something went wrong. Please try again.");
      return;
    }
    onAccept();
  }

  return (
    <div className="warning-page">
      <div className="warning-card">
        <h1 className="warning-title">Before you start</h1>

        <ul className="warning-list">
          <li>LexAI reads <strong>text only</strong> — it cannot read tables, diagrams, figures, images, or equations.</li>
          <li>Answers come <strong>only from the documents you upload</strong> — never from outside knowledge.</li>
          <li>If your documents don't contain the answer, LexAI will say <strong>"I don't know"</strong> rather than guess.</li>
          <li>LexAI is not a substitute for professional, legal, or medical advice.</li>
        </ul>

        <button className="warning-btn" onClick={handleAccept} disabled={saving}>
          {saving ? "Saving..." : "I Accept"}
        </button>
      </div>
    </div>
  );
}
