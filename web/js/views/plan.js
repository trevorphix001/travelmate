// Itinerary page. Renders numbers exactly as the API returns them; JS only formats.
import { api } from "../api.js?v=8";
import { currentUser } from "../auth.js?v=8";
import { icon, kindIcon } from "../icons.js?v=8";
import { hydratePhotos, titleFromUrl, wikiPhoto } from "../photos.js?v=8";
import { errorBox, esc, fmtDate, label, link, rs } from "../util.js?v=8";

const LEAFLET = {
  css: ["https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="],
  js: ["https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="],
};
const EDIT_EXAMPLES = ["Make the stay cheaper", "Remove the last stop on day 2", "Add a food stop on day 1"];
const WEATHER_ICON = (lbl) => /rain|drizzle|shower|thunder/i.test(lbl) ? "rain" : /cloud|overcast|fog/i.test(lbl) ? "cloud" : "sun";
let leafletReady = null;
let map = null;
let layer = null;

function loadLeaflet() {
  if (leafletReady) return leafletReady;
  leafletReady = new Promise((resolve, reject) => {
    const css = Object.assign(document.createElement("link"), { rel: "stylesheet", href: LEAFLET.css[0], integrity: LEAFLET.css[1], crossOrigin: "" });
    const js = Object.assign(document.createElement("script"), { src: LEAFLET.js[0], integrity: LEAFLET.js[1], crossOrigin: "" });
    js.onload = () => resolve(window.L);
    js.onerror = () => reject(new Error("The map couldn't load"));
    document.head.append(css, js);
  });
  return leafletReady;
}

