// One function per backend endpoint (names and fields from web/openapi.json).
import { CONFIG } from "./config.js?v=9";
import { getAccessToken } from "./auth.js?v=9";
import { postStream } from "./sse.js?v=9";

export class ApiError extends Error {
  constructor(title, detail, status, type) {
    super(detail ? `${title}: ${detail}` : title);
    Object.assign(this, { title, detail, status, type });
  }
}

async function authHeaders() {
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function call(method, path, body) {
  let res;
  try {
    res = await fetch(CONFIG.API_BASE + path, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...(await authHeaders()) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("API unreachable", `Is the backend running at ${CONFIG.API_BASE}?`, 0, "network");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.title || `HTTP ${res.status}`, data.detail || "", res.status, data.type);
  return data;
}

export const api = {
  health: () => call("GET", "/health"),
  parse: (query) => call("POST", "/plans/parse", { query }),
  createPlan: (body) => call("POST", "/plans", body),
  savePlan: (plan) => call("POST", "/plans/save", plan),
  listPlans: () => call("GET", "/plans"),
  getPlan: (id) => call("GET", `/plans/${encodeURIComponent(id)}`),
  editPlan: (id, instruction) => call("POST", `/plans/${encodeURIComponent(id)}/edit`, { instruction }),
  deletePlan: (id) => call("DELETE", `/plans/${encodeURIComponent(id)}`),
  searchPlaces: (q) => call("GET", `/places/search?q=${encodeURIComponent(q)}`),
  weather: (lat, lon, start, end) => call("GET", `/weather?lat=${lat}&lon=${lon}&start=${start}&end=${end}`),
  evalReport: () => call("GET", "/eval/report"),
  async streamPlan(body, onEvent, signal) {
    return postStream(CONFIG.API_BASE + "/plans/stream", body, await authHeaders(), onEvent, signal);
  },
};
