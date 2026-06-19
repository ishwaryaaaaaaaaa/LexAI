import { supabase } from "./supabaseClient";
import "./SignIn.css";

export default function SignIn() {
  async function handleGoogleSignIn() {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: window.location.origin, // back to the app after login
      },
    });
    if (error) {
      console.error("Sign-in error:", error.message);
      alert("Sign-in failed. Please try again.");
    }
    // On success, Supabase redirects to Google, then back to the app.
    // App.jsx's auth listener picks up the new session automatically.
  }

  return (
    <div className="signin-page">
      <div className="signin-card">
        <h1 className="signin-wordmark">LEXAI</h1>
        <h2 className="signin-headline">Clarity through intelligence.</h2>
        <p className="signin-sub">
          Answers from your own documents — cited, scored, and verified.
        </p>

        <button className="google-btn" onClick={handleGoogleSignIn}>
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84c-.21 1.12-.85 2.07-1.81 2.71v2.26h2.92C16.6 14.2 17.64 11.93 17.64 9.2z"/>
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.55-1.85.87-3.04.87-2.34 0-4.32-1.58-5.03-3.71H.96v2.33C2.44 15.98 5.48 18 9 18z"/>
            <path fill="#FBBC05" d="M3.97 10.72c-.18-.55-.28-1.13-.28-1.72s.1-1.17.28-1.72V4.95H.96A8.96 8.96 0 0 0 0 9c0 1.45.35 2.82.96 4.05l3.01-2.33z"/>
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0 5.48 0 2.44 2.02.96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/>
          </svg>
          Continue with Google
        </button>

        <div className="signin-divider"><span>OR</span></div>

        <button className="sso-link" disabled>
          Sign in with Institutional SSO (coming soon)
        </button>

        <div className="signin-footer">
          <span>Scholarly Authority · © 2026 LexAI Research Systems</span>
        </div>
      </div>
    </div>
  );
}
