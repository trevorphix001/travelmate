// Frontend configuration. Only public values belong here (never the service-role key).
// SUPABASE_URL and SUPABASE_ANON_KEY come from Supabase > Project Settings > API.
// Your own computer talks to the local API; the published site talks to the API on Render.
const LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const ONLINE_API = "https://travelmate-api.onrender.com/api/v1";

export const CONFIG = {
  API_BASE: window.TRAVELMATE_API_BASE || (LOCAL ? "http://127.0.0.1:8000/api/v1" : ONLINE_API),
  SUPABASE_URL: "https://bgvphduyjtotpkvnoaxm.supabase.co",
  SUPABASE_ANON_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJndnBoZHV5anRvdHBrdm5vYXhtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ5NzYxMjQsImV4cCI6MjExMDU1MjEyNH0.FReFJsOA_4uMth95ZRn_uPwz0sGtdFcuUbw0wswX5io",
};
