// Hash routes: #/  #/plan/:id  #/plans  #/eval  (#/plan/current shows the last generated plan)
const routes = [];

export function route(pattern, handler) {
  routes.push({ re: new RegExp("^" + pattern.replace(/:(\w+)/g, "([^/]+)") + "$"), handler });
}

export function go(hash) {
  if (location.hash === hash) dispatch(); else location.hash = hash;
}

export function dispatch() {
  const path = location.hash.replace(/^#/, "") || "/";
  for (const r of routes) {
    const m = path.match(r.re);
    if (m) { r.handler(...m.slice(1).map(decodeURIComponent)); return; }
  }
  go("#/");
}

export function startRouter() {
  window.addEventListener("hashchange", dispatch);
  dispatch();
}
