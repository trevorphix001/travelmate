// Home: hero + trip search, destinations, travel styles, trip ideas. Uses the same API calls as before
// (/plans/parse, /places/search, /plans/stream via the generate callback).
import { api } from "../api.js?v=9";
import { icon } from "../icons.js?v=9";
import { hydratePhotos, wikiPhoto } from "../photos.js?v=9";
import { errorBox, esc } from "../util.js?v=9";

// Interest and pace values are the TripRequest enums in web/openapi.json.
const STYLES = [
  ["nature", "Nature", "Hills, parks and viewpoints", "leaf"],
  ["waterfalls", "Waterfalls", "Falls and scenic spots", "water"],
  ["heritage", "Heritage", "Forts, palaces and museums", "landmark"],
  ["food", "Food", "Local cafés and restaurants", "food"],
  ["adventure", "Adventure", "Peaks, caves and trails", "flag"],
  ["religious", "Spiritual", "Temples, churches, monasteries", "temple"],
  ["shopping", "Shopping", "Markets and malls", "bag"],
  ["nightlife", "Nightlife", "Bars and live music", "moon"],
];
const PACES = [["relaxed", "Relaxed"], ["normal", "Balanced"], ["packed", "Packed"]];
const MAX_DAYS = 7;

// Featured destinations: names and states as in data/cities.csv; photo = the city's Wikipedia lead image.
const DESTINATIONS = [
  ["Shillong", "Meghalaya", "Shillong", ["waterfalls", "nature"]],
  ["Gangtok", "Sikkim", "Gangtok", ["nature", "religious"]],
  ["Darjeeling", "West Bengal", "Darjeeling", ["nature"]],
  ["Jaipur", "Rajasthan", "Jaipur", ["heritage", "shopping"]],
  ["Udaipur", "Rajasthan", "Udaipur", ["heritage"]],
  ["Munnar", "Kerala", "Munnar", ["nature"]],
  ["Leh", "Ladakh", "Leh", ["adventure", "nature"]],
  ["Hampi", "Karnataka", "Hampi", ["heritage"]],
];

// Example searches people can start from. The budget is the traveller's input, not a price we quote.
const IDEAS = [
  { title: "Meghalaya waterfalls", photo: "Nohkalikai_Falls", origin: "Guwahati", destination: "Shillong", days: 3, travellers: 2,
    budget: 8000, interests: ["waterfalls", "nature"] },
  { title: "Pink City heritage break", photo: "Hawa_Mahal", origin: "Delhi", destination: "Jaipur", days: 3, travellers: 2,
    budget: 14000, interests: ["heritage", "food"] },
  { title: "Tea hills of Munnar", photo: "Munnar", origin: "Kochi", destination: "Munnar", days: 2, travellers: 2,
    budget: 9000, interests: ["nature"] },
  { title: "Ruins of Hampi", photo: "Hampi", origin: "Bengaluru", destination: "Hampi", days: 3, travellers: 2,
    budget: 12000, interests: ["heritage"] },
];
const EXAMPLES = [
  "3 days from Guwahati to Shillong for 2 people starting next Friday, budget Rs 8000, waterfalls and nature",
  "Weekend in Jaipur from Delhi for 4 friends, budget 20000, heritage and food",
  "Solo 2-day trip to Rishikesh from Delhi, Rs 6000, adventure",
];
const HERO_PHOTO = "Nohkalikai_Falls";

let onGenerate = () => {};
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d; };

