// Supabase email magic-link auth. Works only when SUPABASE_URL and SUPABASE_ANON_KEY are configured.
import { CONFIG } from "./config.js?v=8";

const SUPABASE_ESM = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.119.0/+esm";
let client = null;
let session = null;
const listeners = new Set();

export const authEnabled = () => Boolean(CONFIG.SUPABASE_URL && CONFIG.SUPABASE_ANON_KEY);
export const currentUser = () => session?.user ?? null;
export const onAuthChange = (fn) => listeners.add(fn);

export async function initAuth() {
  if (!authEnabled()) return;
  const { createClient } = await import(SUPABASE_ESM);
  client = createClient(CONFIG.SUPABASE_URL, CONFIG.SUPABASE_ANON_KEY);
  ({ data: { session } } = await client.auth.getSession());
  client.auth.onAuthStateChange((_event, s) => {
    session = s;
    listeners.forEach((fn) => fn(currentUser()));
  });
}

export async function signIn(email) {
  const redirect = window.location.origin + window.location.pathname;
  const { error } = await client.auth.signInWithOtp({ email, options: { emailRedirectTo: redirect } });
  if (error) throw error;
}

export async function signOut() {
  await client?.auth.signOut();
}

export async function getAccessToken() {
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data.session?.access_token ?? null;
}
