import { createClient } from "@supabase/supabase-js";

// These come from frontend/.env (must have the VITE_ prefix to be readable here).
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  console.error(
    "Missing Supabase env vars. Check frontend/.env has VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY."
  );
}

// One shared client, used everywhere in the app for auth + data.
export const supabase = createClient(supabaseUrl, supabaseAnonKey);