export function initHome(el, generate) {
  onGenerate = generate;
  el.innerHTML = `
    <div class="hero">
      <div class="hero-img" id="hero-img" role="img" aria-label="Nohkalikai Falls, Meghalaya"></div>
      <div class="container hero-inner">
        <span class="eyebrow" style="color:#ffd7c9">Plan smarter trips across India</span>
        <h1>Your next getaway, planned day by day</h1>
        <p class="hero-sub">Real places, honest travel times and a clear cost estimate, all built around your dates, budget and travel style.</p>
        ${searchCard()}
      </div>
      <a class="photo-credit" id="hero-credit" href="https://en.wikipedia.org/wiki/${HERO_PHOTO}" target="_blank" rel="noopener">Photo: Wikimedia Commons</a>
    </div>

    <div class="container">
      <section class="section" id="destinations" aria-labelledby="dest-title">
        <div class="section-head reveal">
          <div><span class="eyebrow">Popular destinations</span><h2 id="dest-title">Where would you like to go?</h2></div>
          <p>Pick a destination and we'll suggest what to see, how to get there and what it may cost.</p>
        </div>
        <div class="dest-grid">
          ${DESTINATIONS.map(([name, state, wiki], i) => `
            <button type="button" class="dest-card reveal" data-dest="${i}" aria-label="Plan a trip to ${esc(name)}, ${esc(state)}">
              <div class="img-fallback"></div>
              <img data-wiki="${esc(wiki)}" data-width="500" alt="">
              <span class="tag">${esc(state)}</span>
              <div class="body"><h3>${esc(name)}</h3><p class="meta">Plan a trip ${icon("arrow")}</p></div>
            </button>`).join("")}
        </div>
      </section>

      <section class="section" aria-labelledby="style-title">
        <div class="section-head reveal">
          <div><span class="eyebrow">Travel your way</span><h2 id="style-title">Explore by travel style</h2></div>
          <p>Choose what you love; your itinerary will favour places that match.</p>
        </div>
        <div class="cat-grid">
          ${STYLES.map(([id, name, desc, ic]) => `
            <button type="button" class="cat-tile reveal" data-style="${id}">
              <span class="cat-icon">${icon(ic)}</span><strong>${name}</strong><span>${desc}</span>
            </button>`).join("")}
        </div>
      </section>

      <section class="section" id="ideas" aria-labelledby="ideas-title">
        <div class="section-head reveal">
          <div><span class="eyebrow">Trip ideas</span><h2 id="ideas-title">Short breaks to get you started</h2></div>
          <p>Start from an idea and adjust the dates, travellers and budget to suit you.</p>
        </div>
        <div class="ideas-grid">
          ${IDEAS.map((t, i) => `
            <article class="idea-card reveal">
              <div class="idea-media"><div class="img-fallback"></div><img data-wiki="${esc(t.photo)}" data-width="500" alt=""></div>
              <div class="idea-body">
                <h3>${esc(t.title)}</h3>
                <ul class="facts">
                  <li>${icon("route")} ${esc(t.origin)} → ${esc(t.destination)}</li>
                  <li>${icon("calendar")} ${t.days} days</li>
                  <li>${icon("users")} ${t.travellers} travellers</li>
                  <li>${icon("wallet")} Budget Rs ${t.budget.toLocaleString("en-IN")}</li>
                </ul>
                <button type="button" class="btn btn-sm" data-idea="${i}">Use this idea ${icon("arrow")}</button>
              </div>
            </article>`).join("")}
        </div>
      </section>

      <section class="section" aria-labelledby="why-title">
        <div class="section-head reveal"><div><span class="eyebrow">Why TravelMate</span><h2 id="why-title">Plans you can actually follow</h2></div></div>
        <div class="why-grid">
          <div class="why reveal"><span class="cat-icon">${icon("pin")}</span><h3>Real places</h3>
            <p class="muted">Every stop is a real, mapped place with a link to where its details come from.</p></div>
          <div class="why reveal"><span class="cat-icon">${icon("clock")}</span><h3>Realistic days</h3>
            <p class="muted">Travel times reflect local roads, and each day keeps driving to a comfortable limit.</p></div>
          <div class="why reveal"><span class="cat-icon">${icon("wallet")}</span><h3>Clear costs</h3>
            <p class="muted">See travel, stay, food and entry costs line by line, checked against your budget.</p></div>
          <div class="why reveal"><span class="cat-icon">${icon("sun")}</span><h3>Weather-aware</h3>
            <p class="muted">Outdoor spots are planned for drier days using the latest forecast.</p></div>
        </div>
        <div class="cta-band reveal">
          <div><h2>Ready when you are</h2><p>Tell us where and when; we'll take care of the day-by-day plan.</p></div>
          <button type="button" class="btn btn-cta" id="cta-plan">${icon("search")} Start planning</button>
        </div>
      </section>
    </div>`;

  wireSearch(el);
  hydratePhotos(el);
  wikiPhoto(HERO_PHOTO, 1280).then((p) => {
    if (!p) return;
    const box = el.querySelector("#hero-img");
    const img = new Image();
    img.onload = () => { box.style.backgroundImage = `url("${p.src}")`; box.classList.add("loaded"); };
    img.src = p.src;
    if (p.page) el.querySelector("#hero-credit").href = p.page;
  });
  observeReveal(el);
}

