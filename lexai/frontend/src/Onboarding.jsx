import { useState } from "react";
import { supabase } from "./supabaseClient";
import "./Onboarding.css";

const PURPOSES = [
  "Research",
  "Legal",
  "Journalism",
  "Personal learning",
  "Work / Business",
  "Other",
];

export default function Onboarding({ session, onComplete }) {
  const [preferredName, setPreferredName] = useState("");
  const [birthday, setBirthday] = useState("");
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    if (!preferredName.trim()) {
      setError("Please tell us what to call you.");
      return;
    }
    setSaving(true);
    setError("");

    const { error: dbError } = await supabase.from("profiles").upsert({
      id: session.user.id,
      full_name: session.user.user_metadata?.full_name || session.user.email,
      preferred_name: preferredName.trim(),
      birthday: birthday || null,
      purpose,
    });

    setSaving(false);

    if (dbError) {
      setError("Could not save your profile. Please try again.");
      console.error(dbError);
      return;
    }

    onComplete(); // tell App.jsx we're done, move to the next step
  }

  return (
    <div className="onboard-page">
      <form className="onboard-card" onSubmit={handleSubmit}>
        <h1 className="onboard-title">Welcome to LexAI</h1>
        <p className="onboard-sub">A few quick details before you get started.</p>

        <label className="onboard-label">What should we call you?</label>
        <input
          className="onboard-input"
          type="text"
          placeholder="Preferred name"
          value={preferredName}
          onChange={(e) => setPreferredName(e.target.value)}
        />

        <label className="onboard-label">Birthday</label>
        <input
          className="onboard-input"
          type="date"
          value={birthday}
          onChange={(e) => setBirthday(e.target.value)}
        />

        <label className="onboard-label">What are you using LexAI for?</label>
        <select
          className="onboard-input"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        >
          {PURPOSES.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        {error && <p className="onboard-error">{error}</p>}

        <button className="onboard-btn" type="submit" disabled={saving}>
          {saving ? "Saving..." : "Continue"}
        </button>
      </form>
    </div>
  );
}
