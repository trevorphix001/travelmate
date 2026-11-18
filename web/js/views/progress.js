import { api } from "../api.js?v=8";
import { icon } from "../icons.js?v=8";
import { esc, errorBox } from "../util.js?v=8";

// Backend node names -> what the traveller sees.
const STEPS = [
  ["supervisor", "Understanding your trip"],
  ["geo", "Locating your destination"],
  ["research", "Finding the best places to visit"],
  ["weather", "Checking the weather"],
  ["transport", "Comparing ways to get there"],
  ["itinerary", "Arranging your days"],
  ["budget", "Estimating your costs"],
  ["aggregator", "Final checks"],
];

let controller = null;

export function runProgress(el, body, onDone, onBack) {
  controller?.abort();
  controller = new AbortController();
  const r = body.request || {};
  el.innerHTML = `
    <div class="progress-wrap">
      <div class="progress-card" role="status" aria-live="polite">
        <span class="eyebrow">Planning your trip</span>
        <div class="progress-route">
          <span>${esc(r.origin || "Your trip")}</span><span class="line" aria-hidden="true"></span><span>${esc(r.destination || "")}</span>
        </div>
        <p class="muted small" id="p-sub">This usually takes under a minute.</p>
        <div class="bar" aria-hidden="true"><span id="p-bar"></span></div>
        <ol class="steps">
          ${STEPS.map(([id, text], i) => `<li data-node="${id}" class="${i === 0 ? "active" : ""}"><span class="dot" aria-hidden="true"></span>${text}</li>`).join("")}
        </ol>
        <div id="p-error"></div>
        <div style="display:flex;justify-content:flex-end"><button type="button" class="btn btn-ghost btn-sm" id="cancel">Cancel</button></div>
      </div>
    </div>`;
  const done = new Set();
  const setBar = () => { el.querySelector("#p-bar").style.width = `${Math.round((done.size / STEPS.length) * 100)}%`; };
  el.querySelector("#cancel").addEventListener("click", () => { controller.abort(); onBack(); });

  const mark = (node, status = "done") => {
    const li = el.querySelector(`[data-node="${node}"]`);
    if (!li) return;
    li.classList.remove("active");
    li.classList.add("done", status);
    li.querySelector(".dot").innerHTML = status === "failed" ? "!" : status === "skipped" ? "" : icon("check");
    done.add(node);
    setBar();
    el.querySelectorAll(".steps li.active").forEach((x) => x.classList.remove("active"));
    el.querySelector(".steps li:not(.done)")?.classList.add("active");
  };

  api.streamPlan(body, (event, data) => {
    if (event === "node") mark(data.node, data.status);
    else if (event === "done") {
      done.clear(); STEPS.forEach(([id]) => done.add(id)); setBar();
      onDone(data);
    } else if (event === "error") {
      el.querySelectorAll(".steps li.active").forEach((x) => x.classList.remove("active"));
      el.querySelector("#p-sub").textContent = "We couldn't finish this plan.";
      el.querySelector("#p-error").innerHTML = errorBox(data) +
        `<div style="margin-top:12px"><button type="button" id="back" class="btn btn-brand btn-sm">Back to search</button></div>`;
      el.querySelector("#back").addEventListener("click", onBack);
    }
  }, controller.signal).catch((err) => {
    if (err.name !== "AbortError") el.querySelector("#p-error").innerHTML = errorBox({ title: "Connection lost", detail: err.message });
  });
}
