// Dynamusique : fait marcher l'appli et les sons téléchargés sans réseau.
const SHELL = "dyn-shell-v1", AUD = "dyn-audio";

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(["/", "/icon.png", "/manifest.webmanifest"])).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

function timeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("lent")), ms))]);
}

// réseau d'abord, sinon la copie gardée
async function netFirst(req, key, ms) {
  const c = await caches.open(SHELL);
  try {
    const r = await timeout(fetch(req), ms);
    if (r.ok) c.put(key, r.clone());
    return r;
  } catch (e) {
    const hit = await c.match(key);
    if (hit) {
      const h = new Headers(hit.headers);
      h.set("X-Dyn-Offline", "1");
      return new Response(await hit.blob(), { status: 200, headers: h });
    }
    return new Response(JSON.stringify({ error: "hors ligne" }), { status: 503, headers: { "Content-Type": "application/json" } });
  }
}

// son ou pochette : depuis le téléphone si téléchargé (avec gestion du « Range » pour l'iPhone)
async function media(req) {
  const url = new URL(req.url);
  const hit = await caches.open(AUD).then(c => c.match(url.pathname));
  if (!hit) {
    try { return await fetch(req); }
    catch (e) { return new Response("", { status: 504 }); }
  }
  const range = req.headers.get("range");
  if (!range) return hit;
  const blob = await hit.blob(), size = blob.size;
  const m = /bytes=(\d*)-(\d*)/.exec(range) || [];
  let start = m[1] ? +m[1] : 0, end = m[2] ? Math.min(+m[2], size - 1) : size - 1;
  if (!m[1] && m[2]) { start = Math.max(0, size - +m[2]); end = size - 1; }
  if (start >= size) return new Response("", { status: 416, headers: { "Content-Range": "bytes */" + size } });
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": hit.headers.get("Content-Type") || "audio/mp4",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    if (url.pathname === "/" || url.pathname === "/index.html") return e.respondWith(netFirst(req, "/", 4000));
    if (url.pathname === "/api/state") return e.respondWith(netFirst(req, "/api/state", 6000));
    if (/^\/(audio|cover)\//.test(url.pathname)) return e.respondWith(media(req));
    if (url.pathname === "/icon.png" || url.pathname === "/manifest.webmanifest")
      return e.respondWith(caches.match(req).then(r => r || fetch(req)));
    return;
  }
  // polices : gardées pour le hors-ligne
  if (/fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)) {
    e.respondWith(caches.open(SHELL).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(r => { if (r.ok || r.type === "opaque") c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
});
