// Browser-side plan storage for stateless servers (health.storage === "browser").
// Plans never leave this device: no database, no account. Same shapes as the server API (PlanSummary, TripPlan).
const KEY = "travelmate.plans.v1";

function readAll() {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}
function writeAll(all) {
  try { localStorage.setItem(KEY, JSON.stringify(all)); return true; } catch { return false; }
}
const total = (p) => p.budget?.total ?? 0;

export const localStore = {
  save(plan) {
    const all = readAll();
    const id = plan.id || (crypto.randomUUID ? crypto.randomUUID() : `local-${Date.now()}`);
    const saved = { ...plan, id, created_at: plan.created_at || new Date().toISOString() };
    all[id] = saved;
    if (!writeAll(all)) throw new Error("This browser can't store trips (private window or storage full).");
    return saved;
  },
  list() {
    return Object.values(readAll())
      .map((p) => ({ id: p.id, title: p.title, start_date: p.request.start_date, end_date: p.request.end_date,
        total: total(p), created_at: p.created_at }))
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  },
  get(id) {
    const p = readAll()[id];
    if (!p) { const e = new Error("Plan not found on this device"); e.title = "Plan not found"; e.status = 404; throw e; }
    return p;
  },
  remove(id) { const all = readAll(); delete all[id]; writeAll(all); },
};