const hm = (minutes) => (minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60 ? `${minutes % 60} min` : ""}` : `${minutes} min`).trim();
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

export function renderPlan(el, plan, opts = {}) {
  const places = Object.fromEntries(plan.places.map((p) => [p.id, p]));
  const weather = Object.fromEntries(plan.weather.map((w) => [w.date, w]));
  const b = plan.budget;
  const r = plan.request;
  const changedDates = new Set(opts.diff?.changed_dates ?? []);
  const added = new Set(opts.diff?.added ?? []);
  const nights = r.end_date === r.start_date ? 0 : plan.days.length - 1;
  el.innerHTML = `
    <div class="trip-hero">
      <div class="hero-img" id="trip-img" role="img" aria-label="${esc(plan.dest_geo.name)}"></div>
      <div class="container">
        <div>
          <span class="eyebrow" style="color:#ffd7c9">Your itinerary</span>
          <h1>${esc(r.destination)}</h1>
          <ul class="facts">
            <li>${icon("route")} From ${esc(r.origin)}</li>
            <li>${icon("calendar")} ${fmtDate(r.start_date)} – ${fmtDate(r.end_date)} · ${plural(plan.days.length, "day")}${nights ? `, ${plural(nights, "night")}` : ""}</li>
            <li>${icon("users")} ${plural(r.travellers, "traveller")}</li>
            ${r.interests.length ? `<li>${icon("compass")} ${r.interests.map(label).join(", ")}</li>` : ""}
          </ul>
        </div>
        <div class="summary-card">
          <div class="muted small">Estimated total</div>
          <div class="price">${rs(b.grand_total)}</div>
          <div class="small" style="margin:6px 0 2px"><span class="badge ${b.within_budget ? "within" : "over"}">${b.within_budget ? `${icon("check")} Within your budget` : "Over your budget"}</span></div>
          <div class="muted small">Budget ${rs(b.budget_inr)} · ${rs(b.per_person)} per person</div>
          ${opts.diff ? `<div class="small changed" style="margin-top:6px">Updated: ${rs(opts.diff.old_total)} → ${rs(opts.diff.new_total)}</div>` : ""}
          <div class="actions no-print">
            ${plan.id ? `<button type="button" id="edit-btn" class="btn btn-brand btn-sm" aria-expanded="false" aria-controls="refine">${icon("edit")} Refine trip</button>`
              : currentUser() ? `<button type="button" id="save-btn" class="btn btn-cta btn-sm">${icon("check")} Save this trip</button>`
              : `<button type="button" id="signin-save-btn" class="btn btn-cta btn-sm">${icon("user")} Sign in to save</button>`}
            <button type="button" id="print-btn" class="btn btn-sm">${icon("print")} Print / PDF</button>
          </div>
        </div>
      </div>
      <a class="photo-credit" id="trip-credit" target="_blank" rel="noopener" hidden>Photo: Wikimedia Commons</a>
    </div>

    <div class="container">
      ${plan.warnings.length ? `<div class="notice" role="status">${icon("info")}<div>${plan.warnings.map((w) => esc(w)).join("<br>")}</div></div>` : ""}
      ${!plan.id ? `<p class="muted small no-print" id="save-note" style="margin-top:12px">${currentUser() ? "This trip isn't saved yet. Save it to find it later in My trips and refine it." : "Sign in to save this trip and refine it later."}</p>` : ""}
      <div id="refine" class="card refine no-print" hidden style="margin-top:16px"></div>

      <div class="trip-layout">
        <div>
          <div class="day-tabs" role="tablist" aria-label="Trip days">
            ${plan.days.map((d, i) => `<button role="tab" id="tab-${i}" aria-controls="day-${i}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}"
              class="${changedDates.has(d.date) ? "changed" : ""}">Day ${i + 1}<small>${fmtDate(d.date)}</small></button>`).join("")}
          </div>
          ${plan.days.map((d, i) => dayPanel(d, i, places, weather[d.date], added, plan)).join("")}
        </div>

        <aside class="trip-side">
          <div id="map" role="region" aria-label="Map of the selected day"></div>
          <p class="muted tiny" style="margin:6px 2px 0">Numbers follow the day's order. Dashed lines are approximate routes.</p>
          ${transportCard(plan)}
          ${stayCard(plan)}
          ${weatherCard(plan)}
        </aside>
      </div>

      ${costCard(b)}

      <details class="card about" style="margin-top:16px">
        <summary>About this plan</summary>
        <p class="muted small" style="margin-top:12px">Places come from OpenStreetMap and Wikipedia, routes and distances from OSRM,
          and weather from Open-Meteo. Travel times are adjusted for local road conditions. Prices are estimates, not live quotes.</p>
        <ol class="sources">${plan.sources.filter((s) => s.url).map((s) => `<li>${link(s.url, s.name)}</li>`).join("")}</ol>
      </details>
    </div>`;

  el.querySelector("#print-btn").addEventListener("click", () => window.print());
  if (plan.id) setupEdit(el, plan, opts.onEdited);
  setupSave(el, plan, opts);
  setupTabs(el, plan, places);
  hydratePhotos(el);
  wikiPhoto(plan.dest_geo.name, 1280).then((p) => {
    if (!p) return;
    const box = el.querySelector("#trip-img");
    const img = new Image();
    img.onload = () => { box.style.backgroundImage = `url("${p.src}")`; box.classList.add("loaded"); };
    img.src = p.src;
    const credit = el.querySelector("#trip-credit");
    if (p.page) { credit.href = p.page; credit.hidden = false; }
  });
}

function stopMedia(p) {
  const title = titleFromUrl(p.wiki_url) || (p.source.name.startsWith("Wikipedia") ? titleFromUrl(p.source.url) : null);
  return `<div class="stop-media">${kindIcon(p.kind)}${title ? `<img data-wiki="${esc(title)}" data-width="330" alt="${esc(p.name)}">` : ""}</div>`;
}

function dayPanel(d, i, places, w, added, plan) {
  const meal = d.meals[0];
  let mealShown = false;
  const items = [];
  const travel = (leg, from) => leg ? `<div class="tl-travel">${icon(leg.km > 3 ? "car" : "route")} ${from}: ${leg.km} km · about ${hm(leg.minutes)}</div>` : "";
  const mealItem = () => `<li class="tl-item tl-meal"><span class="tl-dot">${icon("food")}</span><div class="tl-meal-card">${icon("food")} Lunch break · ${meal.start}–${meal.end}</div></li>`;
  d.stops.forEach((s, n) => {
    if (meal && !mealShown && meal.start <= s.arrive) { items.push(mealItem()); mealShown = true; }
    const p = places[s.place_id];
    items.push(`<li class="tl-item">
      ${travel(s.leg_from_prev, n === 0 ? "From your stay" : "Next")}
      <span class="tl-dot" aria-hidden="true">${n + 1}</span>
      <article class="stop-card ${added.has(s.place_id) ? "changed" : ""}">
        ${stopMedia(p)}
        <div>
          <div class="stop-time">${s.arrive} – ${s.depart}</div>
          <h3>${esc(p.name)}</h3>
          <div class="stop-kind">${label(p.kind)}</div>
          ${p.summary ? `<p class="stop-summary">${esc(p.summary)}</p>` : ""}
          <div class="stop-links">
            <span>${icon("clock")} ${p.opening_hours ? esc(p.opening_hours) : esc(s.hours_note || "Hours not listed")}</span>
            ${link(p.source.url, p.source.name.startsWith("Wikipedia") ? "Wikipedia" : "View on map")}
            ${p.wiki_url && !p.source.name.startsWith("Wikipedia") ? link(p.wiki_url, "Read more") : ""}
          </div>
        </div>
      </article></li>`);
  });
  if (meal && !mealShown && d.stops.length) items.push(mealItem());
  if (d.return_leg) items.push(`<li class="tl-item tl-home">${travel(d.return_leg, "Back to your stay")}<span class="tl-dot">${icon("bed")}</span></li>`);

  const skipped = d.notes.filter((n) => n.startsWith("Skipped "));
  const notes = d.notes.filter((n) => !n.startsWith("Skipped "));
  const wx = w ? `<span class="weather-pill">${icon(WEATHER_ICON(w.label))} ${esc(w.label)} · ${w.tmin_c ?? "–"}–${w.tmax_c ?? "–"}°C${w.precip_prob != null ? ` · ${w.precip_prob}% rain` : ""}${w.kind === "climate_normal" ? " · typical" : ""}</span>` : "";
  return `<section role="tabpanel" id="day-${i}" aria-labelledby="tab-${i}" class="day-panel card" ${i ? "hidden" : ""}>
    <div class="day-head"><h2>Day ${i + 1} · ${fmtDate(d.date)}</h2>${wx}</div>
    ${notes.map((n) => `<p class="day-note">${esc(n)}</p>`).join("")}
    ${d.stops.length ? `<ol class="timeline">${items.join("")}</ol>`
      : `<p class="muted">A free ${i === plan.days.length - 1 ? "morning before you head home" : "day to relax"}.</p>`}
    ${skipped.length ? `<details class="skipped-list"><summary>${plural(skipped.length, "place")} left out to keep the day comfortable</summary>
      <ul>${skipped.map((n) => `<li>${esc(n.replace(/^Skipped /, ""))}</li>`).join("")}</ul></details>` : ""}
  </section>`;
}

function transportCard(plan) {
  const chosen = plan.budget.transport_mode;
  if (!plan.intercity_options.length) return "";
  return `<div class="card side-card" style="margin-top:16px">
    <h3>${icon("bus")} Getting there</h3>
    ${plan.intercity_options.map((o) => `<div class="option ${o.mode === chosen ? "chosen" : ""}">
      <span class="mode">${label(o.mode)}</span><span class="price">${rs(o.total_inr)}</span>
      <span class="muted small">${o.km} km · about ${hm(o.minutes)} each way</span><span class="muted small" style="text-align:right">return</span>
    </div>`).join("")}
  </div>`;
}

function stayCard(plan) {
  const stay = plan.budget.lines.find((l) => l.category === "stay");
  if (!stay) return "";
  return `<div class="card side-card">
    <h3>${icon("bed")} Where you'll stay</h3>
    <div class="option"><span class="mode">${label(plan.budget.stay_style)} stay in ${esc(plan.dest_geo.name)}</span><span class="price">${rs(stay.total)}</span>
      <span class="muted small" style="grid-column:1/-1">${esc(stay.description)}</span></div>
  </div>`;
}

function weatherCard(plan) {
  if (!plan.weather.length) return "";
  return `<div class="card side-card">
    <h3>${icon("sun")} Weather</h3>
    ${plan.weather.map((w) => `<div class="wx-row"><span>${fmtDate(w.date)}</span>
      <span>${icon(WEATHER_ICON(w.label))} ${esc(w.label)} · ${w.tmin_c ?? "–"}–${w.tmax_c ?? "–"}°C</span></div>`).join("")}
    ${plan.weather.some((w) => w.kind === "climate_normal") ? `<p class="muted tiny" style="margin:8px 0 0">"Typical" days use recent years' weather, not a forecast.</p>` : ""}
  </div>`;
}

function costCard(b) {
  // Stacked bar: one segment per line, sized by flex-grow = line total (no arithmetic in JS).
  const cats = [...new Set(b.lines.filter((l) => l.total > 0).map((l) => l.category))];
  const names = { transport: "Travel", stay: "Stay", food: "Food", local_transport: "Getting around", activities: "Entry tickets" };
  return `<section class="card" style="margin-top:20px" aria-labelledby="cost-title">
    <div class="day-head"><h2 id="cost-title">Cost breakdown</h2><span class="muted small">Estimated for all travellers</span></div>
    <div class="cost-bar" role="img" aria-label="Share of each cost">
      ${b.lines.filter((l) => l.total > 0).map((l) => `<span class="cat-${esc(l.category)}" style="flex-grow:${l.total}" title="${esc(l.description)}: ${rs(l.total)}"></span>`).join("")}
    </div>
    <div class="legend">${cats.map((c) => `<span><i class="cat-${esc(c)}"></i>${names[c] || label(c)}</span>`).join("")}</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Item</th><th class="num">Price</th><th class="num">Qty</th><th class="num">Total</th></tr></thead>
      <tbody>${b.lines.map((l) => `<tr>
        <td>${esc(l.description)}${l.source.url ? ` <span class="small">${link(l.source.url, "source")}</span>` : ""}${l.confidence === "unavailable" ? ` <span class="badge unavailable">not available</span>` : ""}</td>
        <td class="num">${rs(l.unit_cost)}</td><td class="num">${l.qty}</td><td class="num">${rs(l.total)}</td></tr>`).join("")}</tbody>
      <tfoot><tr><th colspan="3">Estimated total</th><th class="num">${rs(b.grand_total)}</th></tr></tfoot>
    </table></div>
    ${b.suggestions.length ? `<div class="callout warn" style="margin-top:12px">${b.suggestions.map((s) => `<p style="margin:0">${esc(s)}</p>`).join("")}</div>` : ""}
    ${b.include_paid_activities ? "" : `<p class="muted small" style="margin:10px 0 0">Some paid entries were left out to keep you within budget.</p>`}
  </section>`;
}

function setupTabs(el, plan, places) {
  const tabs = [...el.querySelectorAll('.day-tabs [role="tab"]')];
  const select = (i, focus) => {
    tabs.forEach((t, j) => {
      t.setAttribute("aria-selected", String(i === j));
      t.tabIndex = i === j ? 0 : -1;
      el.querySelector(`#day-${j}`).hidden = i !== j;
    });
    if (focus) tabs[i].focus();
    drawMap(el, plan, plan.days[i], places);
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(i));
    t.addEventListener("keydown", (e) => {
      const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (k) { e.preventDefault(); select((i + k + tabs.length) % tabs.length, true); }
      if (e.key === "Home") { e.preventDefault(); select(0, true); }
      if (e.key === "End") { e.preventDefault(); select(tabs.length - 1, true); }
    });
  });
  select(0);
}

