import { supabase } from "./supabaseClient";

// ============================================================
// Every call to the LexAI backend goes through apiFetch, so the
// login token, the API URL and error handling live in one place.
// ============================================================

// Set VITE_API_URL in frontend/.env for any deployed backend.
// The localhost default is for local development only.
export const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const SIGN_IN_AGAIN = "Your session has ended. Please sign in again.";

// An error with the HTTP status attached (0 = backend not reachable).
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// App.jsx registers a callback here to show SIGN_IN_AGAIN on the sign-in screen.
let sessionExpiredHandler = null;
export function onSessionExpired(handler) {
  sessionExpiredHandler = handler;
}

async function expireSession() {
  if (sessionExpiredHandler) sessionExpiredHandler(SIGN_IN_AGAIN);
  await supabase.auth.signOut();
  throw new ApiError(SIGN_IN_AGAIN, 401);
}

// Calls the backend and returns the parsed JSON body.
// Throws ApiError on any failure. Never retries.
export async function apiFetch(path, options = {}) {
  // Read the session right before each request so a refreshed token is used.
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) return expireSession();

  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);

  let res;
  try {
    res = await fetch(`${API}${path}`, { ...options, headers });
  } catch {
    throw new ApiError("Could not reach the LexAI backend. Is it running?", 0);
  }

  if (res.status === 401) return expireSession();

  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // body was not JSON
    }
    // 503 carries the backend's own safe message ("...temporarily unavailable...").
    const message =
      res.status === 503 && typeof detail === "string"
        ? detail
        : `Request failed (HTTP ${res.status}).`;
    throw new ApiError(message, res.status);
  }

  return res.json();
}
