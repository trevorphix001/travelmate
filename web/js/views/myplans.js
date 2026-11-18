import { api } from "../api.js?v=9";
import { authEnabled, currentUser } from "../auth.js?v=9";
import { icon } from "../icons.js?v=9";
import { hydratePhotos } from "../photos.js?v=9";
import { errorBox, esc, fmtDate, rs } from "../util.js?v=9";

const head = `<div class="page-head"><span class="eyebrow">My trips</span><h1>Your saved trips</h1></div>`;
const destOf = (title) => (title.split(" to ")[1] || title).split(",")[0].trim();

export async function renderMyPlans(el) {
  const shell = (inner) => { el.innerHTML = `<div class="container page">${head}${inner}</div>`; };
  if (!currentUser()) {
    shell(`<div class="card empty"><span class="cat-icon">${icon("user")}</span><h2>Sign in to see your trips</h2>
      <p class="muted">${authEnabled() ? "Use “Sign in” at the top of the page. We'll email you a link." : "Sign-in isn't available right now."}</p>
      <a class="btn btn-cta" href="#/">${icon("search")} Plan a trip</a></div>`);
    return;
  }
  shell(`<p class="muted"><span class="spinner" aria-hidden="true"></span> Loading your trips…</p>`);
  try {
    const plans = await api.listPlans();
    if (!plans.length) {
      shell(`<div class="card empty"><span class="cat-icon">${icon("map")}</span><h2>No saved trips yet</h2>
        <p class="muted">Trips you plan while signed in are saved here.</p><a class="btn btn-cta" href="#/">${icon("search")} Plan a trip</a></div>`);
      return;
    }
    shell(`<div class="trips-grid">${plans.map((p) => `
      <article class="trip-card" data-id="${esc(p.id)}">
        <div class="idea-media"><div class="img-fallback"></div><img data-wiki="${esc(destOf(p.title))}" data-width="500" alt=""></div>
        <div class="idea-body">
          <h3>${esc(p.title)}</h3>
          <ul class="facts"><li>${icon("calendar")} ${fmtDate(p.start_date)} – ${fmtDate(p.end_date)}</li><li>${icon("wallet")} ${rs(p.total)} est.</li></ul>
          <div style="display:flex;gap:8px;margin-top:auto">
            <a class="btn btn-brand btn-sm" href="#/plan/${encodeURIComponent(p.id)}">View trip</a>
            <button type="button" class="btn btn-sm btn-danger" data-del="${esc(p.id)}">Delete</button>
          </div>
        </div>
      </article>`).join("")}</div>`);
    hydratePhotos(el);
    el.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Delete this trip? This can't be undone.")) return;
      try { await api.deletePlan(b.dataset.del); b.closest("article").remove(); } catch (err) { alert(err.message); }
    }));
  } catch (err) {
    shell(errorBox(err));
  }
}