async function drawMap(el, plan, day, places) {
  const box = el.querySelector("#map");
  let L;
  try { L = await loadLeaflet(); } catch (err) { box.innerHTML = errorBox({ title: err.message }); return; }
  if (!map || map.getContainer() !== box) {
    map?.remove();
    map = L.map(box, { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  layer.clearLayers();
  const c = plan.dest_geo;
  const pts = [[c.lat, c.lon]];
  const color = getComputedStyle(document.documentElement).getPropertyValue("--brand").trim() || "#0b6b63";
  L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "home-marker", html: "⌂", iconSize: [26, 26] }), title: "Your stay" })
    .bindPopup(`Your stay: ${esc(c.name)}`).addTo(layer);
  for (const leg of [...day.stops.map((s) => s.leg_from_prev), day.return_leg].filter(Boolean)) {
    if (leg.geometry) {
      L.polyline(leg.geometry.map(([lon, lat]) => [lat, lon]), {
        color, weight: 4, opacity: 0.85, dashArray: leg.source.url ? null : "6 8",
      }).addTo(layer);
    }
  }
  day.stops.forEach((s, n) => {
    const p = places[s.place_id];
    pts.push([p.lat, p.lon]);
    L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "num-marker", html: String(n + 1), iconSize: [28, 28] }), title: p.name })
      .bindPopup(`<strong>${n + 1}. ${esc(p.name)}</strong><br>${s.arrive}–${s.depart}`)
      .addTo(layer);
  });
  map.invalidateSize();
  if (pts.length > 1) map.fitBounds(pts, { padding: [30, 30] }); else map.setView(pts[0], 12);
}

