// Evaluation page: loads web/data/report.json (written by `python -m eval.report`).
import { errorBox, esc, label } from "../util.js?v=8";

const UNITS = { latency_s: "seconds", total_tokens: "tokens", llm_calls: "calls" };

function chart(metric, g, b) {
  // SVG coordinates are presentation; the values themselves come straight from report.json.
  const max = Math.max(g?.ci95[1] ?? 0, b?.ci95[1] ?? 0, g?.mean ?? 0, b?.mean ?? 0) || 1;
  const W = 520, H = 150, left = 90, right = 70, bar = 34;
  const x = (v) => left + (v / max) * (W - left - right);
  const row = (s, cls, y, name) => s ? `
    <text x="${left - 8}" y="${y + bar / 2 + 4}" text-anchor="end">${name}</text>
    <rect class="${cls}" x="${left}" y="${y}" width="${Math.max(1, x(s.mean) - left)}" height="${bar}" rx="4"></rect>
    <line class="err" x1="${x(s.ci95[0])}" x2="${x(s.ci95[1])}" y1="${y + bar / 2}" y2="${y + bar / 2}"></line>
    <line class="err" x1="${x(s.ci95[0])}" x2="${x(s.ci95[0])}" y1="${y + 8}" y2="${y + bar - 8}"></line>
    <line class="err" x1="${x(s.ci95[1])}" x2="${x(s.ci95[1])}" y1="${y + 8}" y2="${y + bar - 8}"></line>
    <text x="${x(Math.max(s.mean, s.ci95[1])) + 6}" y="${y + bar / 2 + 4}">${s.mean}</text>` : "";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label(metric))}: graph ${g?.mean ?? "n/a"}, baseline ${b?.mean ?? "n/a"}">
    ${row(g, "bar-graph", 14, "Multi-agent")}${row(b, "bar-baseline", 64, "Baseline")}
    <line class="axis" x1="${left}" x2="${W - right}" y1="${H - 26}" y2="${H - 26}"></line>
    <text x="${left}" y="${H - 8}">0</text><text x="${W - right}" y="${H - 8}" text-anchor="end">${+max.toFixed(3)} ${UNITS[metric] || "(share)"}</text>
  </svg>`;
}

export async function renderEval(el) {
  el.innerHTML = `<h1>Evaluation: single agent vs multi-agent</h1><p class="muted"><span class="spinner"></span> Loading report…</p>`;
  let rep;
  try {
    const res = await fetch("data/report.json", { cache: "no-store" });
    if (!res.ok) throw new Error("not found");
    rep = await res.json();
  } catch {
    el.innerHTML = `<h1>Evaluation</h1>${errorBox({ title: "No report yet", detail: "Run python -m eval.run --system both, then python -m eval.report." })}`;
    return;
  }
  const metrics = Object.keys(rep.notes);
  const fmt = (s) => s ? `${s.mean} <span class="muted small">[${s.ci95[0]}, ${s.ci95[1]}]</span>` : "n/a";
  el.innerHTML = `<h1>Evaluation: single agent vs multi-agent</h1>
    <p class="muted">Cases: multi-agent ${rep.cases.graph}, baseline ${rep.cases.baseline}. Means with 95% bootstrap confidence intervals.</p>
    <div class="card table-wrap"><table>
      <thead><tr><th>Metric</th><th>Multi-agent mean [95% CI]</th><th class="num">Median</th><th>Baseline mean [95% CI]</th><th class="num">Median</th><th>Paired diff [95% CI]</th></tr></thead>
      <tbody>${metrics.map((m) => {
        const g = rep.systems.graph[m], b = rep.systems.baseline[m], p = rep.paired_graph_minus_baseline[m];
        return `<tr><td><strong>${label(m)}</strong><br><span class="small muted">${esc(rep.notes[m])}</span></td>
          <td>${fmt(g)}</td><td class="num">${g?.median ?? "n/a"}</td><td>${fmt(b)}</td><td class="num">${b?.median ?? "n/a"}</td>
          <td>${p ? `${p.mean_diff} <span class="muted small">[${p.ci95[0]}, ${p.ci95[1]}]</span>` : "n/a"}</td></tr>`;
      }).join("")}</tbody></table></div>
    <div class="grid-2" style="margin-top:16px">${metrics.map((m) => `<figure class="card" style="margin:0">
      <figcaption><strong>${label(m)}</strong> <span class="muted small">${esc(rep.notes[m])}</span></figcaption>
      ${chart(m, rep.systems.graph[m], rep.systems.baseline[m])}</figure>`).join("")}</div>`;
}
