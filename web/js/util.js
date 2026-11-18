// Formatting and DOM helpers only: no business logic.
const inr = new Intl.NumberFormat("en-IN");
export const rs = (n) => `Rs ${inr.format(n)}`;
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
export const label = (s) => String(s ?? "").replaceAll("_", " ");
export function link(url, text) {
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
}
export function errorBox(err) {
  return `<div class="callout error" role="alert"><strong>${esc(err.title || "Something went wrong")}</strong>
    ${err.detail ? `<div>${esc(err.detail)}</div>` : ""}</div>`;
}
