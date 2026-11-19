// One function per backend endpoint (names and fields from web/openapi.json).
import { CONFIG } from "./config.js?v=9";
import { getAccessToken } from "./auth.js?v=9";
import { postStream } from "./sse.js?v=9";
import { localStore } from "./store.js?v=9";

// "browser" when the server runs stateless (no database): trips are kept on this device instead.
let storage = "server";
export const storageMode = () => storage;
export const setStorageMode = (m) => { storage = m === "browser" ? "browser" : "server"; };
const local = () => storage === "browser";

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
  health: async () => { const h = await call("GET", "/health"); setStorageMode(h.storage); return h; },
  parse: (query) => call("POST", "/plans/parse", { query }),
  createPlan: (body) => call("POST", "/plans", body),
  savePlan: async (plan) => (local() ? localStore.save(plan) : call("POST", "/plans/save", plan)),
  listPlans: async () => (local() ? localStore.list() : call("GET", "/plans")),
  getPlan: async (id) => (local() ? localStore.get(id) : call("GET", `/plans/${encodeURIComponent(id)}`)),
  async editPlan(id, instruction) {
    if (!local()) return call("POST", `/plans/${encodeURIComponent(id)}/edit`, { instruction });
    const plan = localStore.get(id);
    const { id: _id, created_at: _c, ...body } = plan;  // the server validates the plan; ids stay on the device
    const res = await call("POST", "/plans/edit", { plan: body, instruction });
    if (res.plan) res.plan = localStore.save({ ...res.plan, id, created_at: plan.created_at });
    return res;
  },
  deletePlan: async (id) => (local() ? localStore.remove(id) : call("DELETE", `/plans/${encodeURIComponent(id)}`)),
  searchPlaces: (q) => call("GET", `/places/search?q=${encodeURIComponent(q)}`),
  weather: (lat, lon, start, end) => call("GET", `/weather?lat=${lat}&lon=${lon}&start=${start}&end=${end}`),
  evalReport: () => call("GET", "/eval/report"),
  async streamPlan(body, onEvent, signal) {
    return postStream(CONFIG.API_BASE + "/plans/stream", body, await authHeaders(), onEvent, signal);
  },
};