function setupEdit(el, plan, onEdited) {
  const panel = el.querySelector("#refine");
  const btn = el.querySelector("#edit-btn");
  btn.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    btn.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) panel.querySelector("#edit-input").focus();
  });
  panel.innerHTML = `<h3 style="display:flex;gap:8px;align-items:center">${icon("edit")} Refine your trip</h3>
    <form id="edit-form">
      <label for="edit-input">What would you like to change?</label>
      <input id="edit-input" maxlength="300" placeholder="e.g. swap the second stop on day 1">
      <div class="chips" style="margin:12px 0">${EDIT_EXAMPLES.map((x) => `<button type="button" class="chip" data-edit="${esc(x)}">${esc(x)}</button>`).join("")}</div>
      <div class="search-actions"><button class="btn btn-cta btn-sm" type="submit">Update trip</button><span id="edit-status" class="muted small" aria-live="polite"></span></div>
      <div id="edit-msg" style="margin-top:12px"></div>
    </form>`;
  panel.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    panel.querySelector("#edit-input").value = b.dataset.edit;
  }));
  panel.querySelector("#edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = panel.querySelector("#edit-input").value.trim();
    if (text.length < 3) return;
    const status = panel.querySelector("#edit-status");
    status.innerHTML = `<span class="spinner" aria-hidden="true"></span> Updating…`;
    panel.querySelector("#edit-msg").innerHTML = "";
    try {
      const res = await api.editPlan(plan.id, text);
      status.textContent = "";
      if (res.needs_clarification) {
        panel.querySelector("#edit-msg").innerHTML = `<div class="callout info">${esc(res.question)}</div>`;
        return;
      }
      onEdited?.(res.plan, res.diff);
    } catch (err) {
      status.textContent = "";
      panel.querySelector("#edit-msg").innerHTML = errorBox(err);
    }
  });
}

function setupSave(el, plan, opts) {
  el.querySelector("#signin-save-btn")?.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the sign-in menu open (a page click would close it)
    opts.onSignIn?.();
  });
  const btn = el.querySelector("#save-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" aria-hidden="true"></span> Saving…`;
    try {
      opts.onSaved?.(await api.savePlan(plan));
    } catch (err) {
      btn.disabled = false;
      btn.innerHTML = `${icon("check")} Save this trip`;
      el.querySelector("#save-note").innerHTML = errorBox(err);
    }
  });
}