function searchCard() {
  const start = addDays(14);
  const end = addDays(16);
  return `
  <div class="search-card" id="search">
    <div class="search-tabs" role="tablist" aria-label="How do you want to plan?">
      <button type="button" role="tab" id="tab-form" aria-controls="panel-form" aria-selected="true">${icon("search")} Search trips</button>
      <button type="button" role="tab" id="tab-describe" aria-controls="panel-describe" aria-selected="false" tabindex="-1">${icon("spark")} Describe your trip</button>
    </div>

    <form id="panel-form" role="tabpanel" aria-labelledby="tab-form" novalidate>
      <div class="search-grid">
        <div class="field"><label for="f-origin">From</label>
          <div class="field-icon">${icon("pin")}<input id="f-origin" list="cities" autocomplete="off" placeholder="Leaving from" maxlength="80"></div></div>
        <div class="field"><label for="f-dest">To</label>
          <div class="field-icon">${icon("compass")}<input id="f-dest" list="cities" autocomplete="off" placeholder="Where to?" maxlength="80"></div></div>
        <div class="field"><label for="f-start">Start date</label>
          <input id="f-start" type="date" value="${iso(start)}" min="${iso(new Date())}"></div>
        <div class="field"><label for="f-end">End date</label>
          <input id="f-end" type="date" value="${iso(end)}" min="${iso(new Date())}"></div>
        <div class="field"><label for="f-trav">Travellers</label>
          <div class="field-icon">${icon("users")}<input id="f-trav" type="number" min="1" max="10" value="2" inputmode="numeric"></div></div>
        <div class="field"><label for="f-budget">Total budget (Rs)</label>
          <div class="field-icon">${icon("wallet")}<input id="f-budget" type="number" min="1" step="500" placeholder="e.g. 15000" inputmode="numeric"></div></div>
      </div>
      <datalist id="cities"></datalist>
      <div class="search-extras">
        <div class="prefs"><span class="label" id="prefs-label">Travel style</span>
          <div class="chips" role="group" aria-labelledby="prefs-label">
            ${STYLES.map(([id, name]) => `<button type="button" class="chip" aria-pressed="false" data-int="${id}">${name}</button>`).join("")}
          </div></div>
        <div class="pace"><label for="f-pace">Pace</label>
          <select id="f-pace">${PACES.map(([v, t]) => `<option value="${v}" ${v === "normal" ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="search-actions"><button class="btn btn-cta" type="submit">${icon("search")} Plan my trip</button></div>
      </div>
      <div id="form-errors" class="error-text" role="alert"></div>
    </form>

    <form id="panel-describe" role="tabpanel" aria-labelledby="tab-describe" class="describe-box" hidden novalidate>
      <label for="query">Tell us about your trip <span class="ai-tag">${icon("spark")} Plan with TravelMate</span></label>
      <textarea id="query" maxlength="300" placeholder="e.g. ${esc(EXAMPLES[0])}"></textarea>
      <div class="chips describe-examples" aria-label="Examples">
        ${EXAMPLES.map((x, i) => `<button type="button" class="chip" data-ex="${i}">${esc(x.split(",")[0])}</button>`).join("")}
      </div>
      <div class="search-actions"><button class="btn btn-cta" type="submit">${icon("spark")} Plan with TravelMate</button>
        <span id="parse-status" class="muted small" aria-live="polite"></span></div>
      <div id="clarify" class="inline-question" hidden></div>
    </form>
  </div>`;
}

function wireSearch(el) {
  const $ = (s) => el.querySelector(s);
  const tabs = [$("#tab-form"), $("#tab-describe")];
  const showTab = (i, focus) => {
    tabs.forEach((t, j) => { t.setAttribute("aria-selected", String(i === j)); t.tabIndex = i === j ? 0 : -1; });
    $("#panel-form").hidden = i !== 0;
    $("#panel-describe").hidden = i !== 1;
    if (focus) tabs[i].focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => showTab(i));
    t.addEventListener("keydown", (e) => { if (["ArrowRight", "ArrowLeft"].includes(e.key)) { e.preventDefault(); showTab(1 - i, true); } });
  });

  const togglePref = (id, on) => {
    const b = el.querySelector(`[data-int="${id}"]`);
    if (b) b.setAttribute("aria-pressed", String(on ?? b.getAttribute("aria-pressed") !== "true"));
  };
  el.querySelectorAll("[data-int]").forEach((b) => b.addEventListener("click", () => togglePref(b.dataset.int)));
  el.querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
    $("#query").value = EXAMPLES[Number(b.dataset.ex)];
    $("#query").focus();
  }));

  const toSearch = (focusSel) => {
    showTab(0);
    $("#search").scrollIntoView({ block: "center" });
    setTimeout(() => $(focusSel)?.focus({ preventScroll: true }), 350);
  };
  el.querySelectorAll("[data-dest]").forEach((b) => b.addEventListener("click", () => {
    const [name, , , interests] = DESTINATIONS[Number(b.dataset.dest)];
    $("#f-dest").value = name;
    STYLES.forEach(([id]) => togglePref(id, interests.includes(id)));
    toSearch($("#f-origin").value ? "#f-budget" : "#f-origin");
  }));
  el.querySelectorAll("[data-style]").forEach((b) => b.addEventListener("click", () => {
    togglePref(b.dataset.style, true);
    toSearch("#f-dest");
  }));
  el.querySelectorAll("[data-idea]").forEach((b) => b.addEventListener("click", () => {
    const t = IDEAS[Number(b.dataset.idea)];
    $("#f-origin").value = t.origin;
    $("#f-dest").value = t.destination;
    $("#f-start").value = iso(addDays(14));
    $("#f-end").value = iso(addDays(14 + t.days - 1));
    $("#f-trav").value = t.travellers;
    $("#f-budget").value = t.budget;
    STYLES.forEach(([id]) => togglePref(id, t.interests.includes(id)));
    toSearch("#panel-form .btn-cta");
  }));
  $("#cta-plan").addEventListener("click", () => toSearch("#f-origin"));

  let timer;
  for (const id of ["#f-origin", "#f-dest"]) {
    $(id).addEventListener("input", (e) => {
      clearTimeout(timer);
      const q = e.target.value.trim();
      if (q.length < 2) return;
      timer = setTimeout(async () => {
        try {
          const cities = await api.searchPlaces(q);
          $("#cities").innerHTML = cities.map((c) => `<option value="${esc(c.name)}">${esc(c.state)}</option>`).join("");
        } catch { /* suggestions are optional */ }
      }, 250);
    });
  }

  $("#panel-form").addEventListener("submit", (e) => { e.preventDefault(); submitForm(); });
  $("#panel-describe").addEventListener("submit", (e) => { e.preventDefault(); parse($("#query").value); });

  async function parse(text, base = "") {
    const query = (base ? `${base}. ${text}` : text).trim().slice(0, 300);
    if (query.length < 3) { $("#parse-status").textContent = "Tell us a little about your trip first."; return; }
    $("#parse-status").innerHTML = `<span class="spinner" aria-hidden="true"></span> Reading your trip…`;
    $("#clarify").hidden = true;
    try {
      const res = await api.parse(query);
      $("#parse-status").textContent = "";
      if (res.needs_clarification) return askClarification(res.question, query);
      fillForm(res);
    } catch (err) {
      $("#parse-status").textContent = "";
      $("#clarify").hidden = false;
      $("#clarify").innerHTML = errorBox(err);
    }
  }

  function askClarification(question, base) {
    const box = $("#clarify");
    box.hidden = false;
    box.innerHTML = `<div class="callout info"><p><strong>${esc(question)}</strong></p>
      <label for="reply">Your answer</label><input id="reply" maxlength="150">
      <div class="search-actions" style="margin-top:10px"><button type="button" id="reply-btn" class="btn btn-brand btn-sm">Continue</button></div></div>`;
    const send = () => parse($("#reply").value, base);
    $("#reply-btn").addEventListener("click", send);
    $("#reply").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
    $("#reply").focus();
  }

  function fillForm(r) {
    $("#f-origin").value = r.origin; $("#f-dest").value = r.destination;
    $("#f-start").value = r.start_date; $("#f-end").value = r.end_date;
    $("#f-trav").value = r.travellers; $("#f-budget").value = r.budget_inr; $("#f-pace").value = r.pace;
    STYLES.forEach(([id]) => togglePref(id, r.interests.includes(id)));
    showTab(0);
    $("#form-errors").textContent = "";
    $("#panel-form .btn-cta").focus();
    $("#panel-form .search-actions").insertAdjacentHTML("beforeend",
      `<span class="muted small" id="filled-note">We've filled in your trip. Check the details, then plan.</span>`);
  }

  // Client-side checks mirror the TripRequest constraints in openapi.json; the server re-validates.
  function submitForm() {
    const req = {
      origin: $("#f-origin").value.trim(), destination: $("#f-dest").value.trim(),
      start_date: $("#f-start").value, end_date: $("#f-end").value,
      travellers: Number($("#f-trav").value), budget_inr: Number($("#f-budget").value),
      interests: [...el.querySelectorAll('[data-int][aria-pressed="true"]')].map((b) => b.dataset.int),
      pace: $("#f-pace").value,
    };
    const errs = [];
    if (req.origin.length < 2) errs.push("Where are you leaving from?");
    if (req.destination.length < 2) errs.push("Where would you like to go?");
    if (!req.start_date || !req.end_date) errs.push("Pick your travel dates.");
    else {
      const days = (Date.parse(req.end_date) - Date.parse(req.start_date)) / 86400000 + 1;
      if (req.start_date < iso(new Date())) errs.push("The start date is in the past.");
      if (days < 1) errs.push("The end date must be on or after the start date.");
      if (days > MAX_DAYS) errs.push(`Trips can be up to ${MAX_DAYS} days.`);
    }
    if (!(req.travellers >= 1 && req.travellers <= 10)) errs.push("Travellers must be between 1 and 10.");
    if (!(req.budget_inr > 0)) errs.push("Add your total budget.");
    $("#form-errors").textContent = errs.join(" ");
    if (!errs.length) onGenerate({ request: req });
  }
}

function observeReveal(root) {
  const items = root.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) { items.forEach((x) => x.classList.add("in")); return; }
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { rootMargin: "0px 0px -40px 0px" });
  items.forEach((x) => io.observe(x));
}

export function scrollToSection(el, id) {
  const target = el.querySelector(`#${id}`);
  if (target) setTimeout(() => target.scrollIntoView({ block: "start" }), 50);
}
