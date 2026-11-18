import { api } from "./api.js?v=8";
import { authEnabled, currentUser, initAuth, onAuthChange, signIn, signOut } from "./auth.js?v=8";
import { icon } from "./icons.js?v=8";
import { dispatch, go, route, startRouter } from "./router.js?v=8";
import { errorBox, esc } from "./util.js?v=8";
import { renderEval } from "./views/eval.js?v=8";
import { initHome, scrollToSection } from "./views/home.js?v=8";
import { renderMyPlans } from "./views/myplans.js?v=8";
import { renderPlan } from "./views/plan.js?v=8";
import { runProgress } from "./views/progress.js?v=8";

const $ = (id) => document.getElementById(id);
let current = null; // { plan } from the last generation

/** Show one view; `nav` marks the active header link. */
function show(id, nav = id) {
  document.querySelectorAll("main > .view").forEach((v) => { v.hidden = v.id !== id; });
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $("main-nav").classList.remove("open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
}

function renderAuth() {
  const box = $("auth-box");
  if (!authEnabled()) { box.innerHTML = ""; return; }
  const user = currentUser();
  if (user) {
    box.innerHTML = `<a class="btn btn-ghost btn-sm" href="#/plans">${icon("map")} My trips</a>
      <button type="button" class="btn btn-sm" id="account-btn" aria-expanded="false" aria-controls="account-menu">${icon("user")}<span class="sr-only">Account</span></button>
      <div class="account-menu" id="account-menu" hidden><p class="small muted" style="margin:0 0 10px">Signed in as<br><strong>${esc(user.email)}</strong></p>
        <button type="button" class="btn btn-sm" id="signout" style="width:100%">Sign out</button></div>`;
    $("signout").addEventListener("click", signOut);
  } else {
    box.innerHTML = `<button type="button" class="btn btn-sm" id="account-btn" aria-expanded="false" aria-controls="account-menu">${icon("user")} Sign in</button>
      <div class="account-menu" id="account-menu" hidden>
        <form id="signin"><h3 style="margin-bottom:4px">Sign in</h3>
          <p class="small muted">Save trips and come back to them. We'll email you a sign-in link.</p>
          <label for="email">Email address</label><input id="email" type="email" required placeholder="you@example.com" autocomplete="email">
          <button type="submit" class="btn btn-cta btn-sm" style="width:100%;margin-top:10px">Email me a link</button>
          <p id="signin-msg" class="small muted" aria-live="polite" style="margin:8px 0 0"></p></form></div>`;
    $("signin").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await signIn($("email").value); $("signin-msg").textContent = "Check your inbox for the sign-in link."; }
      catch (err) { $("signin-msg").textContent = err.message; }
    });
  }
  const btn = $("account-btn");
  const menu = $("account-menu");
  btn.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    btn.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector("input, button")?.focus();
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !box.contains(e.target)) { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
  });
}

function generate(body) {
  show("progress", "home");
  window.scrollTo(0, 0);
  runProgress($("progress"), body, (plan) => {
    current = { plan };
    go(plan.id ? `#/plan/${encodeURIComponent(plan.id)}` : "#/plan/current");
  }, () => go("#/"));
}

function showPlan(plan, opts = {}) {
  show("plan", "home");
  renderPlan($("plan"), plan, {
    ...opts,
    onEdited: (updated, diff) => { current = { plan: updated }; showPlan(updated, { diff }); },
    onSaved: (saved) => { current = { plan: saved }; go(`#/plan/${encodeURIComponent(saved.id)}`); },
    onSignIn: () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
      if ($("account-menu")?.hidden) $("account-btn")?.click();
    },
  });
}

const homeAt = (section) => () => {
  show("home", section || "home");
  if (section) scrollToSection($("home"), section); else window.scrollTo(0, 0);
};
route("/", homeAt(null));
route("/destinations", homeAt("destinations"));
route("/ideas", homeAt("ideas"));
route("/plans", () => { show("my-plans"); window.scrollTo(0, 0); renderMyPlans($("my-plans")); });
route("/eval", () => {
  show("eval", "");
  $("eval").innerHTML = `<div class="container page" id="eval-inner"></div>`;
  renderEval($("eval-inner"));
});
route("/plan/current", () => {
  if (current) { window.scrollTo(0, 0); showPlan(current.plan); } else go("#/");
});
route("/plan/fixture", async () => {
  // Debug: render a recorded plan (web/data/golden.json) without calling the API.
  try { showPlan(await (await fetch("data/golden.json", { cache: "no-store" })).json()); }
  catch { show("plan"); $("plan").innerHTML = `<div class="container page">${errorBox({ title: "No sample trip", detail: "Add web/data/golden.json" })}</div>`; }
});
route("/plan/:id", async (id) => {
  window.scrollTo(0, 0);
  if (current?.plan.id === id) return showPlan(current.plan);
  show("plan", "my-plans");
  $("plan").innerHTML = `<div class="container page"><p class="muted"><span class="spinner"></span> Loading your trip…</p></div>`;
  try { const plan = await api.getPlan(id); current = { plan }; showPlan(plan); }
  catch (err) { $("plan").innerHTML = `<div class="container page">${errorBox(err)}</div>`; }
});

async function boot() {
  initHome($("home"), generate);
  $("menu-toggle").addEventListener("click", () => {
    const open = $("main-nav").classList.toggle("open");
    $("menu-toggle").setAttribute("aria-expanded", String(open));
  });
  try { await initAuth(); } catch (err) { console.warn("auth init failed", err); }
  renderAuth();
  onAuthChange(() => { renderAuth(); dispatch(); });
  startRouter();
  try {
    const h = await api.health();
    const llm = Object.values(h.providers).some(Boolean);
    $("health").textContent = !llm ? "Trip planning is temporarily unavailable." : !h.db ? "Saving trips is temporarily unavailable." : "";
  } catch {
    $("health").textContent = "Can't reach the TravelMate server right now.";
  }
}

boot();
