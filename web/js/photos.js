// Real destination photos from Wikipedia / Wikimedia Commons (lead image of the article), fetched in the browser.
// Each photo links back to its article, where the author and licence are listed.
const API = "https://en.wikipedia.org/api/rest_v1/page/summary/";
const memo = new Map();

function readCache(key) {
  try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; }
}
function writeCache(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* storage may be unavailable */ }
}

// Wikimedia only serves these standard thumbnail widths (others return HTTP 400).
const BUCKETS = [330, 500, 960, 1280, 1920];

/** Thumbnail URL at the smallest standard width >= the one needed (or the original if it is smaller). */
function sized(d, width) {
  const want = BUCKETS.find((b) => b >= width) || BUCKETS[BUCKETS.length - 1];
  const orig = d.originalimage;
  if (orig?.width && orig.width <= want) return orig.source;
  return d.thumbnail.source.replace(/\/\d+px-([^/?]+)/, `/${want}px-$1`);
}

/** {src, page, description} for an English Wikipedia article title, or null. Never throws. */
export async function wikiPhoto(title, width = 800) {
  if (!title) return null;
  const key = `photo2:${title}:${width}`; // v2: standard-width URLs
  if (memo.has(key)) return memo.get(key);
  const cached = readCache(key);
  if (cached !== null) { memo.set(key, Promise.resolve(cached.src ? cached : null)); return memo.get(key); }
  const job = fetch(API + encodeURIComponent(title.replaceAll(" ", "_")))
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const thumb = d?.thumbnail?.source;
      const out = d && d.type !== "disambiguation" && thumb
        ? { src: sized(d, width), page: d.content_urls?.desktop?.page, description: d.description || "" }
        : { src: null };
      writeCache(key, out);
      return out.src ? out : null;
    })
    .catch(() => null);
  memo.set(key, job);
  return job;
}

/** Article title from a Wikipedia page URL (en.wikipedia.org only). */
export function titleFromUrl(url) {
  const m = /^https:\/\/en\.wikipedia\.org\/wiki\/([^#?]+)/.exec(url || "");
  return m ? decodeURIComponent(m[1]) : null;
}

/** Fill every <img data-wiki="Title"> inside root once its photo is known; hide the fallback when it loads. */
export function hydratePhotos(root) {
  root.querySelectorAll("img[data-wiki]").forEach(async (img) => {
    const photo = await wikiPhoto(img.dataset.wiki, Number(img.dataset.width || 800));
    if (!photo) { img.remove(); return; }
    img.alt = img.alt || "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = photo.src;
    img.addEventListener("load", () => img.parentElement?.querySelector(".img-fallback")?.remove(), { once: true });
    img.addEventListener("error", () => img.remove(), { once: true }); // keep the gradient fallback
    if (img.dataset.descTarget) {
      const t = root.querySelector(img.dataset.descTarget);
      if (t && !t.textContent) t.textContent = photo.description;
    }
  });
}
