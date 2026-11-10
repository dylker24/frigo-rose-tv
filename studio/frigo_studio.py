# -*- coding: utf-8 -*-
"""Frigo Rosé Studio : télécharger des vidéos (YouTube…) et convertir des fichiers en MP4,
rangés directement dans les dossiers Jellyfin.

L'appli tourne sur le PC et s'ouvre dans le navigateur (http://localhost:8795).
Elle s'appuie sur yt-dlp (téléchargement), ffmpeg (conversion) et deno (nécessaire à yt-dlp pour YouTube),
téléchargés automatiquement au premier lancement sur Windows.
"""
import hashlib, hmac, secrets, json, os, re, shutil, string, subprocess, sys, threading, time, urllib.request, urllib.error, uuid, webbrowser, zipfile, io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, quote
# Tout est chargé dès le démarrage : une version « .exe » relit ses morceaux dans son propre fichier,
# et si une mise à jour remplace ce fichier pendant qu'elle tourne, un module chargé plus tard serait illisible.
import traceback, unicodedata, difflib, random, ctypes, ipaddress, socket, email.utils, http.client, encodings.idna, json.decoder  # noqa: F401
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor  # noqa: F401
try:
    import winreg  # noqa: F401
except ImportError:
    winreg = None

APP = "Frigo Rosé Studio"
def _read_version():
    """Numéro de version gravé à la fabrication (studio/version.txt)."""
    if os.environ.get("FRS_VERSION"):
        return os.environ["FRS_VERSION"]
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "version.txt"), encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


VERSION = _read_version()
UPDATE_URL = os.environ.get("FRS_UPDATE_URL") or "https://dylker24.github.io/frigo-rose-tv/"
PORT = int(os.environ.get("FRS_PORT", "8795"))
HOST = os.environ.get("FRS_HOST", "0.0.0.0")  # ouvert au téléphone via Tailscale (voir allowed_client)
WIN = os.name == "nt"
NOWIN = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW : pas de fenêtre noire qui s'ouvre

if WIN:
    DATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "FrigoRoseStudio")
else:
    DATA = os.environ.get("FRS_DATA") or os.path.join(os.path.expanduser("~"), ".frigo-rose-studio")
BIN = os.path.join(DATA, "bin")
CONF = os.path.join(DATA, "config.json")
os.makedirs(BIN, exist_ok=True)

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".vob", ".divx", ".ogv", ".rmvb", ".asf"}

# ---------------------------------------------------------------- réglages
DEFAULT_CONF = {"jellyfin_url": "http://localhost:8096", "jellyfin_key": "", "folders": [], "last_dest": "", "quality": "1080", "keep_thumb": True,
                "watch_on": False, "watch_src": "", "watch_dest": "", "watch_delete": False, "parallel": 2,
                "tunarr_url": "http://localhost:8000", "pin_hash": "", "secret": "", "subs": [], "subs_every": 3,
                "tunarr_cmd": None, "jellyfin_cmd": None, "away": False}
FB_URL = os.environ.get("FRS_FB_URL") or "https://nosrecettes-ede01-default-rtdb.europe-west1.firebasedatabase.app/frigorosetv"
_conf_lock = threading.Lock()


def load_conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            c = json.load(f)
        return {**DEFAULT_CONF, **c}
    except Exception:
        return dict(DEFAULT_CONF)


def save_conf(c):
    with _conf_lock:
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONF)


conf = load_conf()
if not conf.get("secret"):
    conf["secret"] = secrets.token_hex(16)
    try:
        save_conf(conf)
    except OSError:
        pass

# ---------------------------------------------------------------- outils (yt-dlp, ffmpeg, deno)
TOOLS = {
    "yt-dlp": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "ffmpeg": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "deno": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
}
setup = {"ready": False, "step": "", "pct": 0, "error": "", "nvenc": None, "ytdlp_version": ""}


def tool(name):
    exe = name + (".exe" if WIN else "")
    p = os.path.join(BIN, exe)
    if os.path.exists(p):
        return p
    w = shutil.which(name)
    return w or p


def run_env():
    e = dict(os.environ)
    e["PATH"] = BIN + os.pathsep + e.get("PATH", "")
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def download_file(url, label, share):
    """Télécharge un fichier en mettant à jour la progression (share = part de la barre totale)."""
    req = urllib.request.Request(url, headers={"User-Agent": "FrigoRoseStudio/" + VERSION})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        got = 0
        base = setup["pct"]
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if total:
                setup["pct"] = base + share * got / total
                setup["step"] = "%s… %d %%" % (label, 100 * got / total)
        setup["pct"] = base + share
        return buf.getvalue()


def ensure_tools():
    try:
        if WIN:
            steps = [n for n in ("yt-dlp", "ffmpeg", "deno") if not os.path.exists(os.path.join(BIN, n + ".exe"))]
            share = 100.0 / max(1, len(steps))
            for n in steps:
                label = {"yt-dlp": "Téléchargement de yt-dlp", "ffmpeg": "Téléchargement de ffmpeg", "deno": "Téléchargement de deno"}[n]
                setup["step"] = label + "…"
                data = download_file(TOOLS[n], label, share)
                if n == "yt-dlp":
                    with open(os.path.join(BIN, "yt-dlp.exe"), "wb") as f:
                        f.write(data)
                else:
                    z = zipfile.ZipFile(io.BytesIO(data))
                    wanted = {"ffmpeg": ["ffmpeg.exe", "ffprobe.exe"], "deno": ["deno.exe"]}[n]
                    for m in z.namelist():
                        b = m.rsplit("/", 1)[-1]
                        if b in wanted:
                            with z.open(m) as src, open(os.path.join(BIN, b), "wb") as dst:
                                shutil.copyfileobj(src, dst)
        missing = [n for n in ("yt-dlp", "ffmpeg", "ffprobe") if not (os.path.exists(tool(n)) or shutil.which(n))]
        if missing:
            raise RuntimeError("Outils introuvables : " + ", ".join(missing))
        setup["step"] = "Vérification de la carte graphique…"
        setup["nvenc"] = nvenc_ok()
        setup["ready"] = True
        setup["step"] = "Prêt"
        setup["pct"] = 100
        threading.Thread(target=update_ytdlp, daemon=True).start()
    except Exception as e:
        setup["error"] = "Préparation impossible : %s. Vérifie la connexion internet puis relance l'appli." % e


def update_ytdlp():
    """YouTube change souvent : on met yt-dlp à jour à chaque lancement."""
    try:
        if WIN:
            subprocess.run([tool("yt-dlp"), "-U"], capture_output=True, timeout=120, creationflags=NOWIN, env=run_env())
        r = subprocess.run([tool("yt-dlp"), "--version"], capture_output=True, text=True, timeout=30, creationflags=NOWIN, env=run_env())
        setup["ytdlp_version"] = r.stdout.strip()
    except Exception:
        pass


def nvenc_ok():
    """La carte NVIDIA (GTX 1060) sait-elle encoder en H.264 ? Beaucoup plus rapide que le processeur."""
    if os.environ.get("FRS_NO_NVENC"):
        return False
    try:
        r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.3",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, timeout=30, creationflags=NOWIN)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- Jellyfin
BGLOG = []


def bglog(msg):
    BGLOG.insert(0, time.strftime("%H:%M:%S ") + str(msg)[:1500])
    del BGLOG[60:]


def _trace():
    """Les dernières lignes de l'erreur, pour comprendre un bug à distance."""
    import traceback
    return "".join(traceback.format_exc().splitlines(True)[-8:])[-1500:]


def _fast(url):
    """« localhost » sous Windows essaie d'abord l'IPv6 et perd ~2 s à chaque appel : on va droit en IPv4."""
    return re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", (url or "").strip().rstrip("/"), flags=re.I)


def jf(path, method="GET", body=None, timeout=8):
    url = _fast(conf["jellyfin_url"]) + path
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-Emby-Token": conf["jellyfin_key"], "Content-Type": "application/json",
        "Authorization": 'MediaBrowser Client="Frigo Rose Studio", Device="PC", DeviceId="frigo-rose-studio", Version="%s", Token="%s"' % (VERSION, conf["jellyfin_key"])})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        t = r.read()
        return json.loads(t) if t.strip() else None


jf_cache = {"t": 0, "libs": [], "error": ""}


def jf_libraries(force=False):
    if not conf["jellyfin_key"]:
        jf_cache.update(libs=[], error="")
        return jf_cache
    if not force and time.time() - jf_cache["t"] < 60:
        return jf_cache
    try:
        vf = jf("/Library/VirtualFolders") or []
        libs = []
        for v in vf:
            for loc in v.get("Locations") or []:
                libs.append({"name": v.get("Name", ""), "type": v.get("CollectionType") or "", "path": loc, "item": v.get("ItemId") or ""})
        jf_cache.update(t=time.time(), libs=libs, error="")
    except urllib.error.HTTPError as e:
        jf_cache.update(t=time.time(), libs=[], error="Clé refusée par Jellyfin" if e.code in (401, 403) else "Jellyfin répond une erreur %d" % e.code)
    except Exception:
        jf_cache.update(t=time.time(), libs=[], error="Jellyfin injoignable à cette adresse")
    return jf_cache


def jf_refresh():
    if not conf["jellyfin_key"]:
        return False
    try:
        jf("/Library/Refresh", "POST")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- analyse et conversion
def probe(path):
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN, env=run_env())
    if r.returncode != 0:
        raise RuntimeError("Fichier illisible (%s)" % (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "ffprobe"))
    return json.loads(r.stdout)


EMOJI_RE = "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0E\uFE0F\u200D\u20E3]+"

TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def plan_conversion(info):
    """Décide ce qu'on garde tel quel et ce qu'on réencode. Renvoie (args ffmpeg, résumé lisible, besoin de réencoder la vidéo)."""
    streams = info.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    if not vids:
        raise RuntimeError("Pas de piste vidéo dans ce fichier")
    v = vids[0]
    args, notes = ["-map", "0:%d" % v["index"]], []
    pix = v.get("pix_fmt") or ""
    copy_v = v.get("codec_name") == "h264" and pix in ("yuv420p", "yuvj420p") and (v.get("codec_tag_string") or "") != "unknown"
    if copy_v:
        args += ["-c:v", "copy"]
        notes.append("image gardée telle quelle (déjà en H.264)")
    else:
        if setup.get("nvenc"):
            args += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"]
            notes.append("image convertie en H.264 par la carte graphique")
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
            notes.append("image convertie en H.264")
        # largeur et hauteur paires obligatoires pour le H.264
        w, h = v.get("width") or 0, v.get("height") or 0
        vf = []
        if v.get("codec_name") == "mpeg2video":
            vf.append("bwdif=mode=0:deint=interlaced")  # DVD : on enlève les lignes entrelacées
        if h > 1088:
            vf.append("scale=-2:1080:flags=lanczos")  # 4K → 1080p : bien assez pour la télé, et 4 fois plus léger
            notes.append("ramené en 1080p")
            w, h = 0, 1080
        if (v.get("color_transfer") or "") in ("smpte2084", "arib-std-b67"):
            # HDR → couleurs normales (sinon l'image paraît délavée sur la télé)
            vf.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
            notes.append("HDR converti en couleurs normales")
        if w % 2 or h % 2:
            vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        if vf:
            args += ["-vf", ",".join(vf)]
    # la version française en premier (c'est celle que lit la télé)
    fr = lambda a: ((a.get("tags") or {}).get("language") or "").lower() in ("fre", "fra", "fr")
    auds = sorted(auds, key=lambda a: 0 if fr(a) else 1)
    for i, a in enumerate(auds):
        args += ["-map", "0:%d" % a["index"]]
        if a.get("codec_name") in ("aac", "mp3"):
            args += ["-c:a:%d" % i, "copy"]
        else:
            ch = a.get("channels") or 2
            args += ["-c:a:%d" % i, "aac", "-b:a:%d" % i, "192k" if ch <= 2 else "384k"]
            if ch > 6:
                args += ["-ac:a:%d" % i, "6"]
    if auds:
        conv = [a.get("codec_name") for a in auds if a.get("codec_name") not in ("aac", "mp3")]
        notes.append(("son converti en AAC (%s)" % ", ".join(sorted(set(c.upper() for c in conv if c)))) if conv else "son gardé tel quel")
    kept = 0
    for s in subs:
        if s.get("codec_name") in TEXT_SUBS:
            args += ["-map", "0:%d" % s["index"], "-c:s:%d" % kept, "mov_text"]
            kept += 1
    if subs:
        notes.append("%d sous-titre%s gardé%s" % (kept, "s" if kept > 1 else "", "s" if kept > 1 else "") + (" (les sous-titres en image ne passent pas en MP4)" if kept < len(subs) else ""))
    if auds:
        args += ["-disposition:a:0", "default"] + sum((["-disposition:a:%d" % i, "0"] for i in range(1, len(auds))), [])
    args += ["-map_metadata", "0", "-map_chapters", "0", "-movflags", "+faststart"]
    return args, ", ".join(notes), not copy_v


def needs_conversion(path):
    """Un fichier sort de YouTube : est-il déjà un MP4 H.264 + AAC ?"""
    try:
        info = probe(path)
    except Exception:
        return True
    ok_v = any(s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in info.get("streams", []))
    ok_a = all(s.get("codec_name") in ("aac", "mp3") for s in info.get("streams", []) if s.get("codec_type") == "audio")
    return not (path.lower().endswith(".mp4") and ok_v and ok_a)


# ---------------------------------------------------------------- file d'attente
jobs = []
jobs_lock = threading.Lock()
wake = threading.Event()


def new_job(kind, **kw):
    j = {"id": uuid.uuid4().hex[:10], "kind": kind, "status": "attente", "pct": 0, "msg": "En attente", "title": "", "out": [], "error": "",
         "t": time.time(), "proc": None, "cancel": False}
    j.update(kw)
    with jobs_lock:
        jobs.append(j)
    wake.set()
    return j



def is_night():
    """La nuit (2 h à 7 h par défaut) : c'est là que tournent les conversions « cette nuit », sans gêner les chaînes."""
    h = time.localtime().tm_hour
    a, b = int(conf.get("night_from", 2)), int(conf.get("night_to", 7))
    return (a <= h < b) if a < b else (h >= a or h < b)

def public_job(j):
    return {k: v for k, v in j.items() if k not in ("proc",)}


def worker(kind):
    while True:
        wake.wait(2)
        if not setup["ready"]:
            continue
        while True:
            with jobs_lock:
                busy = sum(1 for x in jobs if x["status"] == "en cours" and x["kind"] == kind)
                lim = 1 if kind == "dl" else max(1, min(4, int(conf.get("parallel") or 2)))
                night_busy = any(x["status"] == "en cours" and x.get("night") for x in jobs)
                j = None if busy >= lim else next((x for x in jobs if x["status"] == "attente" and x["kind"] == kind
                                                   and (not x.get("night") or (is_night() and not night_busy))), None)
                if j:
                    j["status"] = "en cours"
            if not j:
                break
            try:
                (do_download if j["kind"] == "dl" else do_convert)(j)
                if j["cancel"]:
                    j.update(status="annulé", msg="Annulé")
                else:
                    j["status"] = "fini"
                    if j.get("req"):
                        threading.Thread(target=req_steps, args=(j["req"], {"found": True, "converted": True}, {"source": "telecharge"}), daemon=True).start()
                    refresh_soon(j)
            except Exception as e:
                if j["cancel"]:
                    j.update(status="annulé", msg="Annulé")
                else:
                    j.update(status="erreur", error=str(e), msg=str(e))
            j["proc"] = None


_ref = {"timer": None, "jobs": []}


def refresh_soon(j):
    """Plusieurs vidéos finissent en même temps : on actualise Jellyfin une seule fois, 15 s après la dernière."""
    with jobs_lock:
        _ref["jobs"].append(j)
        if _ref["timer"]:
            _ref["timer"].cancel()
        delay = 0.5 if os.environ.get("FRS_FAST_REFRESH") else 15

        def go():
            with jobs_lock:
                js, _ref["jobs"] = _ref["jobs"], []
            ok = jf_refresh()
            # envoyé depuis la Médiathèque : la chaîne suit sa médiathèque (ajouts + nettoyage), avec le bandeau de suivi
            sy = {l["name"] for x in js if x.get("sync") for f in (x.get("out") or []) for l in jf_cache["libs"] if _inside(f, [l["path"]])}
            for x in js:
                if x.get("trash_src") and x.get("out") and x.get("status") != "erreur":
                    for l in jf_cache["libs"]:
                        if _inside(x["out"][0], [l["path"]]):
                            CONV_DONE[l["name"]] = CONV_DONE.get(l["name"], 0) + 1
            if sy:
                follow_libs(sorted(sy))
            for x in js:
                if ok:
                    x["msg"] += " · Jellyfin actualisé"
                if x.get("req") and (ok or not conf["jellyfin_key"]):
                    req_steps(x["req"], {"jellyfin": True})
            if True:
                # Tunarr garde sa propre liste : on lui demande de relire la médiathèque quand Jellyfin a fini
                d1, d2 = float(os.environ.get("FRS_TUNARR_DELAY", "45")), float(os.environ.get("FRS_TUNARR_DELAY2", "150"))
                for d in (d1, d2):
                    t = threading.Timer(d, tunarr_scan_for, args=(js,))
                    t.daemon = True
                    t.start()
                for d, last in ((d1 + 15, False), (d2 + 20, False), (d2 * 2 + 20, True)):
                    t = threading.Timer(d, auto_add_to_channels, args=(js, last))
                    t.daemon = True
                    t.start()
        _ref["timer"] = threading.Timer(delay, go)
        _ref["timer"].daemon = True
        _ref["timer"].start()


# ---------------------------------------------------------------- Tunarr
def tunarr(path, method="GET", timeout=8):
    # POST sans contenu : ni corps ni Content-Type (sinon Tunarr répond 415 « x-www-form-urlencoded »)
    req = urllib.request.Request(_fast(conf.get("tunarr_url", "http://localhost:8000")) + path, data=None, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        t = r.read()
        try:
            return json.loads(t) if t.strip() else None
        except ValueError:
            return None


def _key(s):
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


_tn = {"t": 0, "ok": None, "version": ""}


def tunarr_status():
    if time.time() - _tn["t"] > 30:
        _tn["t"] = time.time()
        try:
            v = tunarr("/api/version", timeout=3) or {}
            _tn.update(ok=True, version=str(v.get("tunarr") or v.get("version") or ""))
        except Exception:
            _tn.update(ok=False, version="")
    return {"ok": _tn["ok"], "version": _tn["version"]}


def _fz(s):
    """Nom « tolérant » : sans accents, sans « TV », sans les s du pluriel (Scènes de Ménage ↔ Scènes de Ménages, Simpson ↔ Simpsons)."""
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w != "tv"]
    return "".join(w[:-1] if len(w) > 3 and w.endswith(("s", "x")) else w for w in words)


def same_name(a, b):
    fa, fb_ = _fz(a), _fz(b)
    if not fa or not fb_:
        return False
    if fa == fb_:
        return True
    import difflib
    return difflib.SequenceMatcher(None, fa, fb_).ratio() >= 0.9


def lib_names_for(f):
    nf = os.path.normcase(os.path.abspath(f))
    names = set()
    for l in jf_cache.get("libs") or []:
        lp = os.path.normcase(os.path.abspath(l["path"]))
        if nf.startswith(lp.rstrip("\\/") + os.sep):
            names.add(l["name"])
    if not names:
        d = os.path.dirname(os.path.abspath(f))
        for _ in range(4):
            if os.path.basename(d):
                names.add(os.path.basename(d))
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    return names


def tunarr_scan_for(js):
    """Relance l'analyse des médiathèques Tunarr qui correspondent aux dossiers où sont arrivées les vidéos."""
    try:
        jf_libraries(force=True)
        names = set()
        for j in js:
            for f in j.get("out") or []:
                names |= lib_names_for(f)
        if not names:
            return
        done = 0
        for ms in tunarr("/api/media-sources") or []:
            for lib in ms.get("libraries") or []:
                if any(same_name(lib.get("name"), n) for n in names):
                    tunarr("/api/media-sources/%s/libraries/%s/scan?forceScan=true" % (ms["id"], lib["id"]), "POST")
                    done += 1
        if done:
            for j in js:
                if "Tunarr" not in j["msg"]:
                    j["msg"] += " · Tunarr actualisé"
    except Exception:
        pass


# ---------------------------------------------------------------- Demandes (même base que l'appli Demandes et la télé)
STEP_KEYS = ["found", "converted", "jellyfin", "tunarr"]


def fb(path, method="GET", body=None):
    req = urllib.request.Request(FB_URL + path + ".json", data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def req_steps(rid, add, extra=None):
    """Coche des étapes d'une demande et recalcule son état (comme l'appli Demandes)."""
    try:
        r = fb("/requests/" + rid) or {}
        steps = dict(r.get("steps") or {})
        steps.update(add)
        n = sum(1 for k in STEP_KEYS if steps.get(k))
        st = "non" if r.get("status") == "non" else ("ajoute" if n == 4 else "cours" if n else "attente")
        body = {"steps": steps, "status": st}
        if extra:
            body.update(extra)
        fb("/requests/" + rid, "PATCH", body)
    except Exception:
        pass


# ---------------------------------------------------------------- recherche YouTube
def yt_search(q, n=12):
    if os.environ.get("FRS_FAKE_YT"):  # tests
        with open(os.environ["FRS_FAKE_YT"], encoding="utf-8") as f:
            return json.load(f)
    r = subprocess.run([tool("yt-dlp"), "--flat-playlist", "-J", "--no-warnings", "--encoding", "utf-8", "ytsearch%d:%s" % (n, q)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=NOWIN, env=run_env())
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
        raise RuntimeError(nice_ytdlp_error(err[-1][6:] if err else r.stderr[-200:]))
    data = json.loads(r.stdout or "{}")
    out = []
    for e in data.get("entries") or []:
        vid = e.get("id")
        if not vid or e.get("ie_key") not in (None, "Youtube"):
            continue
        out.append({"id": vid, "title": e.get("title") or "", "duration": e.get("duration") or 0, "channel": e.get("channel") or e.get("uploader") or "",
                    "views": e.get("view_count") or 0, "url": "https://www.youtube.com/watch?v=" + vid, "thumb": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid})
    return out


# ---------------------------------------------------------------- code du téléphone
def pin_hash(pin):
    return hashlib.sha256(("frs:" + pin).encode()).hexdigest()


def session_token():
    return hmac.new(conf["secret"].encode(), (conf.get("pin_hash") or "").encode(), hashlib.sha256).hexdigest()


_fails = {"n": 0, "until": 0}


def popen(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NOWIN, env=run_env())


def ffmpeg_convert(j, src, dst, label=""):
    info = probe(src)
    args, notes, heavy = plan_conversion(info)
    dur = float((info.get("format") or {}).get("duration") or 0)
    tmp = dst + ".part.mp4"
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-i", src] + args + [tmp]
    j["msg"] = (label + " · " if label else "") + "Conversion : " + notes
    p = popen(cmd)
    j["proc"] = p
    errs = []
    for line in p.stdout:
        line = line.strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                t = int(line.split("=", 1)[1]) / 1e6
                if dur > 0:
                    j["pct"] = max(j["pct"], min(99.0, 100 * t / dur))
            except ValueError:
                pass
        elif line and "=" not in line:
            errs.append(line)
        if j["cancel"]:
            p.kill()
    p.wait()
    if j["cancel"]:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    if p.returncode != 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("Conversion ratée : " + (errs[-1] if errs else "erreur ffmpeg"))
    os.replace(tmp, dst)
    return notes


def unique_path(p):
    if not os.path.exists(p):
        return p
    b, e = os.path.splitext(p)
    i = 2
    while os.path.exists("%s (%d)%s" % (b, i, e)):
        i += 1
    return "%s (%d)%s" % (b, i, e)


# ---------- assembler plusieurs parties (téléfilm en 2 parties…) en une seule vidéo ----------
def _run_ff(j, cmd, dur, base, span, label):
    j["msg"] = label
    p = popen(cmd)
    j["proc"] = p
    errs = []
    for line in p.stdout:
        line = line.strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                t = int(line.split("=", 1)[1]) / 1e6
                if dur > 0:
                    j["pct"] = max(j["pct"], min(99.0, base + span * t / dur))
            except ValueError:
                pass
        elif line and "=" not in line:
            errs.append(line)
        if j["cancel"]:
            p.kill()
    p.wait()
    if j["cancel"]:
        return False
    if p.returncode != 0:
        raise RuntimeError("Assemblage raté : " + (errs[-1] if errs else "erreur ffmpeg"))
    return True


def merge_name(paths):
    """Un nom propre pour la vidéo assemblée : la partie commune des noms, sans « partie 1 »."""
    names = [os.path.splitext(os.path.basename(x))[0] for x in paths]
    pre = os.path.commonprefix(names)
    pre = re.sub(r"[\s._\-–(]*(partie|part|pt|épisode|ep|cd|disque|disc)?[\s._\-–]*\d*\s*$", "", pre, flags=re.I).strip(" ._-–(")
    return pre or names[0]


def do_merge(j):
    srcs = j["srcs"]
    for x in srcs:
        if not os.path.isfile(x):
            raise RuntimeError("Fichier introuvable : %s" % os.path.basename(x))
    folder = os.path.dirname(srcs[0])
    j["title"] = j.get("name") or merge_name(srcs)
    infos = [probe(x) for x in srcs]
    durs = [float((i.get("format") or {}).get("duration") or 0) for i in infos]
    total = sum(durs) or 1
    vids = []
    for i in infos:
        v = next((x for x in i.get("streams", []) if x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic")), None)
        if not v:
            raise RuntimeError("Une des parties n'a pas d'image")
        vids.append(v)
    w, h = (vids[0].get("width") or 1280) // 2 * 2, (vids[0].get("height") or 720) // 2 * 2
    copy_v = all(v.get("codec_name") == "h264" and (v.get("pix_fmt") or "") in ("yuv420p", "yuvj420p") and v.get("width") == vids[0].get("width") and v.get("height") == vids[0].get("height") for v in vids)
    parts, done = [], 0.0
    try:
        for n, (x, info, v) in enumerate(zip(srcs, infos, vids)):
            auds = [a for a in info.get("streams", []) if a.get("codec_type") == "audio"]
            a = next((a for a in auds if ((a.get("tags") or {}).get("language") or "").lower() in ("fre", "fra", "fr")), auds[0] if auds else None)
            args = ["-map", "0:%d" % v["index"]]
            if copy_v:
                args += ["-c:v", "copy"]
            else:
                args += (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"] if setup.get("nvenc") else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
                args += ["-pix_fmt", "yuv420p", "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1" % (w, h, w, h), "-r", "25" if not vids[0].get("r_frame_rate") else vids[0]["r_frame_rate"]]
            if a:
                args += ["-map", "0:%d" % a["index"], "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "192k"]
            tmp = os.path.join(folder, ".assemblage-%s-%d.mp4" % (j["id"], n))
            parts.append(tmp)
            cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-i", x] + args + ["-sn", tmp]
            if not _run_ff(j, cmd, durs[n], 90 * done / total, 90 * durs[n] / total, "Partie %d sur %d…" % (n + 1, len(srcs))):
                return
            done += durs[n]
        lst = os.path.join(folder, ".assemblage-%s.txt" % j["id"])
        with open(lst, "w", encoding="utf-8") as f:
            for x in parts:
                f.write("file '%s'\n" % x.replace("'", "'\\''"))
        parts.append(lst)
        dst = unique_path(os.path.join(folder, safe_name(j["title"]) + ".mp4"))
        tmp = dst + ".part.mp4"
        cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-f", "concat", "-safe", "0", "-i", lst,
               "-map", "0", "-c", "copy", "-movflags", "+faststart", tmp]
        if not _run_ff(j, cmd, total, 90, 9.5, "Je colle les parties…"):
            return
        os.replace(tmp, dst)
    finally:
        for x in parts + [locals().get("tmp") or ""]:
            if x and os.path.basename(x).startswith(".assemblage-") or (x and x.endswith(".part.mp4")):
                try:
                    os.remove(x)
                except OSError:
                    pass
    # les parties séparées partent à la corbeille de Studio (récupérables) ; la vidéo entière prend leur place
    try:
        media_trash(srcs)
    except Exception:
        pass
    j["out"] = [dst]
    j["pct"] = 100
    j["msg"] = "Terminé · %d parties assemblées en une vidéo de %s" % (len(srcs), "%d h %02d" % (total // 3600, total % 3600 // 60) if total >= 3600 else "%d min" % (total // 60))



# ---------------------------------------------------------------- Importer un DVD (film) : HandBrake trouve le film principal et le convertit en MP4
HB_ZIP = "https://github.com/HandBrake/HandBrake/releases/download/1.8.2/HandBrakeCLI-1.8.2-win-x86_64.zip"
DVDCSS = ["https://download.videolan.org/pub/libdvdcss/1.4.3/win64/libdvdcss-2.dll", "https://download.videolan.org/pub/libdvdcss/1.2.11/win64/libdvdcss-2.dll"]
HB_DIR = os.path.join(BIN, "handbrake")


def dvd_drives():
    """Lecteurs DVD de ce PC, avec le nom du disque s'il y en a un."""
    if os.environ.get("FRS_FAKE_DVD"):
        lab = os.environ["FRS_FAKE_DVD"]
        return [{"drive": "F:", "label": lab, "disc": bool(lab)}]
    if not WIN:
        return []
    # Windows directement (sans PowerShell) : chaque lettre de lecteur de type CD/DVD, avec le nom du disque
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        res = []
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = chr(65 + i) + ":\\"
            if k32.GetDriveTypeW(root) != 5:
                continue
            name = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            old = k32.SetErrorMode(1)
            ok = k32.GetVolumeInformationW(root, name, 261, None, None, None, fs, 261)
            k32.SetErrorMode(old)
            res.append({"drive": root[:2], "label": name.value if ok else "", "disc": bool(ok)})
        if res:
            return res
    except Exception as e:
        bglog("DVD : lecteurs (Windows) : %s" % e)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=5' | ForEach-Object { $_.DeviceID + '|' + $_.VolumeName + '|' + [string]($_.Size -gt 0) }"],
                             capture_output=True, text=True, timeout=15, creationflags=NOWIN).stdout
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 3 and parts[0]:
            res.append({"drive": parts[0], "label": parts[1], "disc": parts[2].strip().lower() == "true"})
    return res


def dvd_title(label):
    """« LES_VISITEURS_D1 » → « Les Visiteurs »."""
    t = re.sub(r"[_\.]+", " ", label or "").strip()
    t = re.sub(r"\b(D(ISC|VD)?\s*\d|PAL|NTSC|FR|FRA|FRANCE|FRENCH|WS|16X9|DVD|SE|ZONE\s*2|R2)\b", "", t, flags=re.I)
    t = re.sub(r"\b([A-Za-z]{3,})(\d{1,2})\b", r"\1 \2", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t.lower() in ("", "video", "disc", "dvdvolume", "volume", "movie", "film"):
        return "Film DVD"
    small = {"de", "du", "des", "la", "le", "les", "et", "a", "au", "aux", "en", "l", "d"}
    w = [x.lower() for x in t.split()]
    return " ".join(x if (i and x in small) else x[:1].upper() + x[1:] for i, x in enumerate(w)) or "Film DVD"


def hb_tool():
    exe = os.path.join(HB_DIR, "HandBrakeCLI.exe" if WIN else "HandBrakeCLI")
    if not WIN and not os.path.exists(exe):
        return shutil.which("HandBrakeCLI") or exe
    return exe


def ensure_handbrake(j):
    """Une seule fois : HandBrake (en ligne de commande) et de quoi lire les DVD du commerce."""
    if not WIN:
        return
    os.makedirs(HB_DIR, exist_ok=True)
    get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "FrigoRoseStudio/" + VERSION}), timeout=120).read()
    if not os.path.exists(os.path.join(HB_DIR, "HandBrakeCLI.exe")):
        j["msg"] = "Préparation de l'import DVD (une seule fois)…"
        z = zipfile.ZipFile(io.BytesIO(get(HB_ZIP)))
        for m in z.namelist():
            b = m.rsplit("/", 1)[-1]
            if b.lower().endswith((".exe", ".dll")):
                with z.open(m) as src, open(os.path.join(HB_DIR, b), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    if not os.path.exists(os.path.join(HB_DIR, "libdvdcss-2.dll")):
        for u in DVDCSS:
            try:
                data = get(u)
                if len(data) > 20000:
                    with open(os.path.join(HB_DIR, "libdvdcss-2.dll"), "wb") as f:
                        f.write(data)
                    break
            except Exception:
                continue


def makemkv_tool():
    if os.environ.get("FRS_MAKEMKV"):
        return os.environ["FRS_MAKEMKV"]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), r"C:\Program Files (x86)", r"C:\Program Files"):
        for exe in ("makemkvcon64.exe", "makemkvcon.exe"):
            pth = os.path.join(base or "", "MakeMKV", exe)
            if base and os.path.exists(pth):
                return pth
    # installé ailleurs : on demande à Windows où il est (liste des programmes installés)
    if WIN:
        try:
            import winreg
            spots = []
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for path in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                    try:
                        root = winreg.OpenKey(hive, path)
                    except OSError:
                        continue
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            k = winreg.OpenKey(root, winreg.EnumKey(root, i))
                            name = str(winreg.QueryValueEx(k, "DisplayName")[0])
                        except OSError:
                            continue
                        if "makemkv" not in name.lower():
                            continue
                        for v in ("InstallLocation", "DisplayIcon", "UninstallString"):
                            try:
                                x = str(winreg.QueryValueEx(k, v)[0]).strip('"').split(",")[0]
                                spots.append(x if os.path.isdir(x) else os.path.dirname(x))
                            except OSError:
                                pass
            for d in [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "MakeMKV")] + spots:
                for exe in ("makemkvcon64.exe", "makemkvcon.exe"):
                    pth = os.path.join(d, exe)
                    if d and os.path.exists(pth):
                        return pth
        except Exception as e:
            bglog("DVD : recherche de MakeMKV : %s" % e)
    return shutil.which("makemkvcon64") or shutil.which("makemkvcon") or ""


def _mkv_time(t):
    try:
        h, m, sec = [int(x) for x in t.split(":")]
        return h * 3600 + m * 60 + sec
    except Exception:
        return 0


def dvd_makemkv(j, drive, out):
    """Plan B pour les DVD très protégés : MakeMKV copie le film principal, puis ffmpeg le convertit en MP4."""
    mk = makemkv_tool()
    if not mk:
        raise RuntimeError("Ce DVD est trop protégé pour la méthode simple. Installe MakeMKV (gratuit, makemkv.com) sur le PC puis relance l'import : Studio s'en servira tout seul")
    j["msg"] = "Analyse du DVD avec MakeMKV (quelques minutes)…"
    src = "disc:" + str(j.get("mkv_disc", 0))  # premier lecteur DVD (MakeMKV numérote les lecteurs à partir de 0)
    p = subprocess.Popen([mk, "-r", "--minlength=3000", "--progress=-same", "info", src], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NOWIN)
    j["proc"] = p
    lines, t0 = [], time.time()
    for line in p.stdout:
        lines.append(line.rstrip())
        if j["cancel"] or time.time() - t0 > 25 * 60:
            p.kill()
            break
        m = re.match(r"PRGV:(\d+),(\d+),(\d+)", line)
        if m and int(m.group(3)):
            j["msg"] = "Analyse du DVD avec MakeMKV… %d %%" % (100 * int(m.group(2)) / int(m.group(3)))
    p.wait()
    if j["cancel"]:
        return
    idx = src

    class _R:
        stdout = "\n".join(lines)
    r = _R()
    titles = {}
    for line in r.stdout.splitlines():
        m = re.match(r'TINFO:(\d+),(\d+),\d+,"(.*)"', line)
        if m:
            titles.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    if not titles:
        bglog("DVD : MakeMKV dit : " + " | ".join(l[4:].split(",", 3)[-1][:160] for l in lines if l.startswith("MSG:"))[-600:])
        low = r.stdout.lower()
        if "evaluation period" in low or "registration key" in low:
            raise RuntimeError("MakeMKV demande sa clé gratuite : ouvre MakeMKV, menu Aide → Enregistrer, et colle la clé bêta publiée sur makemkv.com")
        raise RuntimeError("MakeMKV ne trouve pas le film sur ce DVD (disque rayé ?)")
    # le film = le titre le plus long (en cas d'égalité, celui avec le plus de chapitres, comme MakeMKV le conseille)
    best = max(titles.items(), key=lambda kv: (_mkv_time(kv[1].get("9", "")), int(kv[1].get("8", "0") or 0)))
    tdir = out[:-4] + ".dvd-copie"
    os.makedirs(tdir, exist_ok=True)
    j["msg"] = "Copie du film depuis le DVD…"
    p = subprocess.Popen([mk, "-r", "--progress=-same", "mkv", idx, best[0], tdir], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NOWIN)
    j["proc"] = p
    for line in p.stdout:
        if j["cancel"]:
            p.kill()
            break
        m = re.match(r"PRGV:(\d+),(\d+),(\d+)", line)
        if m and int(m.group(3)):
            j["pct"] = min(60.0, 60 * int(m.group(2)) / int(m.group(3)))
    p.wait()
    mkvs = [os.path.join(tdir, f) for f in os.listdir(tdir) if f.lower().endswith(".mkv")]
    try:
        if j["cancel"]:
            return
        if not mkvs:
            raise RuntimeError("MakeMKV n'a pas réussi à copier le film")
        src = max(mkvs, key=os.path.getsize)
        j["pct"] = 60
        base = 60.0
        sub = j  # même tâche : la conversion remplit la barre de 60 à 100 %
        info = probe(src)
        args, notes, _h = plan_conversion(info)
        dur = float((info.get("format") or {}).get("duration") or 0)
        tmp = out + ".part.mp4"
        cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-i", src] + args + [tmp]
        if not _run_ff(sub, cmd, dur, base, 40.0, "Conversion du film en MP4…"):
            return
        os.replace(tmp, out)
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def dvd_eject(drive):
    if not WIN or not drive:
        return
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s').InvokeVerb('Eject')" % drive.rstrip("\\")],
                       timeout=20, creationflags=NOWIN)
    except Exception:
        pass


def do_dvd(j):
    drive = (j.get("drive") or "").rstrip("\\")
    name = safe_name(j.get("name") or "Film DVD")
    dest = j.get("dest") or ""
    if not dest:
        raise RuntimeError("Choisis la médiathèque où ranger le film")
    os.makedirs(dest, exist_ok=True)
    out0 = unique_path(os.path.join(dest, name + ".mp4"))
    j["title"] = name
    bglog("DVD : import de %s depuis %s · MakeMKV : %s" % (name, drive, makemkv_tool() or "pas trouvé"))
    if makemkv_tool():
        # MakeMKV installé : c'est lui qui lit le mieux les DVD protégés, on commence par lui
        try:
            dvd_makemkv(j, drive, out0)
            if j["cancel"]:
                return
            if os.path.exists(out0):
                j["out"] = [out0]
                j["pct"] = 100
                j["msg"] = "Film importé · " + os.path.basename(out0)
                if j.get("eject", True):
                    dvd_eject(drive)
                return
        except Exception as e:
            bglog("DVD : MakeMKV n'a pas réussi (%s), essai avec HandBrake" % e)
            j["pct"] = 0
    ensure_handbrake(j)
    hb = hb_tool()
    if not os.path.exists(hb) and not shutil.which(hb):
        raise RuntimeError("HandBrake introuvable")
    out = unique_path(os.path.join(dest, name + ".mp4"))
    tmp = out[:-4] + ".dvd-en-cours.mp4"
    enc = ["-e", "nvenc_h264", "-q", "24"] if setup.get("nvenc") else ["-e", "x264", "--encoder-preset", "veryfast", "-q", "21"]
    cmd = [hb, "-i", (drive + "\\") if WIN else drive, "--main-feature", "-o", tmp, "-Z", "Fast 576p25", "-f", "av_mp4", "--optimize"] + enc + \
          ["-E", "av_aac", "-B", "160", "--mixdown", "stereo", "--audio-lang-list", "fra,fre,und", "--first-audio",
           "-s", "scan", "-F", "--subtitle-burned"]
    j["title"] = name
    j["msg"] = "Lecture du DVD…"
    p = popen(cmd)
    j["proc"] = p
    errs, enc_seen, t0 = [], False, time.time()
    for line in p.stdout:
        if j["cancel"]:
            p.kill()
            break
        if not enc_seen and time.time() - t0 > 12 * 60:
            errs.append("HandBrake bloqué sur la lecture du disque")
            p.kill()
            break
        for part in line.replace("\r", "\n").split("\n"):
            part = part.strip()
            m = re.search(r"Encoding: task \d+ of \d+, ([\d.]+) %(?:.*ETA ([\dhms]+))?", part)
            if m:
                enc_seen = True
                j["pct"] = min(99.0, float(m.group(1)))
                eta = re.match(r"(\d+)h(\d+)m", m.group(2) or "")
                left = "" if not eta else (("reste %d h %02d" % (int(eta.group(1)), int(eta.group(2)))) if int(eta.group(1)) else "reste %d min" % max(1, int(eta.group(2))))
                j["msg"] = "Conversion du film… " + left
            elif part and not part.startswith("Encoding"):
                errs.append(part)
                if "scanning title" in part.lower() or "scan:" in part.lower():
                    j["msg"] = "Recherche du film sur le disque…"
    p.wait()
    if j["cancel"]:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return
    short = False
    if p.returncode == 0 and os.path.exists(tmp):
        try:
            d = float((probe(tmp).get("format") or {}).get("duration") or 0)
            short = 0 < d < 40 * 60  # un « film » de moins de 40 min : HandBrake a pris un faux titre
        except Exception:
            pass
    if short or p.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 50 * 1024 * 1024:
        try:
            os.remove(tmp)
        except OSError:
            pass
        bglog("DVD : HandBrake n'a pas réussi (%s), plan B MakeMKV" % (errs[-1] if errs else "?"))
        j["pct"] = 0
        dvd_makemkv(j, drive, out)
        if j["cancel"]:
            return
    else:
        os.replace(tmp, out)
    j["out"] = [out]
    j["pct"] = 100
    j["msg"] = "Film importé · " + os.path.basename(out)
    if j.get("eject", True):
        dvd_eject(drive)


def dvd_import(drive, name, dest, tchan="", eject=True):
    if any(x.get("dvd") and x["status"] in ("attente", "en cours") and x.get("drive") == drive for x in jobs):
        raise RuntimeError("Ce DVD est déjà en cours d'import")
    nm = re.sub(r"\s+", " ", (name or "").strip()) or "Film DVD"
    return new_job("conv", dvd=True, drive=drive, name=nm, title=nm, dest=dest, tchan=tchan, eject=eject, src="")["id"]


# ================================================================ pilote automatique
# 1) les vidéos lourdes ajoutées dans une médiathèque sont converties la nuit suivante
# 2) Studio surveille disques, chaînes et pannes signalées par les télés, et prévient (Studio + téléphone via ntfy)
ALERTS = {}          # clé -> {"t", "msg", "fix", "level", "sent"}
_autoconv_seen = {}


def notify_phone(title, msg):
    topic = conf.get("ntfy_topic")
    if not topic or not conf.get("ntfy_on", True):
        return False
    try:
        req = urllib.request.Request("https://ntfy.sh/" + quote(topic, safe=""), data=msg.encode("utf-8"), method="POST",
                                     headers={"Title": "Frigo Rose Studio", "Tags": "tv", "Priority": "default"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        bglog("ntfy : %s" % e)
        return False


def alert(key, msg, fix="", level="warn"):
    a = ALERTS.get(key)
    now = time.time()
    if a and a["msg"] == msg:
        a["t"] = now
        if now - a.get("sent", 0) < 12 * 3600:
            return
    else:
        ALERTS[key] = a = {"t": now, "first": now, "msg": msg, "fix": fix, "level": level, "sent": 0}
    a["fix"] = fix
    if notify_phone("Frigo Rosé Studio", msg + ((" · " + fix) if fix else "")):
        a["sent"] = now
    else:
        a["sent"] = a.get("sent") or 0
    away_log("⚠️ " + msg)


def clear_alert(key):
    ALERTS.pop(key, None)


def _find_moved(path):
    """Le dossier d'une médiathèque a disparu : est-il sur un autre disque (lettre changée) ?"""
    if not WIN or len(path) < 3 or path[1] != ":":
        return ""
    tail = path[2:]
    for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
        cand = d + ":" + tail
        if d + ":" != path[:2].upper() and os.path.isdir(cand):
            return cand
    return ""


def monitor_once():
    try:
        jf_libraries(True)
    except Exception:
        pass
    libs = jf_cache.get("libs") or []
    if not libs:
        return  # Jellyfin injoignable : la carte PC le dit déjà
    # disques / dossiers
    for l in libs:
        key = "lib:" + l["name"]
        pth = l.get("path") or ""
        if pth and not os.path.isdir(pth):
            moved = _find_moved(pth)
            if moved:
                alert(key, "Le dossier de %s a changé de disque : %s est maintenant en %s" % (l["name"], pth[:2], moved[:2]),
                      "Remets la lettre %s au disque : clic droit sur Démarrer → Gestion du disque → clic droit sur %s → Modifier la lettre" % (pth[:2], moved[:2]), "bad")
            else:
                alert(key, "Le dossier de %s est introuvable (%s)" % (l["name"], pth), "Disque débranché ? Rebranche-le, sinon la chaîne ne marche plus", "bad")
        else:
            clear_alert(key)
    # chaînes
    try:
        for c in channel_health(True):
            key = "ch:" + c["id"]
            if c.get("count", 0) == 0:
                alert(key, "La chaîne %s est vide" % c["name"], "Ajoute des vidéos dans sa médiathèque", "bad")
            elif c.get("ghosts") and _try_repair(c["id"]):
                clear_alert(key)  # remis en place tout seul (nouvelle version trouvée)
            elif c.get("ghosts"):
                alert(key, "%s : %d programme%s introuvable%s (%s)" % (c["name"], c["ghosts"], "s" if c["ghosts"] > 1 else "", "s" if c["ghosts"] > 1 else "", ", ".join(c.get("ghost_titles", [])[:3])),
                      "Studio → Chaînes → %s → Retirer les programmes introuvables" % c["name"], "bad")
            else:
                clear_alert(key)
    except Exception as e:
        bglog("surveillance des chaînes : %s" % e)
    # pannes signalées par les télés
    try:
        inc = fb("/incidents") or {}
        last = conf.get("inc_seen", 0)
        new = sorted([v for v in inc.values() if isinstance(v, dict) and v.get("at", 0) > last], key=lambda v: v["at"])
        for v in new:
            when = time.strftime("%H:%M", time.localtime(v["at"] / 1000))
            alert("inc:%s:%s" % (v.get("ch"), v["at"] // 3600000), "%s n'a pas démarré sur %s%s à %s" % (v.get("ch"), v.get("dev") or "une télé", (" (" + v["prof"] + ")") if v.get("prof") else "", when),
                  "Regarde la chaîne dans Studio (vidéo lourde ou introuvable ?)", "warn")
        if new:
            conf["inc_seen"] = new[-1]["at"]
            save_conf(conf)
        if len(inc) > 150:
            for k in sorted(inc, key=lambda k: (inc[k] or {}).get("at", 0))[:len(inc) - 100]:
                fb("/incidents/" + k, "DELETE")
    except Exception as e:
        bglog("incidents : %s" % e)
    for k in [k for k, a in ALERTS.items() if k.startswith("inc:") and time.time() - a["t"] > 86400]:
        ALERTS.pop(k, None)


_autoconv_lock = threading.Lock()


def autoconv_once():
    if not conf.get("auto_convert", True) or not _autoconv_lock.acquire(blocking=False):
        return 0
    try:
        return _autoconv_run()
    finally:
        _autoconv_lock.release()


def _autoconv_run():
    libs = (jf_cache.get("libs") or []) if isinstance(jf_cache, dict) else []
    queued = {x.get("src") for x in jobs if x.get("kind") == "conv" and x["status"] in ("attente", "en cours")}
    n = 0
    for l in libs:
        root = l.get("path") or ""
        if not root or not os.path.isdir(root):
            continue
        for d, dirs, fs in os.walk(root):
            if TRASH_DIR in d:
                continue
            for f in fs:
                pth = os.path.join(d, f)
                if os.path.splitext(f)[1].lower() not in VIDEO_EXT or ".part" in f.lower() or pth in queued:
                    continue
                try:
                    st = os.stat(pth)
                except OSError:
                    continue
                sig = "%d:%d" % (st.st_size, int(st.st_mtime))
                if _autoconv_seen.get(pth) == sig or time.time() - st.st_mtime < 600:
                    continue
                try:
                    info = media_info(pth)
                except Exception:
                    continue
                if not info.get("heavy"):
                    _autoconv_seen[pth] = sig  # vidéo légère : plus besoin de la revérifier
                else:  # lourde : revérifiée à chaque passage tant qu'elle n'est pas convertie (même si Studio redémarre)
                    media_convert([pth], night=True)
                    n += 1
                    bglog("pilote auto : %s sera convertie cette nuit (%s)" % (f, ", ".join(info["heavy"])))
    try:
        with open(os.path.join(DATA, "autoconv.json"), "w", encoding="utf-8") as fh:
            json.dump(_autoconv_seen, fh)
    except OSError:
        pass
    if n:
        away_log("Pilote auto : %d vidéo%s lourde%s seront converties cette nuit" % (n, "s" if n > 1 else "", "s" if n > 1 else ""))
    return n


def autopilot_loop():
    try:
        with open(os.path.join(DATA, "autoconv.json"), encoding="utf-8") as fh:
            _autoconv_seen.update(json.load(fh))
    except Exception:
        pass
    if not conf.get("ntfy_topic"):
        conf["ntfy_topic"] = "frigorose-" + uuid.uuid4().hex[:12]
        save_conf(conf)
    time.sleep(90)
    k = 0
    while True:
        try:
            monitor_once()
        except Exception as e:
            bglog("surveillance : %s" % e)
        if k % 3 == 0:
            try:
                autoconv_once()
            except Exception as e:
                bglog("pilote auto : %s" % e)
        if k % 6 == 3 and TIDY.get("state") not in ("prépare", "renomme", "tunarr"):
            # toutes les heures : beaux titres pour les nouvelles vidéos (converties, téléchargées), puis réparation des chaînes
            try:
                if tidy_plan():
                    tidy_run()
                repair_all_channels()
            except Exception as e:
                bglog("beaux titres auto : %s" % e)
        k += 1
        time.sleep(600)


def media_merge(paths, name=""):
    paths = [x for x in paths if x]
    if len(paths) < 2:
        raise RuntimeError("Coche au moins 2 parties")
    for x in paths:
        _check_inside(x)
    paths = sorted(paths, key=lambda x: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.basename(x))])
    nm = re.sub(r"\s+", " ", (name or "").strip()) or merge_name(paths)
    return new_job("conv", merge=True, srcs=paths, src=paths[0], name=nm, title=nm, sync=True)["id"]


def do_convert(j):
    if j.get("merge"):
        return do_merge(j)
    if j.get("dvd"):
        return do_dvd(j)
    src = j["src"]
    if not os.path.isfile(src):
        raise RuntimeError("Fichier introuvable")
    j["title"] = os.path.basename(src)
    base = os.path.splitext(os.path.basename(src))[0]
    dest_dir = j.get("dest") or os.path.dirname(src)
    os.makedirs(dest_dir, exist_ok=True)
    dst = os.path.join(dest_dir, base + ".mp4")
    same = os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(src))
    if same:
        # déjà un .mp4 au même endroit : on convertit à côté puis on remplace l'original seulement si demandé
        dst = os.path.join(dest_dir, base + " (MP4).mp4") if not j.get("delete") else dst + ".new.mp4"
    elif os.path.exists(dst):
        dst = unique_path(dst)
    notes = ffmpeg_convert(j, src, dst)
    if notes is None:
        return
    if j.get("trash_src") and not same:
        # Médiathèque : l'original part à la corbeille de Studio (récupérable), la version MP4 prend sa place sur la chaîne
        try:
            media_trash([src])
        except Exception:
            pass
    elif j.get("delete") or (j.get("trash_src") and same):
        if same:
            os.replace(dst, src)
            dst = src
        else:
            try:
                os.remove(src)
            except OSError:
                pass
    j["out"] = [dst]
    j["pct"] = 100
    j["msg"] = "Terminé · " + notes


def scan_videos(root, recursive=True):
    out = []
    if recursive:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "$"))]
            out += [os.path.join(d, f) for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.endswith((".part.mp4", ".new.mp4", ".conv.mp4"))]
    else:
        out = [e.path for e in os.scandir(root) if e.is_file() and os.path.splitext(e.name)[1].lower() in VIDEO_EXT]
    return sorted(out, key=lambda x: x.lower())


_probe_cache = {}


def already_ok(path):
    """MP4 déjà en H.264 + AAC : rien à faire (résultat gardé tant que le fichier ne change pas)."""
    if not path.lower().endswith(".mp4"):
        return False
    try:
        st = os.stat(path)
    except OSError:
        return True
    k = (path, st.st_mtime, st.st_size)
    if k not in _probe_cache:
        _probe_cache[k] = not needs_conversion(path)
    return _probe_cache[k]


def queue_folder(root, dest, delete, recursive=True, auto=False):
    """Ajoute toutes les vidéos d'un dossier à convertir. Avec une destination, on garde les sous-dossiers."""
    n = skipped = 0
    with jobs_lock:
        busy = {x["src"] for x in jobs if x["kind"] == "conv" and x["status"] in ("attente", "en cours")}
    for f in scan_videos(root, recursive):
        if f in busy:
            continue
        if already_ok(f):
            skipped += 1
            continue
        d = ""
        if dest:
            rel = os.path.relpath(os.path.dirname(f), root)
            d = dest if rel in (".", "") else os.path.join(dest, rel)
        if converted_exists(f, d):
            skipped += 1
            continue
        new_job("conv", src=f, dest=d, delete=delete, title=os.path.basename(f), auto=auto)
        n += 1
    return n, skipped


def converted_exists(src, dest):
    """Ce fichier a déjà été converti (le MP4 est là) ?"""
    base = os.path.splitext(os.path.basename(src))[0]
    d = dest or os.path.dirname(src)
    same = os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(os.path.dirname(src)))
    if src.lower().endswith(".mp4") and same:
        return os.path.exists(os.path.join(d, base + " (MP4).mp4"))
    return os.path.exists(os.path.join(d, base + ".mp4")) or os.path.exists(os.path.join(d, base + " (MP4).mp4"))


_watch = {"sizes": {}, "seen": set(), "last": 0, "msg": ""}


def watcher():
    """Dossier automatique : tout ce qu'on y dépose est converti tout seul."""
    while True:
        time.sleep(float(os.environ.get("FRS_WATCH_EVERY", "20")))
        try:
            if not (setup["ready"] and conf.get("watch_on") and conf.get("watch_src") and os.path.isdir(conf["watch_src"])):
                _watch["msg"] = ""
                continue
            root, dest = conf["watch_src"], conf.get("watch_dest") or ""
            with jobs_lock:
                known = {x["src"] for x in jobs if x["kind"] == "conv"}
            fresh = []
            for f in scan_videos(root, True):
                if f in known or f in _watch["seen"]:
                    continue
                try:
                    st = os.stat(f)
                except OSError:
                    continue
                # fichier encore en train d'être copié : on attend qu'il ne bouge plus
                prev = _watch["sizes"].get(f)
                _watch["sizes"][f] = (st.st_size, st.st_mtime)
                if prev != (st.st_size, st.st_mtime) or time.time() - st.st_mtime < 10:
                    continue
                _watch["seen"].add(f)
                if already_ok(f) or converted_exists(f, (os.path.join(dest, os.path.relpath(os.path.dirname(f), root)) if dest and os.path.relpath(os.path.dirname(f), root) != "." else dest)):
                    continue
                fresh.append(f)
            for f in fresh:
                rel = os.path.relpath(os.path.dirname(f), root)
                d = "" if not dest else (dest if rel == "." else os.path.join(dest, rel))
                new_job("conv", src=f, dest=d, delete=bool(conf.get("watch_delete")), title=os.path.basename(f), auto=True)
            _watch["last"] = time.time()
            _watch["msg"] = ("%d nouvelle%s vidéo%s ajoutée%s" % ((len(fresh),) + (("s",) * 3 if len(fresh) > 1 else ("",) * 3))) if fresh else "Surveille le dossier"
        except Exception as e:
            _watch["msg"] = "Problème : %s" % e


# ---------------------------------------------------------------- démarrage avec Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_get():
    if not WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "FrigoRoseStudio")
            return True
    except OSError:
        return False


def autostart_set(on):
    if not WIN:
        return False
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        if on:
            exe = sys.executable if getattr(sys, "frozen", False) else '"%s" "%s"' % (sys.executable, os.path.abspath(__file__))
            winreg.SetValueEx(k, "FrigoRoseStudio", 0, winreg.REG_SZ, ('"%s"' % exe if getattr(sys, "frozen", False) else exe) + " --silent")
        else:
            try:
                winreg.DeleteValue(k, "FrigoRoseStudio")
            except OSError:
                pass
    return autostart_get()


def is_playlist(url):
    q = parse_qs(urlparse(url).query)
    return "list" in q and "v" not in q or "/playlist" in url


def do_download(j):
    url, dest = j["url"], j["dest"]
    os.makedirs(dest, exist_ok=True)
    q = j.get("quality") or "1080"
    sort = "res:%s,vcodec:h264,acodec:m4a" % q if q != "max" else "res,vcodec:h264,acodec:m4a"
    sub = (j.get("sub") or "").strip()
    folder = os.path.join(dest, sub) if sub else dest
    if j.get("playlist"):
        tpl = os.path.join(folder, "%(playlist_title)s" if (not sub and j.get("pl_folder", True)) else "", "%(playlist_index)02d - %(title)s.%(ext)s")
    else:
        tpl = os.path.join(folder, "%(title)s.%(ext)s")
    cmd = [tool("yt-dlp"), "--newline", "--no-colors", "--encoding", "utf-8", "--ffmpeg-location", os.path.dirname(tool("ffmpeg")),
           "-S", sort, "--merge-output-format", "mp4", "--remux-video", "mp4", "--embed-metadata", "--embed-chapters", "--parse-metadata", "%(upload_date>%Y)s:%(meta_date)s",
           # Tunarr n'indexe pas les titres avec des émojis (la vidéo n'apparaît pas dans « Add Media ») : on les enlève
           "--replace-in-metadata", "title", EMOJI_RE, "", "--replace-in-metadata", "title", r"\s*[｜|]\s*", " - ",
           "--replace-in-metadata", "title", r"\s{2,}", " ",
           "--no-mtime", "--windows-filenames", "-o", tpl,
           # --print rend yt-dlp silencieux : --progress remet l'avancement
           "--no-quiet", "--progress", "--progress-template", "download:FRP %(progress.downloaded_bytes)s/%(progress.total_bytes)s/%(progress.total_bytes_estimate)s/%(info.playlist_index)s/%(info.n_entries)s/%(progress.speed)s/%(progress.eta)s",
           "--print", "before_dl:FRT %(title)s", "--print", "after_move:FRF %(filepath)s", "--no-simulate"]
    if j.get("thumb", True):
        cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    cmd += ["--yes-playlist" if j.get("playlist") else "--no-playlist", url]
    j["msg"] = "Recherche de la vidéo…"
    p = popen(cmd)
    j["proc"] = p
    files, last_err, part = [], "", 0
    for line in p.stdout:
        line = line.rstrip("\n")
        if line.startswith("FRT "):
            j["title"] = line[4:] if not j.get("playlist") else (j["title"] or "Playlist")
            j["cur"] = line[4:]
            j["msg"] = "Téléchargement : " + line[4:]
        elif line.startswith("FRF "):
            files.append(line[4:].strip())
        elif line.startswith("FRP "):
            try:
                d, t, te, idx, n, sp, eta = (line[4:].split("/") + ["NA", "NA"])[:7]
                tot = float(t) if t not in ("NA", "None", "") else (float(te) if te not in ("NA", "None", "") else 0)
                frac = float(d) / tot if tot else 0
                # vidéo et son arrivent en 2 fichiers : chaque moitié compte pour la moitié de la barre
                if frac < 0.02 and j.get("_lastfrac", 0) > 0.9:
                    part = min(part + 1, 1)
                j["_lastfrac"] = frac
                one = 0.85 * frac if part == 0 else 0.85 + 0.15 * frac  # la vidéo pèse bien plus lourd que le son
                if idx not in ("NA", "None", "") and n not in ("NA", "None", ""):
                    one = (int(idx) - 1 + one) / max(1, int(n))
                j["pct"] = max(0, min(95, 95 * one))
                extra = []
                if sp not in ("NA", "None", ""):
                    extra.append("%.1f Mo/s" % (float(sp) / 1e6))
                if eta not in ("NA", "None", "") and part == 0:
                    e = int(float(eta))
                    extra.append("encore %d min" % max(1, round(e / 60)) if e >= 60 else "encore %d s" % e)
                if extra and j.get("cur"):
                    j["msg"] = "Téléchargement : %s · %s" % (j["cur"], " · ".join(extra))
            except (ValueError, ZeroDivisionError):
                pass
        elif line.startswith("ERROR:"):
            last_err = line[6:].strip()
        elif line.startswith(("[Merger]", "[VideoRemuxer]", "[Metadata]", "[EmbedThumbnail]", "[ThumbnailsConvertor]", "[FixupM3u8]", "[FixupM4a]")):
            # après le téléchargement, yt-dlp réécrit le fichier (assemblage image + son, infos, image) : ça prend un moment sur les longues vidéos
            step = {"[Merger]": ("Assemblage de l'image et du son", 96), "[VideoRemuxer]": ("Mise en MP4", 97), "[Metadata]": ("Ajout des infos", 97.5),
                    "[ThumbnailsConvertor]": ("Préparation de l'image", 98), "[EmbedThumbnail]": ("Ajout de l'image de la vidéo", 98.5)}.get(line.split("]")[0] + "]")
            if step:
                j["pct"] = max(j["pct"], step[1])
                j["msg"] = "%s : %s… (plus long pour les grandes vidéos)" % (step[0], j.get("cur") or "")
        if j["cancel"]:
            p.kill()
    p.wait()
    if j["cancel"]:
        return
    if p.returncode != 0 and not files:
        raise RuntimeError(nice_ytdlp_error(last_err))
    # on vérifie chaque fichier : s'il n'est pas déjà en H.264 + AAC, on le convertit
    final = []
    for k, f in enumerate(files):
        if not os.path.exists(f):
            continue
        if needs_conversion(f):
            out = os.path.splitext(f)[0] + ".mp4"
            tmp_out = out if out != f else out + ".conv.mp4"
            j["pct"] = 95
            notes = ffmpeg_convert(j, f, tmp_out, "%d/%d" % (k + 1, len(files)) if len(files) > 1 else "")
            if notes is None:
                return
            if tmp_out != out:
                os.replace(tmp_out, out)
            elif f != out:
                os.remove(f)
            f = out
        final.append(f)
    j["out"] = final
    j["pct"] = 100
    if j.get("playlist"):
        j["title"] = j.get("title") or "Playlist"
        j["msg"] = "Terminé · %d vidéo%s" % (len(final), "s" if len(final) > 1 else "") + (" (certaines n'ont pas pu être téléchargées)" if p.returncode != 0 else "")
    else:
        j["msg"] = "Terminé"


def nice_ytdlp_error(e):
    s = e or ""
    low = s.lower()
    if "private" in low:
        return "Vidéo privée : impossible de la télécharger"
    if "sign in to confirm your age" in low or "age" in low and "confirm" in low:
        return "Vidéo réservée aux adultes : YouTube demande une connexion"
    if "sign in to confirm" in low or "not a bot" in low:
        return "YouTube bloque temporairement le téléchargement. Réessaie dans un moment"
    if "unsupported url" in low:
        return "Ce lien n'est pas pris en charge"
    if "drm" in low:
        return "Vidéo protégée contre la copie"
    if "unavailable" in low:
        return "Vidéo indisponible"
    if "404" in low or "not found" in low:
        return "Rien à ce lien : vérifie qu'il est complet"
    if "403" in low:
        return "Le site refuse le téléchargement de cette vidéo"
    if "unable to download" in low or "getaddrinfo" in low or "timed out" in low:
        return "Problème de connexion internet"
    return s[:220] or "Téléchargement impossible"


# ---------------------------------------------------------------- explorateur de dossiers
def list_dir(path):
    if not path:
        if WIN:
            drives = ["%s:\\" % d for d in string.ascii_uppercase if os.path.exists("%s:\\" % d)]
            return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives], "files": []}
        path = "/"
    path = os.path.abspath(path)
    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith(".") or e.name.startswith("$"):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": e.path})
                    elif os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                        st = e.stat()
                        files.append({"name": e.name, "path": e.path, "size": st.st_size})
                except OSError:
                    pass
    except OSError as e:
        return {"path": path, "parent": os.path.dirname(path), "dirs": [], "files": [], "error": "Dossier inaccessible"}
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    if parent == path:
        parent = "" if WIN else None
    return {"path": path, "parent": parent, "dirs": dirs, "files": files}


# ---------------------------------------------------------------- accès depuis le téléphone (Tailscale)
import ipaddress, socket
TS_NET = ipaddress.ip_network("100.64.0.0/10")
TS6_NET = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def allowed_client(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        if os.environ.get("FRS_ALLOW_ALL"):
            return True
        return a.is_loopback or (a.version == 4 and a in TS_NET) or (a.version == 6 and a in TS6_NET)
    except ValueError:
        return False


_ts = {"t": 0, "ip": ""}


def tailscale_ip():
    if os.environ.get("FRS_FAKE_TS"):
        return os.environ["FRS_FAKE_TS"]
    if time.time() - _ts["t"] > 30:
        _ts.update(t=time.time(), ip=_tailscale_ip())
    return _ts["ip"]


def _tailscale_ip():
    """Adresse Tailscale de ce PC (100.x.x.x), pour ouvrir Studio depuis le téléphone."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.100.100.100", 53))
        ip = s.getsockname()[0]
        s.close()
        if ipaddress.ip_address(ip) in TS_NET:
            return ip
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ipaddress.ip_address(ip) in TS_NET:
                return ip
    except OSError:
        pass
    return ""



# ---------------------------------------------------------------- état du PC (onglet « PC »)
try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None
_boot = time.time()


def _ua_device(ua):
    u = ua or ""
    if "AFT" in u:
        return "Fire Stick"
    if "FrigoRoseTablet" in u:
        return "Tablette"
    if "FrigoRoseTV" in u:
        return "Télé (appli)"
    if re.search(r"iPhone|Android.*Mobile", u):
        return "Téléphone"
    if "iPad" in u or "Android" in u:
        return "Tablette"
    if "VLC" in u:
        return "VLC"
    return "Navigateur" if "Mozilla" in u else (u.split("/")[0][:30] or "Appareil")


_tsn = {"t": 0, "map": {}}


def tailscale_names():
    """Nom des appareils Tailscale (100.x → « telephone-de-lena »), pour savoir qui regarde."""
    if time.time() - _tsn["t"] < 120:
        return _tsn["map"]
    _tsn["t"] = time.time()
    exe = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if WIN else "")
    m = {}
    try:
        if exe and os.path.exists(exe):
            r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=NOWIN)
            d = json.loads(r.stdout or "{}")
            for peer in list((d.get("Peer") or {}).values()) + ([d["Self"]] if d.get("Self") else []):
                for ip in peer.get("TailscaleIPs") or []:
                    m[ip] = peer.get("HostName") or peer.get("DNSName", "").split(".")[0]
    except Exception:
        pass
    _tsn["map"] = m
    return m


def find_proc(kind):
    """Le processus Tunarr ou Jellyfin qui tourne sur le PC (pour pouvoir le relancer)."""
    if not psutil:
        return None
    pats = {"tunarr": re.compile(r"tunarr", re.I), "jellyfin": re.compile(r"^jellyfin(\.windows\.tray)?(\.exe)?$", re.I)}[kind]
    best = None
    for pr in psutil.process_iter(["pid", "name", "exe", "cmdline", "cwd"]):
        try:
            nm = pr.info["name"] or ""
            cl = " ".join(pr.info["cmdline"] or [])
            if pats.search(nm) or (kind == "tunarr" and "tunarr" in cl.lower() and re.search(r"node|bun", nm, re.I)):
                if kind == "jellyfin" and "tray" in nm.lower():
                    best = best or pr  # le vrai serveur est préféré à l'icône de la barre des tâches
                    continue
                return pr
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return best


def remember_cmd(kind):
    pr = find_proc(kind)
    if not pr:
        return
    try:
        cmd = {"cmdline": pr.info["cmdline"], "cwd": pr.info["cwd"]}
        if cmd["cmdline"] and conf.get(kind + "_cmd") != cmd:
            conf[kind + "_cmd"] = cmd
            save_conf(conf)
    except Exception:
        pass


def start_cmd(kind):
    cmd = conf.get(kind + "_cmd")
    if not cmd or not cmd.get("cmdline"):
        raise RuntimeError("Je ne sais pas encore comment lancer %s : lance-le une fois à la main sur le PC, Studio s'en souviendra." % kind.capitalize())
    flags = (0x00000008 | 0x00000200 | NOWIN) if WIN else 0  # détaché, dans son propre groupe, sans fenêtre
    subprocess.Popen(cmd["cmdline"], cwd=cmd.get("cwd") or None, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, start_new_session=not WIN)


def restart(kind):
    """Relance Tunarr ou Jellyfin (s'il est planté ou bloqué)."""
    if kind == "jellyfin" and conf.get("jellyfin_key"):
        try:
            jf("/System/Restart", "POST")
            return "Jellyfin redémarre (environ 30 s)"
        except Exception:
            pass
    pr = find_proc(kind)
    if pr:
        remember_cmd(kind)
        try:
            for c in pr.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            pr.kill()
            pr.wait(15)
        except psutil.AccessDenied:
            raise RuntimeError("%s tourne comme service Windows : Studio n'a pas le droit de le relancer. Redémarre plutôt le PC." % kind.capitalize())
        except Exception:
            pass
        time.sleep(1.5)
    start_cmd(kind)
    return "%s relancé, il sera prêt dans quelques secondes" % kind.capitalize()


# ---------------------------------------------------------------- Mode absence + battement de cœur
_away_wake = threading.Event()
_away = {"log": [], "down": {"tunarr": 0, "jellyfin": 0}, "last_ok": {}, "beat": 0, "beat_ok": False}


def away_log(msg):
    _away["log"].insert(0, {"t": time.time(), "msg": msg})
    del _away["log"][30:]


def no_sleep(on):
    """Empêche Windows de se mettre en veille tant que Studio tourne (l'écran, lui, peut s'éteindre)."""
    if not WIN:
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED if on else 0))
    except Exception:
        pass


def _svc_ok(kind):
    try:
        if kind == "tunarr":
            tunarr("/api/version", timeout=4)
        else:
            with urllib.request.urlopen(_fast(conf["jellyfin_url"]) + "/System/Info/Public", timeout=4):
                pass
        return True
    except Exception:
        return False


def away_status():
    return {"on": bool(conf.get("away")), "nosleep": bool(conf.get("away")) and WIN, "autostart": autostart_get(), "hidden": bool(conf.get("launch_hidden")),
            "can_start": {k: bool((conf.get(k + "_cmd") or {}).get("cmdline")) for k in ("tunarr", "jellyfin")},
            "tailscale": bool(tailscale_ip()), "beat_ok": _away["beat_ok"], "beat": _away["beat"], "log": _away["log"][:12]}


def away_set(on):
    conf["away"] = bool(on)
    save_conf(conf)
    if on:
        try:
            autostart_set(True)
        except Exception:
            pass
        for k in ("tunarr", "jellyfin"):
            remember_cmd(k)
        away_log("Mode absence activé")
    else:
        away_log("Mode absence désactivé")
    _away_wake.set()  # la boucle (qui garde l'anti-veille) applique tout de suite
    time.sleep(0.6)
    return away_status()


def heartbeat():
    """Dit à la télé et aux téléphones que le PC est allumé (toutes les minutes)."""
    st = {"at": int(time.time() * 1000), "tunarr": _svc_ok("tunarr"), "jellyfin": _svc_ok("jellyfin"), "away": bool(conf.get("away")), "version": VERSION}
    try:
        fb("/studio", "PUT", st)
        _away.update(beat=time.time(), beat_ok=True)
    except Exception:
        _away["beat_ok"] = False
    return st


# ---------- Jellyfin et Tunarr sans fenêtre noire ----------
def _startup_dirs():
    out = []
    for env in ("APPDATA", "PROGRAMDATA"):
        b = os.environ.get(env)
        if b:
            out.append(os.path.join(b, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"))
    return out


def startup_entries():
    """Ce qui lance Jellyfin ou Tunarr avec une fenêtre au démarrage de Windows."""
    res = []
    if not WIN:
        return res
    pat = re.compile(r"tunarr|jellyfin", re.I)
    for d in _startup_dirs():
        try:
            for f in os.listdir(d):
                if pat.search(f):
                    res.append({"kind": "file", "where": d, "name": f})
        except OSError:
            pass
    try:
        import winreg
        for root, rn in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            try:
                with winreg.OpenKey(root, RUN_KEY) as k:
                    i = 0
                    while True:
                        try:
                            n, v, _t = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        i += 1
                        if pat.search(n) or pat.search(str(v)):
                            res.append({"kind": "reg", "where": rn, "name": n, "value": str(v)})
            except OSError:
                pass
    except Exception:
        pass
    return res


def _kill_tree(pr):
    try:
        kids = pr.children(recursive=True)
    except Exception:
        kids = []
    for x in kids + [pr]:
        try:
            x.kill()
        except Exception:
            pass


def hide_services():
    """Coupe les lancements « avec fenêtre » et relance Jellyfin et Tunarr cachés. Studio les lancera ensuite lui-même."""
    if not WIN:
        raise RuntimeError("Seulement sur le PC Windows")
    done = []
    for k in ("tunarr", "jellyfin"):
        remember_cmd(k)
        if not (conf.get(k + "_cmd") or {}).get("cmdline"):
            raise RuntimeError("%s ne tourne pas : lance-le une fois, puis réessaie" % k.capitalize())
    # 1. on range les raccourcis de démarrage (récupérables) et on note les lancements du registre
    bak = os.path.join(DATA, "demarrage-range")
    os.makedirs(bak, exist_ok=True)
    saved = conf.get("run_backup") or []
    for e in startup_entries():
        try:
            if e["kind"] == "file":
                shutil.move(os.path.join(e["where"], e["name"]), unique_path(os.path.join(bak, e["name"])))
                done.append("raccourci « %s » rangé" % e["name"])
            else:
                import winreg
                root = winreg.HKEY_CURRENT_USER if e["where"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
                with winreg.OpenKey(root, RUN_KEY, 0, winreg.KEY_SET_VALUE) as kk:
                    winreg.DeleteValue(kk, e["name"])
                saved.append(e)
                done.append("lancement « %s » retiré" % e["name"])
        except Exception:
            pass
    conf["run_backup"] = saved
    conf["launch_hidden"] = True
    save_conf(conf)
    autostart_set(True)
    # 2. on relance chacun sans fenêtre
    for k in ("tunarr", "jellyfin"):
        pr = find_proc(k)
        if pr:
            _kill_tree(pr)
        time.sleep(2)
        start_cmd(k)
        done.append("%s relancé sans fenêtre" % k.capitalize())
    away_log("Jellyfin et Tunarr tournent maintenant sans fenêtre")
    return done


def services_boot():
    """Au démarrage de Studio : si Jellyfin ou Tunarr ne tournent pas, Studio les lance (cachés)."""
    if not conf.get("launch_hidden"):
        return
    time.sleep(float(os.environ.get("FRS_BOOT_WAIT", "12")))
    for k in ("tunarr", "jellyfin"):
        try:
            if not _svc_ok(k) and not find_proc(k):
                start_cmd(k)
                away_log("%s lancé par Studio (sans fenêtre)" % k.capitalize())
        except Exception as e:
            away_log("Je n'arrive pas à lancer %s (%s)" % (k.capitalize(), e))


def away_loop():
    if conf.get("away"):
        away_log("Studio a démarré, mode absence toujours actif")
    while True:
        try:
            # l'anti-veille est tenu par ce fil-ci, qui ne s'arrête jamais (Windows l'oublie si le fil se termine)
            no_sleep(bool(conf.get("away")))
            st = heartbeat()
            if conf.get("away"):
                for k in ("tunarr", "jellyfin"):
                    if st[k]:
                        if _away["down"][k] >= 2:
                            away_log("%s remarche" % k.capitalize())
                        _away["down"][k] = 0
                        continue
                    _away["down"][k] += 1
                    # 2 minutes sans réponse : on le relance (puis on réessaie toutes les 10 minutes)
                    n = _away["down"][k]
                    if n == 2 or (n > 2 and n % 10 == 0):
                        try:
                            if find_proc(k) and n == 2:
                                restart(k)
                                away_log("%s ne répondait plus : relancé" % k.capitalize())
                            else:
                                start_cmd(k)
                                away_log("%s était arrêté : démarré" % k.capitalize())
                        except Exception as e:
                            away_log("%s ne répond pas et je n'arrive pas à le relancer (%s)" % (k.capitalize(), e))
        except Exception:
            pass
        _away_wake.wait(float(os.environ.get("FRS_BEAT", "60")))
        _away_wake.clear()


def nvidia():
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if WIN else "")
    if not exe or not os.path.exists(exe):
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,encoder.stats.sessionCount",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=6, creationflags=NOWIN)
        a = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        num = lambda v: float(v) if re.match(r"^[\d.]+$", v) else None
        return {"name": a[0], "use": num(a[1]), "temp": num(a[2]), "mem": num(a[3]), "memt": num(a[4]), "enc": num(a[5]) if len(a) > 5 else None}
    except Exception:
        return None


_pc = {"t": 0, "data": None}


def pc_state():
    if _pc["data"] and time.time() - _pc["t"] < 3:
        return _pc["data"]
    d = {"time": time.time()}
    # Tunarr : en marche ? qui regarde ?
    tn = {"ok": False}
    try:
        v = tunarr("/api/version", timeout=3) or {}
        tn.update(ok=True, version=str(v.get("tunarr") or ""))
        chans = {c["id"]: c for c in (tunarr("/api/channels", timeout=5) or [])}
        tn["channels"] = len(chans)
        names = tailscale_names()
        watch = []
        for cid, sess in (tunarr("/api/sessions", timeout=5) or {}).items():
            c = chans.get(cid) or {}
            for se in sess or []:
                for co in se.get("connections") or []:
                    ip = co.get("ip") or ""
                    watch.append({"channel": c.get("name", "Chaîne"), "number": c.get("number"), "ip": ip, "who": names.get(ip) or ("Ce PC" if ip in ("127.0.0.1", "::1") else ""),
                                  "device": _ua_device(co.get("userAgent")), "last": co.get("lastHeartbeat")})
        # qui regarde (profils envoyés par les télés) : on associe par chaîne
        try:
            pres = [v for v in (fb("/presence") or {}).values() if isinstance(v, dict) and time.time() * 1000 - (v.get("at") or 0) < 120000]
        except Exception:
            pres = []
        for w in watch:
            m = next((p for p in pres if _key(p.get("channel")) == _key(w["channel"]) and not p.get("_used")), None)
            if m:
                m["_used"] = True
                w.update(profile=m.get("name"), pic=m.get("pic") or "", av=m.get("av") or 0, own=m.get("own"), device=m.get("device") or w["device"])
        tn["watch"] = sorted(watch, key=lambda w: (w["number"] or 0))
        threading.Thread(target=remember_cmd, args=("tunarr",), daemon=True).start()
    except Exception:
        pass
    d["tunarr"] = tn
    # Jellyfin
    jfs = {"ok": False}
    try:
        with urllib.request.urlopen(_fast(conf["jellyfin_url"]) + "/System/Info/Public", timeout=3) as r:
            info = json.loads(r.read())
        jfs.update(ok=True, version=info.get("Version", ""), name=info.get("ServerName", ""))
        if conf.get("jellyfin_key"):
            try:
                ss = jf("/Sessions?ActiveWithinSeconds=600") or []
                jfs["watch"] = [{"user": x.get("UserName", ""), "client": x.get("Client", ""), "device": x.get("DeviceName", ""),
                                 "item": (x.get("NowPlayingItem") or {}).get("Name", "")} for x in ss if x.get("NowPlayingItem")]
            except Exception:
                pass
        threading.Thread(target=remember_cmd, args=("jellyfin",), daemon=True).start()
    except Exception:
        pass
    d["jellyfin"] = jfs
    d["can_start"] = {k: bool((conf.get(k + "_cmd") or {}).get("cmdline")) for k in ("tunarr", "jellyfin")}
    # disques des médiathèques
    roots = {}
    for l in jf_cache.get("libs") or []:
        r0 = os.path.splitdrive(l["path"])[0] + os.sep if WIN else "/"
        roots.setdefault(r0, []).append(l["name"])
    for f in conf.get("folders") or []:
        r0 = os.path.splitdrive(f)[0] + os.sep if WIN else "/"
        roots.setdefault(r0, [])
    if not roots:
        roots = {(os.path.splitdrive(DATA)[0] + os.sep) if WIN else "/": []}
    disks = []
    for r0, names in roots.items():
        try:
            u = shutil.disk_usage(r0)
            disks.append({"root": r0, "free": u.free, "total": u.total, "libs": sorted(set(names))})
        except OSError:
            pass
    d["disks"] = disks
    if psutil:
        try:
            vm = psutil.virtual_memory()
            d["cpu"] = psutil.cpu_percent(interval=0.3)
            d["ram"] = {"used": vm.used, "total": vm.total, "pct": vm.percent}
            d["uptime"] = time.time() - psutil.boot_time()
        except Exception:
            pass
    d["gpu"] = nvidia()
    d["studio_uptime"] = time.time() - _boot
    _pc.update(t=time.time(), data=d)
    return d


# ---------------------------------------------------------------- abonnements YouTube
def sub_list_ids(url, n=15):
    """Dernières vidéos d'une chaîne ou d'une playlist YouTube (sans les télécharger)."""
    if os.environ.get("FRS_FAKE_SUBS"):
        with open(os.environ["FRS_FAKE_SUBS"], encoding="utf-8") as f:
            d = json.load(f).get(url) or {}
        return d.get("name", ""), d.get("entries", [])
    u = url.rstrip("/")
    if re.search(r"youtube\.com/(@|channel/|c/|user/)", u) and not re.search(r"/(videos|streams|shorts|playlists|featured)$", u):
        u += "/videos"
    r = subprocess.run([tool("yt-dlp"), "--flat-playlist", "-J", "--no-warnings", "--encoding", "utf-8", "--playlist-end", str(n), u],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=NOWIN, env=run_env())
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
        raise RuntimeError(nice_ytdlp_error(err[-1][6:] if err else ""))
    d = json.loads(r.stdout or "{}")
    name = re.sub(r"\s*-\s*(Videos|Vidéos)$", "", d.get("channel") or d.get("uploader") or d.get("title") or "")
    ents = []
    for e in d.get("entries") or []:
        if not e.get("id") or e.get("live_status") in ("is_upcoming", "is_live"):
            continue
        ents.append({"id": e["id"], "title": e.get("title") or "", "url": e.get("url") if (e.get("url") or "").startswith("http") else "https://www.youtube.com/watch?v=" + e["id"]})
    return name, ents


_subs_lock = threading.Lock()


def check_sub(sb, first_take=None):
    name, ents = sub_list_ids(sb["url"])
    if name and not sb.get("name"):
        sb["name"] = name
    seen = set(sb.get("seen") or [])
    uniq, ids = [], set()
    for e in ents:
        if e["id"] not in ids:
            ids.add(e["id"])
            uniq.append(e)
    ents = uniq
    if first_take is not None:
        # à l'ajout : on prend les N dernières, le reste est considéré comme déjà vu
        fresh = ents[:first_take]
        for e in ents[first_take:]:
            seen.add(e["id"])
    else:
        fresh = [e for e in ents if e["id"] not in seen]
    for e in reversed(fresh):
        new_job("dl", url=e["url"], dest=sb["dest"], quality=sb.get("quality") or "1080", sub=sb.get("sub") or "", playlist=False, thumb=True,
                title=e["title"] or e["url"], req="", req_title="", from_sub=sb.get("name") or "", tchan=sb.get("tchan") or "")
        seen.add(e["id"])
    sb["seen"] = list(seen)[-400:]
    sb["last"] = time.time()
    sb["last_new"] = len(fresh)
    sb["error"] = ""
    return len(fresh)


def subs_loop():
    while True:
        time.sleep(float(os.environ.get("FRS_SUBS_TICK", "60")))
        if not setup["ready"]:
            continue
        every = max(0.5, float(conf.get("subs_every") or 3)) * 3600
        for sb in list(conf.get("subs") or []):
            if time.time() - (sb.get("last") or 0) < every:
                continue
            with _subs_lock:
                try:
                    check_sub(sb)
                except Exception as e:
                    sb["error"] = str(e)
                    sb["last"] = time.time()
                save_conf(conf)


# ---------------------------------------------------------------- envoi de fichiers depuis le téléphone
def safe_name(n):
    n = os.path.basename((n or "").replace("\\", "/"))
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", n).strip(" .")
    return n or "video.mp4"


def after_upload(path, dest, tchan="", sync=False):
    """Fichier reçu : s'il est déjà en MP4 H.264, c'est fini ; sinon on le convertit à la même place."""
    if os.path.splitext(path)[1].lower() in VIDEO_EXT and needs_conversion(path):
        return new_job("conv", src=path, dest=dest, delete=True, title=os.path.basename(path), uploaded=True, tchan="" if sync else tchan, sync=sync)
    j = new_job("up", title=os.path.basename(path), out=[path], pct=100, msg="Reçu · déjà en MP4", status="fini", tchan="" if sync else tchan, sync=sync)
    refresh_soon(j)
    return j


# ---------------------------------------------------------------- programmation des chaînes Tunarr (onglet « Chaînes »)
BACKUPS = os.path.join(DATA, "sauvegardes-chaines")
os.makedirs(BACKUPS, exist_ok=True)


def tn(path, method="GET", body=None, timeout=20):
    # pas de « Content-Type: json » sans contenu : Tunarr refuse (400 « Body cannot be empty »)
    req = urllib.request.Request(_fast(conf.get("tunarr_url", "http://localhost:8000")) + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read()
            return json.loads(t) if t.strip() else None
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read() or b"{}").get("message", "")
        except Exception:
            pass
        raise RuntimeError("Tunarr a refusé (%d) %s" % (e.code, msg[:160]))
    except (urllib.error.URLError, OSError):
        raise RuntimeError("Tunarr ne répond pas (il est lancé ?)")


def tn_channels():
    out = []
    for c in tn("/api/channels") or []:
        out.append({"id": c["id"], "number": c.get("number"), "name": (c.get("name") or "").strip(), "icon": (c.get("icon") or {}).get("path", ""),
                    "count": c.get("programCount"), "duration": c.get("duration") or 0})
    return sorted(out, key=lambda c: c["number"] or 0)


def tn_libraries():
    out = []
    for ms in tn("/api/media-sources") or []:
        for l in ms.get("libraries") or []:
            if l.get("enabled", True):
                out.append({"id": l["id"], "name": (l.get("name") or "").strip(), "type": l.get("mediaType") or "", "source": ms.get("id")})
    return sorted(out, key=lambda l: _key(l["name"]))


_libc = {}


_jfids = {}
_libext = {}


def jf_item_ids(parent, fresh=False):
    """Les vidéos qui existent vraiment dans une médiathèque Jellyfin (Tunarr, lui, garde les fichiers supprimés)."""
    c = _jfids.get(parent)
    if c and not fresh and time.time() - c[0] < 30:
        return c[1]
    ids = None
    if conf.get("jellyfin_key") and parent:
        q = "ParentId=%s&Recursive=true&IsFolder=false&MediaTypes=Video&EnableImages=false&EnableUserData=false&Fields=Path&Limit=200000" % quote(parent)
        for path in ("/Items?" + q, None):
            try:
                if path is None:
                    users = jf("/Users") or []
                    u = next((x for x in users if (x.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None)
                    if not u:
                        break
                    path = "/Users/%s/Items?%s" % (u["Id"], q)
                r = jf(path, timeout=20) or {}
                ids = {str(x.get("Id") or "").replace("-", "").lower(): (x.get("Name") or os.path.splitext(os.path.basename(x.get("Path") or ""))[0] or "?")
                       for x in r.get("Items") or [] if x.get("Id") and not x.get("IsFolder")
                       and x.get("Type") not in ("Folder", "CollectionFolder", "Season", "Series", "BoxSet", "PhotoAlbum", "Playlist")
                       and (x.get("MediaType") in (None, "", "Video"))}
                break
            except Exception:
                ids = None
    _jfids[parent] = (time.time(), ids)
    return ids


def _ext_of(pg, x):
    e = pg.get("externalId") or pg.get("externalKey") or x.get("externalKey") or ""
    if not e:
        for i in pg.get("identifiers") or []:
            if i.get("type") == "jellyfin" and i.get("id"):
                e = i["id"]
                break
    return str(e).replace("-", "").lower()


def tn_library_programs(lib_id, fresh=False, alive=True):
    """Tout le contenu d'une médiathèque Tunarr (on passe par sa base, pas par sa recherche qui oublie certains titres).
    alive : on ne garde que ce qui existe encore dans Jellyfin (Tunarr garde les fichiers supprimés ou déplacés)."""
    c = _libc.get(lib_id)
    if c and not fresh and time.time() - c[0] < 30:
        out = c[1]
    else:
        r = tn("/api/media-libraries/%s/programs" % quote(str(lib_id), safe=""), timeout=60)
        arr = r if isinstance(r, list) else (r or {}).get("result") or []
        out = []
        for x in arr:
            pg = x.get("program") or x
            if not pg.get("uuid") and not x.get("id"):
                continue
            out.append({"id": pg.get("uuid") or x.get("id"), "title": pg.get("title") or "", "duration": x.get("duration") or pg.get("duration") or 0,
                        "type": pg.get("type") or "", "year": pg.get("year"), "show": (pg.get("grandparent") or {}).get("title") if isinstance(pg.get("grandparent"), dict) else "",
                        "season": pg.get("seasonNumber") or (pg.get("parent") or {}).get("index") if isinstance(pg.get("parent"), dict) else pg.get("seasonNumber"),
                        "episode": pg.get("episodeNumber") or pg.get("index"), "ext": _ext_of(pg, x), "state": pg.get("state") or ""})
        _libc[lib_id] = (time.time(), out)
    if not alive:
        return out
    out = [x for x in out if x.get("state") in ("", "ok")]
    if lib_id not in _libext:
        try:
            for l in tn_libraries_all():
                _libext[l["id"]] = str(l.get("external") or "")
        except Exception:
            pass
    ids = jf_item_ids(_libext.get(lib_id, ""), fresh)
    if ids:  # Jellyfin a répondu : on écarte les fantômes
        out = [x for x in out if not x.get("ext") or x["ext"] in ids]
    return out


_LINEUP_KEYS = {"content": ("type", "id", "duration"), "custom": ("type", "id", "duration", "customShowId", "index"),
                "filler": ("type", "id", "duration", "fillerListId", "fillerType"), "redirect": ("type", "duration", "channel", "channelNumber", "channelName"),
                "flex": ("type", "duration", "fillerConfig")}


def _clean_item(it):
    keys = _LINEUP_KEYS.get(it.get("type"))
    if not keys:
        return None
    o = {k: it[k] for k in keys if k in it and it[k] is not None}
    if "duration" not in o:
        o["duration"] = it.get("durationMs") or 0
    return o if o.get("duration") else None


def tn_programming(cid):
    r = tn("/api/channels/%s/programming" % cid, timeout=30) or {}
    progs = r.get("programs") or {}
    items = []
    for i, it in enumerate(r.get("lineup") or []):
        t = it.get("type")
        pg = (progs.get(it.get("id")) or {}).get("program") or {}
        title = pg.get("title") or {"flex": "Pause (écran de chaîne)", "redirect": "Renvoi vers « %s »" % it.get("channelName", ""), "custom": "Programme personnalisé"}.get(t, "Programme")
        items.append({"i": i, "type": t, "id": it.get("id"), "title": title, "duration": it.get("duration") or it.get("durationMs") or 0,
                      "lib": pg.get("libraryId") or "", "state": pg.get("state") or "", "ext": _ext_of(pg, it) if pg else ""})
    return {"name": (r.get("name") or "").strip(), "number": r.get("number"), "lineup": items, "raw": r.get("lineup") or []}


# ---------- « Reprendre au début » sur la télé : le programme en cours, lu depuis Jellyfin ----------
def tv_startover(num):
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    try:
        n = float(num)
    except (TypeError, ValueError):
        raise RuntimeError("chaîne inconnue")
    ch = next((c for c in (tn("/api/channels") or []) if c.get("number") is not None and abs(float(c["number"]) - n) < 0.01), None)
    if not ch:
        raise RuntimeError("chaîne inconnue")
    pr = tn_programming(ch["id"])
    items = [x for x in pr["lineup"] if (x.get("duration") or 0) > 0]
    total = sum(x["duration"] for x in items)
    if not total:
        raise RuntimeError("chaîne vide")
    now = time.time() * 1000
    off = (now - float(ch.get("startTime") or 0)) % total
    cur = items[-1]
    for x in items:
        if off < x["duration"]:
            cur = x
            break
        off -= x["duration"]
    if cur.get("type") != "content" or not cur.get("ext"):
        raise RuntimeError("Ce programme ne peut pas être repris au début")
    jid = str(cur["ext"]).replace("-", "")
    return dict(_jf_vod(jid), title=cur.get("title") or "", start=int(now - off), dur=int(cur["duration"]), elapsed=int(off))


def _jf_vod(jid):
    """Lecture d'une vidéo Jellyfin sur la télé (via Studio) : le fichier tel quel quand c'est possible, sinon conversion en bonne qualité."""
    q = urlencode({"MediaSourceId": jid, "VideoCodec": "h264", "AudioCodec": "aac", "AllowVideoStreamCopy": "true", "AllowAudioStreamCopy": "true",
                   "TranscodingMaxAudioChannels": 2, "MaxStreamingBitrate": 40000000, "VideoBitrate": 12000000, "AudioBitrate": 192000,
                   "MaxWidth": 1920, "SegmentContainer": "ts",
                   "MinSegments": 1, "BreakOnNonKeyFrames": "true", "DeviceId": "frigo-rose-tv", "PlaySessionId": uuid.uuid4().hex})
    port = urlparse(_fast(conf["jellyfin_url"])).port or 8096
    return {"path": "/Videos/%s/master.m3u8?%s" % (jid, q), "proxy": "/tv/jf/Videos/%s/master.m3u8?%s" % (jid, q), "port": port}


# ---------- Replay : ce qui est passé sur les chaînes ces 7 derniers jours ----------
_replay = {"t": 0, "items": []}


def tv_replay(days=7):
    if _replay["items"] and time.time() - _replay["t"] < 300:
        return _replay["items"]
    now = time.time() * 1000
    since = now - days * 864e5
    best = {}
    for ch in tn("/api/channels") or []:
        try:
            r = tn("/api/channels/%s/programming" % ch["id"], timeout=30) or {}
        except Exception:
            continue
        progs = r.get("programs") or {}
        lineup = [x for x in (r.get("lineup") or []) if (x.get("duration") or x.get("durationMs") or 0) > 0]
        durs = [x.get("duration") or x.get("durationMs") or 0 for x in lineup]
        total = sum(durs)
        if not total:
            continue
        start0 = float(ch.get("startTime") or 0)
        off = (now - start0) % total
        k, acc = 0, 0
        while k < len(lineup) and acc + durs[k] <= off:
            acc += durs[k]
            k += 1
        end = now - (off - acc)  # début du programme en cours = fin du précédent
        steps = 0
        while end > since and steps < 2000:
            steps += 1
            k = (k - 1) % len(lineup)
            it = lineup[k]
            start = end - durs[k]
            pg = (progs.get(it.get("id")) or {}).get("program") or {}
            ext = _ext_of(pg, it) if pg and it.get("type") == "content" else ""
            if ext and durs[k] >= 5 * 60000 and (pg.get("state") or "ok") == "ok":
                show = ((pg.get("grandparent") or {}).get("title") or pg.get("showTitle") or "")
                prev = best.get(ext)
                if not prev or prev["ended"] < end:
                    best[ext] = {"ext": ext, "id": it.get("id"), "title": pg.get("title") or "", "show": show, "type": pg.get("type") or "",
                                 "season": pg.get("seasonNumber"), "episode": pg.get("episodeNumber"), "year": pg.get("year"),
                                 "ch": (ch.get("name") or "").strip(), "num": ch.get("number"), "aired": int(start), "ended": int(end), "dur": int(durs[k])}
            end = start
    items = sorted(best.values(), key=lambda x: -x["ended"])[:400]
    _replay.update(t=time.time(), items=items)
    return items


# ---------- « Au choix » : sur une chaîne en direct, on choisit le programme qui passe juste après ----------
CHOICES = {}   # id de chaîne -> {"pid", "title", "who", "at"}


def _tv_channel(num):
    try:
        n = float(num)
    except (TypeError, ValueError):
        raise RuntimeError("chaîne inconnue")
    ch = next((c for c in (tn("/api/channels") or []) if c.get("number") is not None and abs(float(c["number"]) - n) < 0.01), None)
    if not ch:
        raise RuntimeError("chaîne inconnue")
    return ch


def _cur_index(ch, raw):
    durs = [x.get("duration") or x.get("durationMs") or 0 for x in raw]
    total = sum(durs)
    if not total:
        raise RuntimeError("chaîne vide")
    off = (time.time() * 1000 - float(ch.get("startTime") or 0)) % total
    k = 0
    while k < len(raw) and off >= durs[k]:
        off -= durs[k]
        k += 1
    return min(k, len(raw) - 1), durs, off


def tv_lineup(num):
    """Ce qu'on peut choisir sur la chaîne : chaque programme une fois, sauf celui en cours."""
    ch = _tv_channel(num)
    r = tn("/api/channels/%s/programming" % ch["id"], timeout=30) or {}
    raw, progs = r.get("lineup") or [], r.get("programs") or {}
    k, durs, off = _cur_index(ch, raw)
    seen, out = set(), []
    n = len(raw)
    for step in range(1, n + 1):
        it = raw[(k + step) % n]
        if it.get("type") != "content" or it.get("id") in seen or it.get("id") == raw[k].get("id"):
            continue
        seen.add(it.get("id"))
        pg = (progs.get(it.get("id")) or {}).get("program") or {}
        show = ((pg.get("grandparent") or {}).get("title") or "")
        ep = ("S%sE%s · " % (pg.get("seasonNumber"), pg.get("episodeNumber"))) if pg.get("seasonNumber") is not None and pg.get("episodeNumber") is not None else ""
        out.append({"id": it.get("id"), "title": (show + " · " + ep if show else "") + (pg.get("title") or "Programme"), "dur": durs[(k + step) % n],
                    "year": pg.get("year"), "next": step == 1})
    left = int(durs[k] - off)
    choice = CHOICES.get(ch["id"])
    if choice and time.time() - choice["at"] > left / 1000 + 600:
        choice = None
    return {"items": out[:300], "left": left, "choice": choice}


def tv_choose_next(num, pid, who=""):
    ch = _tv_channel(num)
    cid = ch["id"]
    r = tn("/api/channels/%s/programming" % cid, timeout=30) or {}
    raw = list(r.get("lineup") or [])
    if not raw:
        raise RuntimeError("chaîne vide")
    k, durs, off = _cur_index(ch, raw)
    if raw[k].get("id") == pid:
        raise RuntimeError("Ce programme passe déjà en ce moment")
    n = len(raw)
    # le prochain passage de ce programme, en partant de maintenant
    j = next(((k + st) % n for st in range(1, n) if raw[(k + st) % n].get("id") == pid), None)
    if j is None:
        raise RuntimeError("Ce programme n'est pas sur la chaîne")
    title = ((r.get("programs") or {}).get(pid) or {}).get("program", {}).get("title") or "Programme"
    if j == (k + 1) % n:
        CHOICES[cid] = {"pid": pid, "title": title, "who": who, "at": time.time()}
        return {"ok": True, "title": title, "already": True}
    start0 = float(ch.get("startTime") or 0)
    tn_backup(cid, raw)
    item = raw.pop(j)
    shift = 0
    if j < k:          # il était plus haut dans la liste : tout ce qui suit remonte, on décale le départ pour ne pas couper le programme en cours
        k -= 1
        shift = durs[j]
    raw.insert(k + 1, item)
    tn_save(cid, raw)  # tn_save recale tout seul le départ : le programme en cours n'est pas coupé
    CHOICES[cid] = {"pid": pid, "title": title, "who": who, "at": time.time()}
    bglog("au choix : %s passe après le programme en cours sur %s (%s)" % (title, ch.get("name"), who or "?"))
    return {"ok": True, "title": title}


def tv_replay_play(ext):
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    it = next((x for x in tv_replay() if x["ext"] == ext), None)
    if not it:
        raise RuntimeError("Ce programme n'est plus en replay")
    jid = str(ext).replace("-", "")
    return dict(_jf_vod(jid), title=it["title"], start=int(time.time() * 1000), dur=it["dur"], elapsed=0)


# ---------- Fiche programme sur la télé : affiche, genres, durée, casting (lus dans Jellyfin) ----------
_info_cache = {}
_INFO_FIELDS = "Overview,Genres,People,ProductionYear,OfficialRating,CommunityRating,RunTimeTicks,Taglines,PremiereDate,ParentId"


def _img(item_id, kind="Primary", w=600):
    return "/tv/img?id=%s&t=%s&w=%d" % (item_id, kind, w)


def tv_info(pid="", ext=""):
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    key = (pid or "") + "|" + (ext or "")
    c = _info_cache.get(key)
    if c and time.time() - c[0] < 1800:
        return c[1]
    if not ext and pid:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", pid):
            raise RuntimeError("programme inconnu")
        pg = tn("/api/programs/%s" % pid, timeout=10) or {}
        ext = _ext_of(pg, {})
    ext = str(ext or "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", ext):
        raise RuntimeError("Pas de fiche pour ce programme")
    r = _jf_items("Ids=%s&Fields=%s" % (ext, _INFO_FIELDS))
    if not r:
        raise RuntimeError("Jellyfin ne connaît pas ce programme")
    x = r[0]
    ep = x.get("Type") == "Episode"
    s = {}
    if ep and x.get("SeriesId"):
        s = (_jf_items("Ids=%s&Fields=%s" % (x["SeriesId"], _INFO_FIELDS)) or [{}])[0]
    tags = x.get("ImageTags") or {}
    stags = s.get("ImageTags") or {}
    # fond : l'image large du film (ou de la série), sinon l'affiche
    if x.get("BackdropImageTags"):
        bd = _img(x["Id"], "Backdrop", 1280)
    elif ep and x.get("ParentBackdropItemId"):
        bd = _img(x["ParentBackdropItemId"], "Backdrop", 1280)
    elif s.get("BackdropImageTags"):
        bd = _img(s["Id"], "Backdrop", 1280)
    else:
        bd = ""
    if ep and stags.get("Primary"):
        poster = _img(s["Id"], "Primary", 500)
    elif tags.get("Primary"):
        poster = _img(x["Id"], "Primary", 500)
    else:
        poster = ""
    logo = _img(x["Id"], "Logo", 700) if tags.get("Logo") else (_img(s["Id"], "Logo", 700) if stags.get("Logo") else "")
    people = x.get("People") or s.get("People") or []
    cast = [{"name": p.get("Name"), "role": (p.get("Role") or "")[:40],
             "img": _img(p["Id"], "Primary", 200) if p.get("PrimaryImageTag") else ""}
            for p in people if p.get("Type") == "Actor" and p.get("Name")][:6]
    direc = [p.get("Name") for p in people if p.get("Type") == "Director" and p.get("Name")][:2]
    rt = x.get("RunTimeTicks") or s.get("RunTimeTicks") or 0
    out = {"kind": "episode" if ep else "movie",
           "title": (s.get("Name") or x.get("SeriesName")) if ep else x.get("Name"),
           "episode": x.get("Name") if ep else "",
           "season": x.get("ParentIndexNumber"), "number": x.get("IndexNumber"),
           "year": x.get("ProductionYear") or s.get("ProductionYear"),
           "genres": (x.get("Genres") or s.get("Genres") or [])[:3],
           "rating": round(float(x.get("CommunityRating") or s.get("CommunityRating") or 0), 1) or None,
           "official": x.get("OfficialRating") or s.get("OfficialRating") or "",
           "minutes": int(rt / 600000000) if rt else None,
           "overview": (x.get("Overview") or s.get("Overview") or "")[:700],
           "tagline": ((x.get("Taglines") or s.get("Taglines") or [""])[0] or "")[:140],
           "backdrop": bd, "poster": poster, "logo": logo, "cast": cast, "directors": direc}
    _info_cache[key] = (time.time(), out)
    if len(_info_cache) > 400:
        for k in sorted(_info_cache, key=lambda k: _info_cache[k][0])[:100]:
            _info_cache.pop(k, None)
    return out


# ---------- Beaux titres : nettoie les noms de fichiers (Friends.S01E05.MULTI.1080p… → « Friends · S1 E5 ») ----------
_nt_TECH = set("""480p 576p 720p 1080p 1080i 2160p 4k uhd hdr hdr10 sdr 10bit 8bit x264 x265 h264 h265 hevc avc aac ac3 eac3 dts dd ddp dd+ truehd atmos
vof vfi2 vff2 web webrip web-dl webdl bluray blu-ray bdrip brrip dvdrip hdtv hdtv-rip hdrip tvrip rip remux multi multi-vff vff vfq vf vf2 vfi vo vost vostfr french truefrench
subfrench internal repack proper nf amzn dsnp hulu hmax ad ld doc complete integrale mhd light custom remastered remaster happylee""".split())
_nt_ACC = {"fantome": "fantôme", "etoiles": "étoiles", "etoile": "étoile", "amerique": "Amérique", "greve": "grève", "noel": "Noël", "lycee": "lycée",
        "epreuve": "épreuve", "romeo": "Roméo", "depart": "départ", "gout": "goût", "invites": "invités", "peres": "pères", "madere": "Madère",
        "mouvementees": "mouvementées", "rentree": "rentrée", "separations": "séparations", "tournee": "tournée", "sirenes": "sirènes",
        "parait": "paraît", "recompose": "recomposé", "recompence": "récompense", "recompense": "récompense", "ecole": "école", "ete": "été",
        "premiere": "première", "derniere": "dernière", "pere": "père", "mere": "mère", "frere": "frère", "freres": "frères", "soeur": "sœur",
        "coeur": "cœur", "reve": "rêve", "fete": "fête", "fetes": "fêtes", "verite": "vérité", "eleve": "élève", "eleves": "élèves", "hopital": "hôpital",
        "chateau": "château", "medecin": "médecin", "seance": "séance", "metier": "métier", "detective": "détective",
        "hyeres": "Hyères", "senas": "Sénas", "etre": "être", "ca": "ça", "enigme": "énigme", "scene": "scène", "deja": "déjà", "theatre": "théâtre",
        "cinema": "cinéma", "telephone": "téléphone", "television": "télévision", "difference": "différence", "enquete": "enquête", "eclair": "éclair",
        "epoque": "époque", "etrange": "étrange", "equipe": "équipe", "etat": "état", "carriere": "carrière", "mariee": "mariée", "marie": "marié",
        "aine": "aîné", "ile": "île", "foret": "forêt", "reunion": "réunion", "decouverte": "découverte", "generation": "génération", "heros": "héros"}
_nt_CAPS_KEEP = {"TV", "IA", "COP", "USA", "UK", "OK", "DJ", "VIP", "SOS", "BD", "BDE", "RAID", "M6", "TF1", "PSG", "OM", "II", "III", "IV", "S.O.S.", "XXL", "M6"}
_nt_PROPER = {"japon": "Japon", "danielle": "Danielle", "france": "France", "paris": "Paris", "disneyland": "Disneyland", "gaza": "Gaza",
           "israel": "Israël", "russie": "Russie", "noel": "Noël", "brigitte": "Brigitte", "jerry": "Jerry", "tom": "Tom", "mickey": "Mickey",
           "patrick": "Patrick", "gerard": "Gérard", "francais": "Français", "macron": "Macron", "europe": "Europe", "marseille": "Marseille", "capital": "Capital", "philippe": "Philippe", "etchebest": "Etchebest"}
_nt_JUNK = {"prime", "episodecomplet", "compilationfr", "megacompilation", "m6mag", "zoneinterdite", "verino"}
_nt_SEP = re.compile(r"[._]+")


def _nt_n(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode())


def _nt_is_tech(w):
    lw = w.lower().strip("[]()")
    if lw in _nt_TECH or lw in ("5.1", "7.1", "2.0"):
        return True
    return bool(re.fullmatch(r"\d{3,4}[pi]|[xh]\.?26[45]|ddp?\d?(\.\d)?|\d+bit|web-?dl|hdtv-?rip|ac3-\w+|[xh]26[45]-\w+|h26[45]-\w+|aac-\w+", lw))


def _nt_fix_words(s, apos=True):
    if apos:
        s = re.sub(r"(?<![.\w])([lLdDsSjJnNmMcC]|[qQ]u) (?=[aeiouyhéèêàâîôûAEIOUYHÉ])", lambda m: m.group(1) + "'", s)
    out = []
    for w in s.split(" "):
        pre = ""
        if "'" in w[:3]:
            pre, w = w[:w.index("'") + 1], w[w.index("'") + 1:]
        k = _nt_n(w)
        if k in _nt_ACC and w.lower() == unicodedata.normalize("NFD", w.lower()).encode("ascii", "ignore").decode():
            r = _nt_ACC[k]
            w = (r[0].upper() + r[1:]) if w[:1].isupper() and r[:1].islower() else r
        out.append(pre + w)
    return " ".join(out)


def _nt_sentence(s):
    """Les mots tout en majuscules repassent en minuscules (« DANIELLE AU JAPON » → « Danielle au Japon »)."""
    words = s.split(" ")
    L = lambda w: re.sub(r"[^A-Za-zÀ-ÿŒœ]", "", w)
    caps = {i for i, w in enumerate(words) if len(L(w)) >= 2 and L(w).isupper() and not re.search(r"\d", w) and w.strip("!?.,:;()'\"#") not in _nt_CAPS_KEEP}
    if len(caps) < 2:
        return s
    res = []
    for i, w in enumerate(words):
        if i in caps:
            lw = w.lower()
            res.append(_nt_PROPER.get(_nt_n(lw), lw))
        else:
            res.append(w)
    t = " ".join(res)
    m = re.search(r"[a-zà-ÿœ]", t)
    if m and not re.search(r"[A-ZÀ-Ý0-9]", t[:m.start()]):
        t = t[:m.start()] + t[m.start()].upper() + t[m.start() + 1:]
    return t


def _nt_cap1(s):
    s = s.strip()
    return s[:1].upper() + s[1:] if s else s


def _nt_strip_tech(s, first=False, years=True):
    """Coupe au premier mot technique (FRENCH, 1080p, x264…) ou à l'année suivie de technique."""
    s = re.sub(r"\[[^\]]*\]", " ", s)
    toks = [t for t in re.split(r"[ ._]+", s) if t]
    year = None
    cut = len(toks)
    Y = lambda x: bool(re.fullmatch(r"\(?(19|20)\d\d\)?", x or ""))
    for i, t in enumerate(toks):
        if _nt_is_tech(t) and (i > 0 or first):
            cut = i
            break
        if years and Y(t) and i > 0 and (i + 1 >= len(toks) or _nt_is_tech(toks[i + 1]) or Y(toks[i + 1]) or re.fullmatch(r"(?i)s\d+e\d+", toks[i + 1])):
            year = int(t.strip("()"))
            cut = i
            break
    return " ".join(toks[:cut]).strip(" -–|"), year


_nt_EP = [re.compile(p, re.I) for p in (
    r"(?:^|[ ._-])S(\d{1,2})[ ._]?E[ ._]?(\d{1,3})(?=$|[ ._-])",
    r"(?:^|[ ._-])S?(\d{1,2})x(\d{1,3})(?=$|[ ._-])",
    r"(?:^|[ ._-])(\d{1,2})E(\d{1,3})(?=$|[ ._-])",
    r"(?:^|[ ._-])S(\d{1,2})()(?=[ ._-])",
    r"(?:^|[ ._-])()E(\d{1,3})(?=[ ._-])")]


def _nt_segments(t, show, junk=()):
    parts = [p.strip() for p in re.split(r"\s+[-|]+\s+|\s*/\s*/\s*|\s+[-|]+$", t) if p.strip()]
    keep = [p for p in parts if _nt_n(p) not in _nt_JUNK and _nt_n(p) not in junk and not p.startswith("@") and (not show or _nt_n(p) not in (_nt_n(show), _nt_n(show + "tv")))]
    return " - ".join(keep or parts[:1])


def nice_title(name, show=None, chan=""):
    """(titre propre, année) ; (None, None) si le nom est déjà bien."""
    o = (name or "").strip()
    if not o or re.fullmatch(r"(MVI|IMG|DSC|VID|PXL|GOPR)[_ -]?\d+|\d+", o, re.I):
        return None, None
    if " · " in o:  # déjà un beau titre (Friends · S1 E5)
        return None, None
    t = re.sub(r"\.(mkv|mp4|avi|m4v|mov|ts|wmv)$", "", o, flags=re.I)
    t = t.replace("：", ":").replace("｜", "|").replace("？", "?").replace("⧸", "/").replace("’", "'").replace("…", "")
    t = re.sub(r"^\((?:M6|TF1|W9|C8)\)\s*", "", t)
    t = re.sub(r"(?i)(\d+)[ ._]pourcent\b", r"\1%", t)
    t = re.sub(r"(?i)[ ._]avec[ ._]philippe[ ._]etchebest", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    junk = {_nt_n(chan), _nt_n(re.sub(r"(?i)\s+tv\s*$", "", chan or ""))} - {""}
    year = None
    m = re.match(r"^((?:19|20)\d\d)\s*-\s*(.+?)[\s-]*$", t)
    if m and not re.match(r"^\d", m.group(2)):
        year, t = int(m.group(1)), m.group(2)
    words = [w for w in re.split(r"[ ._\[\]]+", t) if w]
    filelike = (bool(re.search(r"\w[._]\w", t)) and " " not in t) or sum(1 for w in words[1:] if _nt_is_tech(w)) >= 2
    sea = ep = None
    eptitle = None
    for rx in _nt_EP:
        m = rx.search(t)
        if m:
            sea = int(m.group(1)) if m.group(1) else None
            ep = int(m.group(2)) if m.group(2) else None
            before, after = t[:m.start()], t[m.end():]
            after = re.sub(r"(?i)[ .]team[ .].*$", "", after)
            after = re.sub(r"(?i)^[ .]*final\b", "", after)
            after = _nt_strip_tech(after, first=True, years=False)[0].strip(" .-–")
            after = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}-\d{2}-\d{4}\b", "", after)
            eptitle = after
            if not show:
                show = _nt_cap1(_nt_SEP.sub(" ", _nt_strip_tech(before)[0]))
            break
    if sea is None and ep is None and show:
        m = re.match(r"^(\d{1,2})\s+-\s+(.+)$", t)  # « 04 - Marjorie au régime - EN FAMILLE »
        if m:
            ep, eptitle = int(m.group(1)), m.group(2)
        elif _nt_n(t).startswith(_nt_n(show)) and _nt_n(t) != _nt_n(show):
            k = len(show.split())
            rest = " ".join(re.split(r"[ ._]+", t)[k:])
            rest = re.sub(r"(?i)[ .]team[ .].*$", "", rest)
            bits = [_nt_strip_tech(b, first=True, years=False)[0].strip(" ()-.,") for b in re.split(r"\s+-\s+", rest)]
            bits = [b for b in bits if b]
            if bits:
                new = show + " · " + _nt_cap1(_nt_fix_words(_nt_sentence(" - ".join(bits)), False))
                return (new, None) if new != o else (None, None)
    if sea is not None or ep is not None:
        et = _nt_segments(_nt_SEP.sub(" ", eptitle or ""), show, junk)
        et = _nt_fix_words(et, filelike).strip(" -.,")
        et = re.sub(r"^\((.+)\)$", r"\1", et)
        if _nt_n(et) == _nt_n(show):
            et = ""
        code = ("S%d " % sea if sea is not None else "") + ("E%d" % ep if ep is not None else "")
        new = " · ".join(x for x in [show, code.strip(), _nt_cap1(_nt_sentence(et))] if x)
    else:
        if filelike:
            t, y = _nt_strip_tech(t)
            year = year or y
            t = _nt_SEP.sub(" ", t)
        else:
            t2 = re.sub(r"\s*\((19|20)\d\d\)\s*$", "", t)
            if t2 != t:
                year = year or int(t.rstrip()[-5:-1])
                t = t2
        t = _nt_segments(t, show, junk)
        new = _nt_cap1(_nt_fix_words(_nt_sentence(t), filelike).strip(" -.,"))
    new = re.sub(r"\s+", " ", new).strip()
    if not new or new == o:
        return None, None
    return new, year


TIDY = {"state": "", "plan": [], "done": 0, "total": 0, "err": "", "t": 0}
TIDY_BACKUP = os.path.join(DATA, "titres-avant.json")


def _jf_admin():
    users = jf("/Users") or []
    u = next((x for x in users if (x.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None)
    return u["Id"] if u else None


def tidy_plan():
    """Ce qui serait renommé (rien n'est changé ici)."""
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    chans = sorted([c for c in (tn("/api/channels") or []) if c.get("id")], key=lambda c: float(c.get("number") or 0))
    def prog(c):
        try:
            return c, tn_programming(c["id"])
        except Exception:
            return c, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        per = list(ex.map(prog, chans))
    owner, ids = {}, []
    for c, pr in per:
        for x in (pr or {}).get("lineup") or []:
            e = x.get("ext")
            if x.get("type") == "content" and e and e not in owner:
                owner[e] = c
                ids.append(e)
    info = {}
    for i in range(0, len(ids), 80):
        for it in _jf_items("Ids=%s&Fields=ProviderIds,ProductionYear,Path" % ",".join(ids[i:i + 80])):
            info[str(it.get("Id") or "").replace("-", "").lower()] = it
    # les chaînes faites d'épisodes (Friends S01E05…) : le nom de la chaîne sert de nom de série
    bych = {}
    for e in ids:
        it = info.get(e)
        if it:
            bych.setdefault(owner[e]["id"], []).append(it)
    show_of = {}
    for c, _ in per:
        its = bych.get(c["id"]) or []
        vids = [x for x in its if x.get("Type") == "Video"]
        eps = sum(1 for x in vids if any(rx.search(x.get("Name") or "") for rx in _nt_EP[:3]) or re.match(r"^\d{1,2}\s+-\s+", x.get("Name") or ""))
        if len(vids) >= 3 and eps * 2 >= len(vids):
            show_of[c["id"]] = re.sub(r"(?i)\s+tv\s*$", "", (c.get("name") or "").strip())
    plan = []
    for e in ids:
        it = info.get(e)
        if not it:
            continue
        c = owner[e]
        if re.search(r"(?i)vid[ée]o.*famille|souvenir", c.get("name") or ""):
            continue
        t, name = it.get("Type"), it.get("Name") or ""
        if t == "Movie":
            pids = it.get("ProviderIds") or {}
            if pids.get("Tmdb") or pids.get("Imdb"):
                continue
            new, year = nice_title(name, chan=c.get("name") or "")
            plan.append({"id": e, "type": t, "old": name, "new": new or name, "year": year or it.get("ProductionYear"), "action": "identify", "chan": c.get("name")})
        elif t == "Video":
            new, year = nice_title(name, show_of.get(c["id"]), c.get("name") or "")
            if new:
                plan.append({"id": e, "type": t, "old": name, "new": new, "year": None if show_of.get(c["id"]) else year, "action": "rename", "chan": c.get("name")})
    # tous les films des médiathèques « films », même ceux pas encore sur une chaîne
    have = {x["id"] for x in plan}
    try:
        libs = jf_libraries(force=True)["libs"]
    except Exception:
        libs = []
    seenlib = set()
    for l in libs:
        if l.get("type") != "movies" or not l.get("item") or l["item"] in seenlib:
            continue
        seenlib.add(l["item"])
        for it in _jf_items("ParentId=%s&Recursive=true&IncludeItemTypes=Movie&Fields=ProviderIds,ProductionYear,Path" % l["item"]):
            e = str(it.get("Id") or "").replace("-", "").lower()
            pids = it.get("ProviderIds") or {}
            if e in have or pids.get("Tmdb") or pids.get("Imdb"):
                continue
            fn = os.path.splitext(os.path.basename(it.get("Path") or ""))[0] or it.get("Name") or ""
            new, year = nice_title(fn)
            if not new:
                new, year = (it.get("Name") or fn), None
            plan.append({"id": e, "type": "Movie", "old": it.get("Name") or fn, "new": new, "year": year or it.get("ProductionYear"),
                         "action": "identify", "chan": l.get("name")})
            have.add(e)
    return plan


def _tidy_identify(x):
    q = x["new"]
    r = media_idsearch(x["id"], "Movie", q, x.get("year") or "")
    best, score = None, 0
    for c in r[:6]:
        s = difflib.SequenceMatcher(None, _nt_n(q), _nt_n(c.get("Name"))).ratio()
        if x.get("year") and c.get("ProductionYear"):
            dy = abs(int(c["ProductionYear"]) - int(x["year"]))
            s += .15 if dy == 0 else .05 if dy == 1 else -.3
        if s > score:
            best, score = c, s
    if best and score >= .82:
        jf("/Items/RemoteSearch/Apply/%s?ReplaceAllImages=true" % quote(x["id"]), "POST", best, timeout=60)
        return best.get("Name")
    return None


def _tidy_rename(uid, x):
    dto = jf("/Users/%s/Items/%s" % (uid, x["id"]), timeout=15) or {}
    if not dto.get("Id"):
        raise RuntimeError("introuvable")
    dto["Name"] = x["new"]
    if x.get("year") and not dto.get("ProductionYear"):
        dto["ProductionYear"] = int(x["year"])
    lk = set(dto.get("LockedFields") or [])
    lk.add("Name")
    dto["LockedFields"] = sorted(lk)
    jf("/Items/%s" % x["id"], "POST", dto, timeout=20)


def tidy_run(ids=None):
    try:
        TIDY.update(state="prépare", err="", done=0)
        plan = tidy_plan()
        if ids:
            plan = [x for x in plan if x["id"] in ids]
        TIDY.update(plan=plan, total=len(plan), state="renomme")
        try:
            with open(TIDY_BACKUP, "r", encoding="utf-8") as f:
                back = json.load(f)
        except Exception:
            back = {}
        uid = _jf_admin()
        for x in plan:
            try:
                back.setdefault(x["id"], x["old"])
                if x["action"] == "identify":
                    got = _tidy_identify(x)
                    if got:
                        x["new"], x["result"] = got, "identifié"
                    else:
                        _tidy_rename(uid, x)
                        x["result"] = "renommé"
                else:
                    _tidy_rename(uid, x)
                    x["result"] = "renommé"
            except Exception as e:
                x["result"] = "erreur : %s" % e
            TIDY["done"] += 1
        with open(TIDY_BACKUP, "w", encoding="utf-8") as f:
            json.dump(back, f, ensure_ascii=False, indent=1)
        TIDY["state"] = "tunarr"
        time.sleep(20)
        for t in tn_libraries_all():
            try:
                tn_scan_lib(t["id"], every=0)
            except Exception:
                pass
        _catalog.update(t=0)
        _info_cache.clear()
        TIDY.update(state="fini", t=time.time())
    except Exception as e:
        TIDY.update(state="erreur", err=str(e))


# ---------- Vidéoclub : chaque chaîne est un « univers » qu'on regarde à la demande ----------
_vod = {"chans": [], "by": {}, "ok": set()}
_EPNUM = re.compile(r"(?i)\bS(\d{1,2})\s?E(\d{1,3})\b|(?:^|·\s)E(\d{1,3})\b")


def _vod_index(perch, info):
    chans, by, ok = [], {}, set()
    for num, name, exts in perch:
        items = []
        for e in exts:
            it = info.get(e)
            if not it:
                continue
            t = it.get("Type")
            nm = it.get("Name") or ""
            m = _EPNUM.search(nm)
            se = (int(m.group(1)), int(m.group(2))) if m and m.group(1) else ((0, int(m.group(3))) if m and m.group(3) else None)
            rt = it.get("RunTimeTicks") or 0
            items.append({"jid": e, "title": nm, "year": it.get("ProductionYear"), "type": t,
                          "img": _img(e, "Primary", 480) if (it.get("ImageTags") or {}).get("Primary") else "",
                          "dur": int(rt / 10000) if rt else 0, "se": se, "desc": (it.get("Overview") or "")[:240],
                          "genres": (it.get("Genres") or [])[:3]})
            ok.add(e)
        if not items:
            continue
        movies = sum(1 for x in items if x["type"] == "Movie")
        eps = sum(1 for x in items if x["se"])
        kind = "movies" if movies * 2 >= len(items) else "series" if eps * 2 >= len(items) else "videos"
        if kind == "series":
            items.sort(key=lambda x: (x["se"] or (999, 999), x["title"]))
        else:
            items.sort(key=lambda x: unicodedata.normalize("NFD", (x["title"] or "").lower()).encode("ascii", "ignore"))
        posters = [x["img"] for x in items if x["img"] and x["type"] == "Movie"][:3] or [x["img"] for x in items if x["img"]][:3]
        chans.append({"num": num, "name": name, "n": len(items), "kind": kind, "posters": posters})
        by[str(num)] = {"num": num, "name": name, "kind": kind, "items": items}
    _vod.update(chans=chans, by=by, ok=ok)


def tv_vod_play(ext):
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    ext = str(ext or "").replace("-", "").lower()
    if not _vod["ok"]:
        tv_catalog()
    if ext not in _vod["ok"]:
        raise RuntimeError("Ce programme n'est plus sur les chaînes")
    it = next((x for c in _vod["by"].values() for x in c["items"] if x["jid"] == ext), {})
    return dict(_jf_vod(ext), title=it.get("title") or "", start=int(time.time() * 1000), dur=it.get("dur") or 0, elapsed=0)


# ---------- Catalogue : tout ce qui passe sur les chaînes, avec les affiches ----------
_catalog = {"t": 0, "items": [], "busy": False}
_catalog_lock = threading.Lock()


def _catalog_build():
    chans = sorted([c for c in (tn("/api/channels") or []) if c.get("id")], key=lambda c: float(c.get("number") or 0))
    seen = {}  # ext -> [{num, name, pid}]
    order = []
    perch = []  # [(num, name, [ext…])] dans l'ordre des chaînes

    def prog(c):
        try:
            return c, tn_programming(c["id"])
        except Exception:
            return c, None
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(prog, chans))
    for c, pr in res:
        if not pr:
            continue
        num = c.get("number")
        num = int(num) if isinstance(num, (int, float)) and float(num).is_integer() else num
        mine = []
        perch.append((num, (c.get("name") or "").strip(), mine))
        for x in pr["lineup"]:
            e = x.get("ext")
            if x.get("type") != "content" or not e or x.get("state") not in ("", "ok"):
                continue
            if e not in mine:
                mine.append(e)
            if e not in seen:
                seen[e] = []
                order.append(e)
            if not any(s["num"] == num for s in seen[e]):
                seen[e].append({"num": num, "name": (c.get("name") or "").strip(), "pid": x.get("id")})
    info = {}
    ids = list(seen)
    for i in range(0, len(ids), 80):
        for it in _jf_items("Ids=%s&Fields=Genres,ProductionYear,DateCreated,Overview" % ",".join(ids[i:i + 80])):
            info[str(it.get("Id") or "").replace("-", "").lower()] = it
    _vod_index(perch, info)
    out, groups = [], {}
    for e in order:
        it = info.get(e)
        if not it:
            continue
        t = it.get("Type")
        ch = seen[e]
        if t == "Episode" and it.get("SeriesId"):
            key = "s:" + it["SeriesId"]
            g = groups.get(key)
            if not g:
                g = groups[key] = {"key": key, "kind": "series", "title": it.get("SeriesName") or it.get("Name"), "jid": it["SeriesId"],
                                   "year": it.get("ProductionYear"), "genres": [], "n": 0, "chans": [], "added": it.get("DateCreated") or "",
                                   "poster": _img(it["SeriesId"], "Primary", 360)}
                out.append(g)
            g["n"] += 1
            g["year"] = min(filter(None, [g["year"], it.get("ProductionYear")]), default=None)
            g["added"] = max(g["added"], it.get("DateCreated") or "")
            for s in ch:
                if not any(z["num"] == s["num"] for z in g["chans"]):
                    g["chans"].append({"num": s["num"], "name": s["name"]})
        elif t == "Movie":
            out.append({"key": "m:" + e, "kind": "movie", "title": it.get("Name"), "jid": e, "year": it.get("ProductionYear"),
                        "genres": (it.get("Genres") or [])[:3], "chans": ch, "added": it.get("DateCreated") or "",
                        "poster": _img(e, "Primary", 360) if (it.get("ImageTags") or {}).get("Primary") else ""})
        else:
            # vidéos YouTube, souvenirs… : une seule case par chaîne
            for s in ch[:1]:
                key = "v:%s" % s["num"]
                g = groups.get(key)
                if not g:
                    g = groups[key] = {"key": key, "kind": "videos", "title": s["name"], "jid": "", "year": None, "genres": [],
                                       "n": 0, "chans": [{"num": s["num"], "name": s["name"]}], "added": "",
                                       "poster": _img(e, "Primary", 360) if (it.get("ImageTags") or {}).get("Primary") else ""}
                    out.append(g)
                g["n"] += 1
                g["eps"] = g.get("eps", 0) + (1 if re.search(r"(?i)\bs\d{1,2}\s?e\s?\d{1,3}\b|\b\d{1,2}x\d{2}\b|\bS?\d{1,2}E\d{1,3}\b", it.get("Name") or "") else 0)
                g["added"] = max(g["added"], it.get("DateCreated") or "")
    # une chaîne de vidéos qui est en fait une série (Friends S01E05…) : on l'affiche comme une série
    for g in out:
        if g["kind"] == "videos":
            g["coll"] = True
            g["title"] = re.sub(r"(?i)\s+tv$", "", g["title"]).strip() or g["title"]
            if g.pop("eps", 0) * 2 >= g["n"] and g["n"] >= 3:
                g["kind"] = "series"
    # genres des séries : lus sur la série elle-même
    sids = [g["jid"] for g in out if g["kind"] == "series" and not g.get("coll")]
    for i in range(0, len(sids), 80):
        for it in _jf_items("Ids=%s&Fields=Genres,ProductionYear" % ",".join(sids[i:i + 80])):
            g = groups.get("s:" + str(it.get("Id")))
            if g and not g.get("coll"):
                g["genres"] = (it.get("Genres") or [])[:3]
                g["year"] = it.get("ProductionYear") or g["year"]
                if not (it.get("ImageTags") or {}).get("Primary"):
                    g["poster"] = ""
    out.sort(key=lambda x: unicodedata.normalize("NFD", (x["title"] or "").lower()).encode("ascii", "ignore"))
    return out


def tv_catalog(fresh=False):
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Studio n'est pas relié à Jellyfin")
    age = time.time() - _catalog["t"]
    if _catalog["items"] and age < 900 and not fresh:
        return _catalog["items"]

    def run():
        try:
            items = _catalog_build()
            _catalog.update(t=time.time(), items=items)
        except Exception as e:
            bglog("catalogue : %s" % e)
        finally:
            _catalog["busy"] = False
    if _catalog["items"]:
        # on rend l'ancien tout de suite et on met à jour derrière
        with _catalog_lock:
            if not _catalog["busy"]:
                _catalog["busy"] = True
                threading.Thread(target=run, daemon=True).start()
        return _catalog["items"]
    with _catalog_lock:
        if not _catalog["items"]:
            _catalog["busy"] = True
            run()
    if not _catalog["items"]:
        raise RuntimeError("Le catalogue n'a pas pu être préparé")
    return _catalog["items"]


def tn_backup(cid, raw):
    f = os.path.join(BACKUPS, "%s-%d.json" % (cid, int(time.time() * 1000)))
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    # on garde les 20 dernières sauvegardes par chaîne
    mine = sorted(x for x in os.listdir(BACKUPS) if x.startswith(cid + "-"))
    for old in mine[:-20]:
        try:
            os.remove(os.path.join(BACKUPS, old))
        except OSError:
            pass


def _time_snap(cid):
    """Avant de changer une chaîne : quel programme passe, et où on en est dedans."""
    try:
        ch = tn("/api/channels/%s" % quote(cid, safe="")) or {}
        raw = (tn("/api/channels/%s/programming" % cid, timeout=30) or {}).get("lineup") or []
        durs = [x.get("duration") or x.get("durationMs") or 0 for x in raw]
        total = sum(durs)
        if not total or ch.get("startTime") is None:
            return None
        off = (time.time() * 1000 - float(ch["startTime"])) % total
        k = 0
        while k < len(raw) and off >= durs[k]:
            off -= durs[k]
            k += 1
        k = min(k, len(raw) - 1)
        occ = sum(1 for x in raw[:k] if x.get("id") == raw[k].get("id"))
        return {"id": raw[k].get("id"), "occ": occ, "off": off, "start": float(ch["startTime"])}
    except Exception:
        return None


def _time_restore(cid, snap):
    """Après le changement : on recale le départ de la chaîne pour que le même programme continue au même endroit
    (sinon le guide et la télé annoncent un autre film que celui qui passe vraiment)."""
    if not snap or not snap.get("id"):
        return
    try:
        raw = (tn("/api/channels/%s/programming" % cid, timeout=30) or {}).get("lineup") or []
        acc, n = 0, 0
        for x in raw:
            d = x.get("duration") or x.get("durationMs") or 0
            if x.get("id") == snap["id"]:
                if n == snap["occ"]:
                    want = int(time.time() * 1000 - acc - snap["off"])
                    ch = tn("/api/channels/%s" % quote(cid, safe="")) or {}
                    if abs(float(ch.get("startTime") or 0) - want) > 1500:
                        tn_channel_put(cid, startTime=want)
                    return
                n += 1
            acc += d
    except Exception as e:
        bglog("recalage de la chaîne : %s" % e)


def tn_save(cid, lineup):
    snap = _time_snap(cid)
    items = [x for x in (_clean_item(i) for i in lineup) if x]
    tn("/api/channels/%s/programming" % cid, "POST", {"type": "manual", "lineup": items, "append": False}, timeout=60)
    _time_restore(cid, snap)
    _health["data"] = None


def announce(cid, progs, cur=None):
    """Nouveautés : la télé affiche ce qui vient d'arriver sur une chaîne (seulement quand on ajoute avec Studio)."""
    try:
        c = next((x for x in tn_channels() if x["id"] == cid), None) or {}
        titles = [p.get("title") or "" for p in progs][:30]
        fb("/news", "POST", {"at": int(time.time() * 1000), "channel": c.get("name") or (cur or {}).get("name") or "", "number": c.get("number"),
                             "titles": titles, "count": len(progs), "ids": [p.get("id") for p in progs][:30]})
    except Exception:
        pass


def tn_add(cid, progs, announce_it=True):
    """Ajoute des contenus à la fin de la programmation (sans toucher au reste)."""
    cur = tn_programming(cid)
    tn_backup(cid, cur["raw"])
    items = [{"type": "content", "id": p["id"], "duration": p["duration"]} for p in progs if p.get("id") and p.get("duration")]
    if not items:
        raise RuntimeError("Rien à ajouter")
    snap = _time_snap(cid)
    tn("/api/channels/%s/programming" % cid, "POST", {"type": "manual", "lineup": items, "append": True}, timeout=60)
    _time_restore(cid, snap)
    _health["data"] = None
    if announce_it:
        threading.Thread(target=announce, args=(cid, [p for p in progs if p.get("id") and p.get("duration")], cur), daemon=True).start()
    return len(items)


def tn_edit(cid, action, b):
    import random
    cur = tn_programming(cid)
    raw = list(cur["raw"])
    if action == "undo":
        mine = sorted(x for x in os.listdir(BACKUPS) if x.startswith(cid + "-"))
        if not mine:
            raise RuntimeError("Rien à annuler")
        with open(os.path.join(BACKUPS, mine[-1]), encoding="utf-8") as fh:
            old = json.load(fh)
        tn_save(cid, old)
        os.remove(os.path.join(BACKUPS, mine[-1]))
        return "Dernière modification annulée"
    tn_backup(cid, raw)
    if action == "remove":
        idx = sorted({int(i) for i in (b.get("indexes") or [b.get("index")]) if i is not None and 0 <= int(i) < len(raw)}, reverse=True)
        if not idx:
            raise RuntimeError("Rien à retirer")
        for i in idx:
            raw.pop(i)
        if not raw:
            raise RuntimeError("Une chaîne doit garder au moins un programme")
        msg = "%d programme%s retiré%s" % (len(idx), "s" if len(idx) > 1 else "", "s" if len(idx) > 1 else "")
    elif action == "move":
        i, d = int(b.get("index")), int(b.get("dir") or 0)
        j = max(0, min(len(raw) - 1, i + d if abs(d) == 1 else (0 if d < 0 else len(raw) - 1)))
        it = raw.pop(i)
        raw.insert(j, it)
        msg = "Déplacé"
    elif action == "shuffle":
        random.shuffle(raw)
        msg = "Programmation mélangée"
    elif action == "dedupe":
        seen, out = set(), []
        for it in raw:
            k = (it.get("type"), it.get("id"))
            if it.get("type") == "content" and k in seen:
                continue
            seen.add(k)
            out.append(it)
        msg = "%d doublon%s retiré%s" % ((len(raw) - len(out),) + (("s", "s") if len(raw) - len(out) > 1 else ("", "")))
        raw = out
    else:
        raise RuntimeError("Action inconnue")
    tn_save(cid, raw)
    return msg


def channel_for_lib(path):
    """La chaîne qui va avec une médiathèque (même nom : « Dessin Animé TV » ↔ « Dessin Animé TV »)."""
    lib = next((l for l in jf_cache.get("libs") or [] if os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(l["path"])))), None)
    name = lib["name"] if lib else os.path.basename(path.rstrip("\\/"))
    try:
        for c in tn_channels():
            if same_name(c["name"], name):
                return c["id"]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- santé des chaînes
_health = {"t": 0, "data": None}


def _nat(t):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", (t or "").lower())]


def _ep_order(p):
    return (_nat(p.get("show") or ""), p.get("season") or 0, p.get("episode") or 0, _nat(p.get("title")))


def channel_ghosts(pr):
    """Les programmes d'une chaîne dont le fichier n'existe plus (Jellyfin ne les a plus) : Tunarr afficherait une erreur."""
    out = []
    by_lib = {}
    for x in pr["lineup"]:
        if x.get("type") == "content" and x.get("id") and x.get("lib"):
            by_lib.setdefault(x["lib"], []).append(x)
    for lib, items in by_lib.items():
        if lib not in _libext:
            try:
                for l in tn_libraries_all():
                    _libext[l["id"]] = str(l.get("external") or "")
            except Exception:
                pass
        par = _libext.get(lib, "")
        c = _jfids.get(par)
        ids = c[1] if c and time.time() - c[0] < 5 else jf_item_ids(par, True)
        if not ids:  # Jellyfin ne répond pas : on ne devine rien
            continue
        for x in items:
            if (x.get("ext") and x["ext"] not in ids) or x.get("state") not in ("", "ok"):
                out.append(x)
    return out


def _tnorm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(ch for ch in t if unicodedata.category(ch) != "Mn"))


_SE_RX = re.compile(r"(?i)(?:^|[^a-z0-9])s(\d{1,2})\s?e\s?(\d{1,3})(?![0-9])|(?:^|[^0-9])(\d{1,2})x(\d{2,3})(?![0-9])")


def _tkeys(title):
    """Différentes façons de reconnaître la même vidéo : titre brut, beau titre, numéro d'épisode."""
    t = title or ""
    keys = [_tnorm(t)]
    try:
        n = nice_title(t)[0]
        if n:
            keys.append(_tnorm(n))
    except Exception:
        pass
    m = _SE_RX.search(t)
    if m:
        se = (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4)))
        keys.append("se:%d:%d" % se)
    return [k for k in keys if k]


def repair_ghosts(cid, remove_rest=False):
    """Programmes introuvables d'une chaîne : si la vidéo existe encore sous une nouvelle version (convertie, renommée…),
    on la remet À LA MÊME PLACE ; sinon on la retire seulement si remove_rest. Le programme en cours n'est pas coupé (tn_save)."""
    pr = tn_programming(cid)
    g = channel_ghosts(pr)
    if not g:
        return 0, 0, []
    gid = {x["id"]: x for x in g}
    used = {x["id"] for x in pr["lineup"] if x.get("type") == "content" and x["id"] not in gid}
    cands = {}
    for lib in {x["lib"] for x in g if x.get("lib")}:
        try:
            for c in tn_library_programs(lib, fresh=True):
                for k in _tkeys(c["title"]):
                    cands.setdefault(k, []).append(c)
        except Exception:
            pass
    newraw, fixed, removed, titles = [], 0, 0, []
    for it in pr["raw"]:
        gx = gid.get(it.get("id"))
        if not gx:
            newraw.append(it)
            continue
        c = None
        for k in _tkeys(gx["title"]):
            c = next((c for c in cands.get(k, []) if c["id"] not in used and c.get("duration")), None)
            if c:
                break
        if c:
            newraw.append({"type": "content", "id": c["id"], "duration": c["duration"]})
            used.add(c["id"])
            fixed += 1
            titles.append(gx["title"])
        elif remove_rest:
            removed += 1
        else:
            newraw.append(it)
    if not fixed and not removed:
        return 0, 0, []
    tn_backup(cid, pr["raw"])
    tn_save(cid, newraw)
    return fixed, removed, sorted(set(titles))


def _try_repair(cid):
    try:
        f, r, t = repair_ghosts(cid)
        if f:
            bglog("réparation : %d programme(s) remis dans leur nouvelle version (%s)" % (f, ", ".join(t[:5])))
        return f > 0 and not channel_ghosts(tn_programming(cid))
    except Exception as e:
        bglog("réparation : %s" % e)
        return False


def repair_all_channels():
    """Après des conversions : toutes les chaînes (pas seulement celle de la médiathèque) retrouvent la nouvelle version."""
    out = []
    for c in tn_channels():
        try:
            f, r, t = repair_ghosts(c["id"])
            if f:
                out.append("%s : %s" % (c["name"], ", ".join(t[:4])))
                bglog("réparation : %d programme(s) remplacé(s) par leur nouvelle version sur %s (%s)" % (f, c["name"], ", ".join(t[:5])))
        except Exception as e:
            bglog("réparation de %s : %s" % (c.get("name"), e))
    return out


def remove_ghosts(cid):
    pr = tn_programming(cid)
    g = channel_ghosts(pr)
    ids = {x["id"] for x in g}
    if not ids:
        return 0, []
    # sécurité : si presque toute la chaîne semble disparue, c'est sûrement Jellyfin qui répond mal
    content = [x for x in pr["lineup"] if x.get("type") == "content"]
    if len(ids) > max(3, len(content) * 0.6):
        raise RuntimeError("Trop de programmes semblent disparus (%d) : je préfère ne rien toucher. Vérifie que Jellyfin voit bien la médiathèque." % len(ids))
    tn_backup(cid, pr["raw"])
    keep = [x for x in pr["raw"] if x.get("id") not in ids]
    tn_save(cid, keep)
    return len(pr["raw"]) - len(keep), sorted({x["title"] for x in g})


# ---------------------------------------------------------------- réglages d'une chaîne (nom, numéro, logo, suppression) sans Tunarr
_CH_KEYS = ["disableFillerOverlay", "duration", "fillerCollections", "fillerRepeatCooldown", "groupTitle", "guideFlexTitle", "guideMinimumDuration", "icon", "id",
            "name", "number", "offline", "startTime", "stealth", "watermark", "onDemand", "streamMode", "transcodeConfigId", "subtitlesEnabled", "subtitlePreferences"]


def tn_channel_put(cid, **changes):
    c = tn("/api/channels/%s" % quote(cid, safe="")) or {}
    if not c.get("id"):
        raise RuntimeError("Chaîne introuvable")
    body = {k: c[k] for k in _CH_KEYS if c.get(k) is not None}
    for k, v in changes.items():
        if k == "icon_path":
            body["icon"] = dict(body.get("icon") or {"width": 0, "duration": 0, "position": "bottom-right"}, path=v)
        else:
            body[k] = v
    tn("/api/channels/%s" % quote(cid, safe=""), "PUT", body, timeout=30)
    _health["data"] = None
    return body


def channel_rename(cid, name):
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise RuntimeError("Donne un nom")
    tn_channel_put(cid, name=name)
    return name


def channel_renumber(cid, number):
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise RuntimeError("Le numéro doit être un nombre")
    if not 1 <= number <= 9999:
        raise RuntimeError("Numéro entre 1 et 9999")
    other = next((c for c in tn("/api/channels") or [] if c.get("number") == number and c.get("id") != cid), None)
    if other:
        raise RuntimeError("Le n°%d est déjà pris par « %s »" % (number, other.get("name")))
    tn_channel_put(cid, number=number)
    return number


def channel_logo(cid, data, filename="logo.png"):
    """Envoie l'image à Tunarr (comme son écran de chaîne) et la pose comme logo."""
    if len(data) < 100:
        raise RuntimeError("Image vide")
    if len(data) > 8 * 1024 * 1024:
        raise RuntimeError("Image trop lourde (8 Mo maximum)")
    ext = os.path.splitext(filename)[1].lower() or ".png"
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}.get(ext, "image/png")
    bnd = "----frs" + secrets.token_hex(8)
    body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n" % (bnd, "logo" + ext, ctype)).encode() + data + ("\r\n--%s--\r\n" % bnd).encode()
    req = urllib.request.Request(_fast(conf.get("tunarr_url", "http://localhost:8000")) + "/api/upload/image", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + bnd})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Tunarr a refusé l'image (%d)" % e.code)
    url = res.get("fileUrl") or ""
    if not url:
        raise RuntimeError("Tunarr n'a pas renvoyé d'adresse pour l'image")
    # les télés doivent pouvoir la charger : adresse Tailscale du PC, pas « localhost »
    path = urlparse(url).path if url.startswith("http") else ("/" + url.lstrip("/"))
    port = urlparse(conf.get("tunarr_url", "http://localhost:8000")).port or 8000
    host = tailscale_ip() or urlparse(conf.get("tunarr_url", "http://localhost:8000")).hostname or "localhost"
    full = "http://%s:%d%s" % (host, port, path)
    tn_channel_put(cid, icon_path=full)
    return full


def channel_delete(cid):
    c = tn("/api/channels/%s" % quote(cid, safe="")) or {}
    if not c.get("id"):
        raise RuntimeError("Chaîne introuvable")
    # on garde une copie de la programmation au cas où
    try:
        tn_backup(cid, tn_programming(cid)["raw"])
        with open(os.path.join(BACKUPS, "%s-reglages.json" % cid), "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass
    tn("/api/channels/%s" % quote(cid, safe=""), "DELETE", timeout=30)
    _health["data"] = None
    return c.get("name")


def channel_health(fresh=False):
    """Pour chaque chaîne : combien de temps avant de repasser la même chose, doublons, et ce qui attend dans sa médiathèque."""
    if _health["data"] and not fresh and time.time() - _health["t"] < 60:
        return _health["data"]
    chans = tn_channels()
    libs = tn_libraries()
    from concurrent.futures import ThreadPoolExecutor

    def one(c):
        h = {"id": c["id"], "number": c["number"], "name": c["name"], "icon": c["icon"]}
        try:
            pr = tn_programming(c["id"])
            content = [x for x in pr["lineup"] if x["type"] == "content" and x.get("id")]
            total = sum(x["duration"] or 0 for x in pr["lineup"])
            uniq = {}
            for x in content:
                uniq.setdefault(x["id"], x["duration"] or 0)
            h.update(loop=total, unique=sum(uniq.values()), count=len(content), uniq_count=len(uniq), dupes=len(content) - len(uniq))
            try:
                gh = channel_ghosts(pr)
                h.update(ghosts=len(gh), ghost_titles=sorted({x["title"] for x in gh})[:6])
            except Exception:
                pass
            lib = next((l for l in libs if same_name(l["name"], c["name"])), None)
            if lib:
                lp = tn_library_programs(lib["id"])
                miss = [x for x in lp if x["id"] not in uniq and x.get("duration")]
                h.update(lib=lib["name"], lib_id=lib["id"], lib_count=len(lp), missing=len(miss), missing_dur=sum(x["duration"] for x in miss))
        except Exception as e:
            h["error"] = str(e)
        lp_ = h.get("loop") or 0
        if h.get("error"):
            h["level"] = "?"
        elif lp_ < 6 * 3600000:
            h["level"] = "bad"
        elif lp_ < 24 * 3600000:
            h["level"] = "warn"
        else:
            h["level"] = "ok"
        return h

    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(one, chans))
    _health.update(t=time.time(), data=out)
    return out


def channel_fill(cid):
    """Ajoute à la chaîne tout ce qui est dans sa médiathèque mais pas encore dessus (dans l'ordre des épisodes)."""
    c = next((x for x in tn_channels() if x["id"] == cid), None)
    if not c:
        raise RuntimeError("Chaîne introuvable")
    lib = next((l for l in tn_libraries() if same_name(l["name"], c["name"])), None)
    if not lib:
        raise RuntimeError("Je ne trouve pas de médiathèque qui porte le nom de « %s »" % c["name"])
    on = {x["id"] for x in tn_programming(cid)["lineup"] if x.get("id")}
    miss = sorted((x for x in tn_library_programs(lib["id"], fresh=True) if x["id"] not in on and x.get("duration")), key=_ep_order)
    if not miss:
        return 0
    n = tn_add(cid, miss)
    _health["data"] = None
    return n


# ---------------------------------------------------------------- Médiathèque (gérer Jellyfin, les dossiers et Tunarr depuis Studio)
TRASH_DIR = "_Corbeille Studio"
TRASH_IDX = os.path.join(DATA, "corbeille.json")
_mstat = {}


def _need_key():
    if not conf.get("jellyfin_key"):
        raise RuntimeError("Ajoute d'abord ta clé Jellyfin (Réglages)")


def media_roots():
    return [l["path"] for l in jf_libraries(force=False)["libs"]]


def _inside(p, roots=None):
    np_ = os.path.normcase(os.path.abspath(p))
    for r in roots if roots is not None else media_roots():
        nr = os.path.normcase(os.path.abspath(r)).rstrip("\\/")
        if np_ == nr or np_.startswith(nr + os.sep):
            return r
    return None


def _check_inside(p):
    if not p or not _inside(p):
        raise RuntimeError("Ce dossier n'est pas dans une de tes médiathèques")


def _dir_stats(path):
    c = _mstat.get(path)
    if c and time.time() - c[0] < 90:
        return c[1]
    n = sz = 0
    last = 0
    for d, dirs, fs in os.walk(path):
        dirs[:] = [x for x in dirs if x != TRASH_DIR]
        for f in fs:
            if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                try:
                    st = os.stat(os.path.join(d, f))
                except OSError:
                    continue
                n += 1
                sz += st.st_size
                last = max(last, st.st_mtime)
    out = {"count": n, "size": sz, "last": last}
    _mstat[path] = (time.time(), out)
    return out


def _forget_stats():
    _mstat.clear()


def media_libs():
    _need_key()
    libs = jf_libraries(force=True)
    if libs["error"]:
        raise RuntimeError(libs["error"])
    try:
        tl = tn_libraries_all()
    except Exception:
        tl = None
    try:
        chans = tn_channels()
    except Exception:
        chans = []
    by = {}
    for l in libs["libs"]:
        b = by.setdefault(l["name"], {"name": l["name"], "type": l["type"], "item": l.get("item"), "paths": []})
        b["paths"].append(l["path"])
    out = []
    for b in by.values():
        st = {"count": 0, "size": 0, "last": 0}
        for pth in b["paths"]:
            if os.path.isdir(pth):
                x = _dir_stats(pth)
                st = {"count": st["count"] + x["count"], "size": st["size"] + x["size"], "last": max(st["last"], x["last"])}
        b.update(st)
        b["exists"] = all(os.path.isdir(x) for x in b["paths"])
        if tl is not None:
            t = next((x for x in tl if same_name(x["name"], b["name"])), None)
            b["tunarr"] = {"enabled": bool(t and t["enabled"]), "found": bool(t)}
        c = next((x for x in chans if same_name(x["name"], b["name"])), None)
        if c:
            b["channel"] = {"id": c["id"], "name": c["name"], "number": c["number"]}
        out.append(b)
    out.sort(key=lambda b: _key(b["name"]))
    return out


def tn_libraries_all():
    out = []
    for ms in tn("/api/media-sources") or []:
        for l in ms.get("libraries") or []:
            out.append({"id": l["id"], "name": (l.get("name") or "").strip(), "enabled": l.get("enabled", True), "source": ms.get("id"), "external": l.get("externalKey")})
    return out


def media_ls(path):
    _check_inside(path)
    if not os.path.isdir(path):
        raise RuntimeError("Dossier introuvable")
    root = _inside(path)
    dirs, files = [], []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith(".") or e.name == TRASH_DIR:
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": e.path, **_dir_stats(e.path)})
                elif os.path.splitext(e.name)[1].lower() in VIDEO_EXT:
                    st = e.stat()
                    files.append({"name": e.name, "path": e.path, "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
    dirs.sort(key=lambda x: _nat(x["name"]))
    files.sort(key=lambda x: _nat(os.path.splitext(x["name"])[0]))
    crumbs = []
    cur = os.path.abspath(path)
    while True:
        crumbs.insert(0, {"name": os.path.basename(cur) or cur, "path": cur})
        if os.path.normcase(cur) == os.path.normcase(os.path.abspath(root)):
            break
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return {"path": path, "root": root, "crumbs": crumbs, "dirs": dirs, "files": files}


_info_cache = {}


def media_info(path):
    """Ce qu'il y a dans un fichier vidéo, et s'il est lourd à lire pour Tunarr (démarrage lent)."""
    _check_inside(path)
    st = os.stat(path)
    k = (path, st.st_mtime, st.st_size)
    if k in _info_cache:
        return _info_cache[k]
    info = probe(path)
    fmt = info.get("format") or {}
    v = next((x for x in info.get("streams", []) if x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic")), {}) or {}
    auds = [x for x in info.get("streams", []) if x.get("codec_type") == "audio"]
    dur = float(fmt.get("duration") or 0)
    br = int(fmt.get("bit_rate") or 0) or (int(st.st_size * 8 / dur) if dur else 0)
    fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        a, b_ = fr.split("/")
        fps = round(float(a) / float(b_), 2) if float(b_) else 0
    except Exception:
        fps = 0
    fo = (v.get("field_order") or "progressive").lower()
    vc = (v.get("codec_name") or "?").lower()
    heavy = []
    if vc not in ("h264",):
        heavy.append({"hevc": "vidéo HEVC (H.265)", "mpeg4": "vieux format XviD/DivX", "mpeg2video": "format MPEG-2 (DVD)", "av1": "vidéo AV1", "vc1": "format VC-1", "msmpeg4v3": "vieux format DivX 3", "vp9": "vidéo VP9"}.get(vc, "vidéo " + vc.upper()))
    if (v.get("height") or 0) > 1100:
        heavy.append("image en %dp (plus que la Full HD)" % v.get("height"))
    if fo not in ("progressive", "unknown"):
        heavy.append("image entrelacée")
    if br > 14_000_000:
        heavy.append("très gros débit (%d Mb/s)" % round(br / 1e6))
    if (v.get("pix_fmt") or "").endswith(("10le", "10be")):
        heavy.append("couleurs 10 bits")
    out = {"path": path, "name": os.path.basename(path), "size": st.st_size, "duration": dur, "bitrate": br, "container": (fmt.get("format_name") or "").split(",")[0],
           "video": {"codec": vc, "w": v.get("width"), "h": v.get("height"), "fps": fps, "interlaced": fo not in ("progressive", "unknown"), "pix": v.get("pix_fmt")},
           "audio": [{"codec": x.get("codec_name"), "ch": x.get("channels"), "lang": (x.get("tags") or {}).get("language", "")} for x in auds],
           "heavy": heavy, "ok_mp4": path.lower().endswith(".mp4") and vc == "h264" and not heavy}
    _info_cache[k] = out
    return out


def media_heavy(folder):
    """Les vidéos d'un dossier qui ralentissent le démarrage de la chaîne."""
    _check_inside(folder)
    res = []
    from concurrent.futures import ThreadPoolExecutor
    files = [os.path.join(d, f) for d, dirs, fs in os.walk(folder) for f in fs if os.path.splitext(f)[1].lower() in VIDEO_EXT and TRASH_DIR not in d]
    def safe(f):
        try:
            return media_info(f)
        except Exception:
            return None
    with ThreadPoolExecutor(4) as ex:
        res = [x for x in ex.map(safe, files) if x]
    heavy = [x for x in res if x["heavy"]]
    return {"total": len(res), "heavy": [{"path": x["path"], "name": x["name"], "why": x["heavy"]} for x in heavy]}


def media_convert(paths, night=False):
    ids = []
    busy = {x.get("src") for x in jobs if x.get("kind") == "conv" and x["status"] in ("attente", "en cours")}
    for pth in paths:
        _check_inside(pth)
        if pth in busy:
            continue  # déjà dans la file d'attente
        kw = {"night": True, "msg": "Prévue cette nuit (vers %d h), une à la fois" % int(conf.get("night_from", 2))} if night else {}
        ids.append(new_job("conv", src=pth, dest=os.path.dirname(pth), delete=False, trash_src=True, sync=True, title=os.path.basename(pth), **kw)["id"])
    return ids


# ---------- fiche et image d'un film (quand Jellyfin s'est trompé de film) ----------
def _jf_raw(path, timeout=15):
    req = urllib.request.Request(_fast(conf["jellyfin_url"]) + path, headers={"X-Emby-Token": conf["jellyfin_key"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type") or "image/jpeg"


def _jf_items(q):
    try:
        return (jf("/Items?" + q, timeout=25) or {}).get("Items") or []
    except urllib.error.HTTPError:
        users = jf("/Users") or []
        u = next((x for x in users if (x.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None)
        if not u:
            return []
        return (jf("/Users/%s/Items?%s" % (u["Id"], q), timeout=25) or {}).get("Items") or []


def _norm_p(p):
    return os.path.normcase(os.path.normpath(p or ""))


def jf_find(path):
    """L'élément Jellyfin qui correspond à un fichier (et sa médiathèque)."""
    _need_key()
    _check_inside(path)
    np = _norm_p(path)
    for l in jf_libraries()["libs"]:
        lp = _norm_p(l["path"])
        if not np.startswith(lp.rstrip("\\/") + os.sep) or not l.get("item"):
            continue
        q = "ParentId=%s&Recursive=true&IsFolder=false&Fields=Path,ProductionYear,ProviderIds,Overview,Genres&EnableUserData=false&Limit=200000" % quote(l["item"])
        for x in _jf_items(q):
            if _norm_p(x.get("Path")) == np:
                return x, l
    return None, None


def _ident_target(x):
    if x.get("Type") == "Episode" and x.get("SeriesId"):
        try:
            s = jf("/Items/%s" % x["SeriesId"]) or {}
        except Exception:
            s = {}
        if not s.get("Id"):
            r = _jf_items("Ids=%s&Fields=ProductionYear,Overview,Genres" % x["SeriesId"])
            s = r[0] if r else {"Id": x["SeriesId"], "Name": x.get("SeriesName")}
        return s, "Series"
    return x, "Movie"


def media_ident(path):
    x, l = jf_find(path)
    if not x:
        raise RuntimeError("Jellyfin ne connaît pas encore ce fichier (attends 1-2 min qu'il le voie)")
    t, kind = _ident_target(x)
    name = os.path.splitext(os.path.basename(path))[0]
    guess = re.sub(r"[._]+", " ", name)
    guess = re.sub(r"\b(19|20)\d\d\b.*$", "", guess).strip() or guess
    return {"id": t.get("Id"), "kind": kind, "name": t.get("Name") or "", "year": t.get("ProductionYear") or "",
            "overview": (t.get("Overview") or "")[:300], "genres": (t.get("Genres") or [])[:3],
            "img": bool((t.get("ImageTags") or {}).get("Primary")), "guess": (t.get("SeriesName") or guess) if kind == "Series" else guess,
            "lib": l.get("item")}


def media_idsearch(item, kind, q, year):
    _need_key()
    info = {"Name": q.strip()}
    if str(year).strip().isdigit():
        info["Year"] = int(year)
    r = jf("/Items/RemoteSearch/%s" % ("Series" if kind == "Series" else "Movie"), "POST",
           {"SearchInfo": info, "ItemId": item, "IncludeDisabledProviders": False}, timeout=40) or []
    return r[:16]


def media_idapply(item, result, lib, keep=False):
    _need_key()
    if not item or not isinstance(result, dict) or not result.get("ProviderIds"):
        raise RuntimeError("Choisis un film dans la liste")
    jf("/Items/RemoteSearch/Apply/%s?ReplaceAllImages=%s" % (quote(item), "false" if keep else "true"), "POST", result, timeout=60)

    def later():
        try:
            time.sleep(25)
            key = str(lib or "").replace("-", "").lower()
            for t in tn_libraries_all():
                if str(t.get("external") or "").replace("-", "").lower() == key:
                    tn_scan_lib(t["id"], every=0)
            time.sleep(30)
            fb("/config/imgreset", "PUT", int(time.time() * 1000))
        except Exception:
            bglog("fiche %s : %s" % (item, _trace()))
    threading.Thread(target=later, daemon=True).start()
    return True


def media_search(q):
    k = _fz(q)
    if len(k) < 2:
        return []
    out = []
    for r in media_roots():
        for d, dirs, fs in os.walk(r):
            dirs[:] = [x for x in dirs if x != TRASH_DIR]
            for f in fs:
                if os.path.splitext(f)[1].lower() in VIDEO_EXT and k in _fz(os.path.splitext(f)[0]):
                    pth = os.path.join(d, f)
                    try:
                        sz = os.path.getsize(pth)
                    except OSError:
                        sz = 0
                    out.append({"name": f, "path": pth, "size": sz, "dir": d, "lib": next((l["name"] for l in jf_cache["libs"] if l["path"] == r), "")})
                    if len(out) >= 300:
                        return out
    return out


def _after_change(paths):
    """Jellyfin puis Tunarr relisent les médiathèques touchées, puis leurs chaînes se mettent à jour."""
    _forget_stats()
    _jfids.clear()
    _health["data"] = None
    if paths:
        jf_refresh()
        names = set()
        for pth in paths:
            r = _inside(pth)
            names |= {l["name"] for l in jf_cache["libs"] if l["path"] == r}
        follow_libs(sorted(names))


def media_rename(path, name):
    _check_inside(path)
    name = safe_name(name)
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1]
        if not name.lower().endswith(ext.lower()):
            name += ext
    dst = os.path.join(os.path.dirname(path), name)
    if os.path.normcase(dst) == os.path.normcase(path):
        return path
    if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(path):
        raise RuntimeError("« %s » existe déjà ici" % name)
    unlink_from_channels([path])   # l'ancien nom quitte la chaîne, le nouveau y sera remis tout seul
    os.rename(path, dst)
    _after_change([path, dst])
    return dst


def _move(src, dst_dir):
    dst = unique_path(os.path.join(dst_dir, os.path.basename(src)))
    shutil.move(src, dst)
    return dst


def media_move(paths, dest):
    _check_inside(dest)
    if not os.path.isdir(dest):
        raise RuntimeError("Dossier de destination introuvable")
    done = []
    for pth in paths:
        _check_inside(pth)
    unlink_from_channels(paths)    # l'ancienne place quitte sa chaîne, la nouvelle chaîne la reçoit toute seule
    for pth in paths:
        if os.path.normcase(os.path.abspath(dest)).startswith(os.path.normcase(os.path.abspath(pth)) + os.sep):
            raise RuntimeError("Impossible de déplacer un dossier dans lui-même")
        done.append(_move(pth, dest))
    _after_change(list(paths) + done)
    return done


def _trash_load():
    try:
        with open(TRASH_IDX, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _trash_save(items):
    tmp = TRASH_IDX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, TRASH_IDX)


def _titles_in(pth):
    if os.path.isfile(pth):
        return {_fz(os.path.splitext(os.path.basename(pth))[0])}
    t = set()
    for d, _, fs in os.walk(pth):
        for f in fs:
            if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                t.add(_fz(os.path.splitext(f)[0]))
    return t


def unlink_from_channels(paths):
    """Ce qu'on met à la corbeille est aussi retiré des chaînes (sinon Tunarr afficherait une erreur à cet endroit)."""
    removed = 0
    try:
        libs = tn_libraries()
        chans = tn_channels()
    except Exception:
        return 0
    for pth in paths:
        root = _inside(pth)
        names = {l["name"] for l in jf_cache["libs"] if l["path"] == root}
        titles = _titles_in(pth)
        if not titles:
            continue
        for tl in [l for l in libs if any(same_name(l["name"], n) for n in names)]:
            ids = {x["id"] for x in tn_library_programs(tl["id"], fresh=True) if _fz(x.get("title")) in titles}
            if not ids:
                continue
            for c in chans:
                try:
                    cur = tn_programming(c["id"])
                    if not any(x.get("id") in ids for x in cur["raw"]):
                        continue
                    tn_backup(c["id"], cur["raw"])
                    keep = [x for x in cur["raw"] if x.get("id") not in ids]
                    removed += len(cur["raw"]) - len(keep)
                    tn_save(c["id"], keep)
                except Exception:
                    pass
    return removed


def media_trash(paths, unlink=True):
    items = _trash_load()
    removed = unlink_from_channels(paths) if unlink else 0
    n = 0
    for pth in paths:
        root = _inside(pth)
        if not root:
            raise RuntimeError("Ce fichier n'est pas dans une de tes médiathèques")
        tdir = os.path.join(os.path.dirname(os.path.abspath(root)), TRASH_DIR, time.strftime("%Y-%m-%d"))
        os.makedirs(tdir, exist_ok=True)
        dst = _move(pth, tdir)
        sz = os.path.getsize(dst) if os.path.isfile(dst) else sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(dst) for f in fs)
        items.insert(0, {"id": secrets.token_hex(6), "from": pth, "at": dst, "t": time.time(), "size": sz, "dir": os.path.isdir(dst)})
        n += 1
    _trash_save(items)
    _after_change(paths)
    return {"n": n, "removed": removed}


def trash_list():
    items = [x for x in _trash_load() if os.path.exists(x["at"])]
    return items


def trash_restore(ids):
    items = _trash_load()
    back = []
    for x in [x for x in items if x["id"] in ids]:
        if not os.path.exists(x["at"]):
            continue
        os.makedirs(os.path.dirname(x["from"]), exist_ok=True)
        dst = unique_path(x["from"])
        shutil.move(x["at"], dst)
        back.append(dst)
    _trash_save([x for x in items if x["id"] not in ids])
    _after_change(back)
    return len(back)


def trash_empty(ids=None):
    items = _trash_load()
    gone = 0
    keep = []
    for x in items:
        if ids is not None and x["id"] not in ids:
            keep.append(x)
            continue
        try:
            if os.path.isdir(x["at"]):
                shutil.rmtree(x["at"])
            elif os.path.exists(x["at"]):
                os.remove(x["at"])
            gone += 1
        except OSError:
            keep.append(x)
    _trash_save(keep)
    return gone


JF_TYPES = {"films": "movies", "videos": "homevideos"}


def tunarr_sync_libs(name, scan=True):
    """Tunarr relit la liste des médiathèques Jellyfin, active la nouvelle et l'analyse."""
    for _ in range(8):
        try:
            for ms in tn("/api/media-sources") or []:
                tn("/api/media-sources/%s/libraries/refresh" % ms["id"], "POST", {}, timeout=60)
            for t in tn_libraries_all():
                if same_name(t["name"], name):
                    src, lid = quote(str(t["source"]), safe=""), quote(str(t["id"]), safe="")
                    if not t["enabled"]:
                        tn("/api/media-sources/%s/libraries/%s" % (src, lid), "PUT", {"enabled": True})
                    if scan:
                        tn("/api/media-sources/%s/libraries/%s/scan?forceScan=true" % (src, lid), "POST")
                    return t
        except Exception:
            pass
        time.sleep(4)
    return None


def tn_create_channel(name, number=None):
    chans = tn("/api/channels") or []
    if any(same_name(c.get("name"), name) for c in chans):
        raise RuntimeError("Une chaîne « %s » existe déjà" % name)
    used = {c.get("number") for c in chans}
    if not number:
        number = next(n for n in range(1, 1000) if n not in used)
    elif number in used:
        raise RuntimeError("Le numéro %s est déjà pris" % number)
    base = tn("/api/channels/%s" % chans[0]["id"]) if chans else {}
    ch = {"disableFillerOverlay": base.get("disableFillerOverlay", False), "duration": 0, "fillerCollections": [], "fillerRepeatCooldown": base.get("fillerRepeatCooldown", 30000),
          "groupTitle": base.get("groupTitle") or "Frigo Rosé TV", "guideMinimumDuration": base.get("guideMinimumDuration", 30000),
          "icon": {"path": "", "width": 0, "duration": 0, "position": "bottom-right", "useDefaultIconFallback": True},
          "id": str(__import__("uuid").uuid4()), "name": name, "number": number, "offline": {"mode": "pic", "picture": "", "soundtrack": ""},
          "onDemand": {"enabled": False}, "startTime": int(time.time() // 60 * 60000), "stealth": False, "streamMode": base.get("streamMode") or "hls",
          "transcodeConfigId": base.get("transcodeConfigId") or "00000000-0000-0000-0000-000000000000", "subtitlesEnabled": False}
    if base.get("watermark"):
        ch["watermark"] = dict(base["watermark"], url="")
    tn("/api/channels", "POST", {"type": "new", "channel": ch}, timeout=30)
    return {"id": ch["id"], "name": name, "number": number}


def media_drives():
    """Les disques du PC (et un disque dur branché) où on peut ranger une médiathèque."""
    libs = jf_libraries()["libs"]
    base = os.path.dirname(os.path.abspath(libs[0]["path"])) if libs else ""
    cur = os.path.splitdrive(base)[0].upper() if WIN else "/"
    out = []
    parts = []
    try:
        parts = [x.mountpoint for x in psutil.disk_partitions(all=False)] if (psutil and WIN) else []
    except Exception:
        parts = []
    if WIN and not parts:
        parts = ["%s:\\" % c for c in string.ascii_uppercase if os.path.exists("%s:\\" % c)]
    if not WIN and not parts:
        parts = ["/"]
    for m in parts:
        try:
            u = shutil.disk_usage(m)
        except Exception:
            continue
        d = os.path.splitdrive(m)[0].upper() if WIN else m
        if (WIN and d in ("A:", "B:")) or u.total < 8e9:
            continue
        out.append({"drive": d or m, "path": m, "free": u.free, "total": u.total, "current": (d == cur) if WIN else m == "/"})
    return {"drives": out, "folder": os.path.basename(base) or "TV 2424"}


def media_newlib(name, kind, make_channel=True, number=None, drive=""):
    _need_key()
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise RuntimeError("Donne un nom")
    if not re.search(r"\btv$", name, re.I):
        name += " TV"
    libs = jf_libraries(force=True)["libs"]
    if any(same_name(l["name"], name) for l in libs):
        raise RuntimeError("Tu as déjà une médiathèque « %s »" % name)
    base = os.path.dirname(os.path.abspath(libs[0]["path"])) if libs else os.path.join(os.path.expanduser("~"), "Videos")
    if drive:
        dv = next((d for d in media_drives()["drives"] if d["drive"].upper() == str(drive).upper()), None)
        if not dv:
            raise RuntimeError("Ce disque n'est pas branché")
        if not dv["current"]:
            base = os.path.join(dv["path"], os.path.basename(base) or "TV 2424")
    folder = os.path.join(base, safe_name(name))
    os.makedirs(folder, exist_ok=True)
    jf("/Library/VirtualFolders?" + urlencode({"name": name, "collectionType": JF_TYPES.get(kind, "homevideos"), "paths": folder, "refreshLibrary": "true"}),
       "POST", {"LibraryOptions": {"EnableRealtimeMonitor": True, "PathInfos": [{"Path": folder}]}}, timeout=30)
    jf_libraries(force=True)
    res = {"name": name, "path": folder}
    st = {"msg": "Médiathèque créée"}

    def follow():
        t = tunarr_sync_libs(name)
        st["msg"] += " · activée dans Tunarr" if t else " · Tunarr ne la voit pas encore (réessaie « Analyser » dans une minute)"
    threading.Thread(target=follow, daemon=True).start()
    if make_channel:
        res["channel"] = tn_create_channel(name, number)
    return res


def media_renamelib(old, new):
    _need_key()
    new = re.sub(r"\s+", " ", (new or "").strip())
    if not new:
        raise RuntimeError("Donne un nom")
    jf("/Library/VirtualFolders/Name?" + urlencode({"name": old, "newName": new, "refreshLibrary": "true"}), "POST", timeout=30)
    jf_libraries(force=True)
    threading.Thread(target=tunarr_sync_libs, args=(new, False), daemon=True).start()
    return new


def media_removelib(name):
    _need_key()
    jf("/Library/VirtualFolders?" + urlencode({"name": name, "refreshLibrary": "true"}), "DELETE", timeout=30)
    jf_libraries(force=True)
    try:
        for ms in tn("/api/media-sources") or []:
            tn("/api/media-sources/%s/libraries/refresh" % ms["id"], "POST", {}, timeout=60)
    except Exception:
        pass


def media_scan(name, fill=False):
    _need_key()
    lib = next((l for l in jf_libraries(force=True)["libs"] if l["name"] == name), None)
    if not lib:
        raise RuntimeError("Médiathèque introuvable")
    if lib.get("item"):
        jf("/Items/%s/Refresh?Recursive=true&MetadataRefreshMode=Default&ImageRefreshMode=Default" % lib["item"], "POST")
    else:
        jf("/Library/Refresh", "POST")
    _forget_stats()

    def later():
        time.sleep(float(os.environ.get("FRS_TUNARR_DELAY", "45")))
        tunarr_sync_libs(name)
        if not fill:
            return
        # puis on met sur la chaîne ce que Tunarr vient de découvrir
        c = next((x for x in tn_channels() if same_name(x["name"], name)), None)
        if not c:
            return
        for _ in range(6):
            time.sleep(float(os.environ.get("FRS_TUNARR_DELAY", "20")))
            try:
                if channel_fill(c["id"]):
                    return
            except Exception:
                pass
    threading.Thread(target=later, daemon=True).start()


def media_pending():
    """Ce qui est dans Jellyfin mais pas encore passé sur Tunarr : vidéos pas sur leur chaîne, médiathèques sans chaîne ou pas encore lues par Tunarr."""
    libs = media_libs()
    tl = tn_libraries_all()
    from concurrent.futures import ThreadPoolExecutor

    def one(l):
        t = next((x for x in tl if same_name(x["name"], l["name"])), None)
        it = {"name": l["name"], "count": l["count"], "channel": l.get("channel"), "missing": [], "tunarr": bool(t and t["enabled"]), "tn_count": 0}
        if t and t["enabled"]:
            progs = tn_library_programs(t["id"])
            it["tn_count"] = len(progs)
            if l.get("channel"):
                on = {x.get("id") for x in tn_programming(l["channel"]["id"])["lineup"] if x.get("id")}
                miss = sorted((x for x in progs if x["id"] not in on and x.get("duration")), key=_ep_order)
                it["missing"] = [{"title": x["title"], "duration": x["duration"]} for x in miss]
        un = lib_unscanned(t["id"]) if t and t["enabled"] else {}
        it["unscanned"] = len(un) if (t and t["enabled"] and jf_item_ids(_libext.get(t["id"], ""))) else max(0, l["count"] - it["tn_count"])
        it["unscanned_names"] = sorted(un.values())[:8]
        return it

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, [l for l in libs if l["count"]]))
    return [it for it in res if it["missing"] or not it["channel"] or it["unscanned"] or not it["tunarr"]]


# ---------------------------------------------------------------- la chaîne suit sa médiathèque
SYNC = {}          # nom de médiathèque -> {"step", "t", "done", "msg"}
k_retry = {}
CONV_DONE = {}     # nom de médiathèque -> nombre de vidéos converties (l'ancienne version a disparu) depuis la dernière mise à jour de la chaîne
_sync_lock = threading.Lock()


def sync_state():
    now = time.time()
    with _sync_lock:
        for k in [k for k, v in SYNC.items() if v.get("done") and now - v["t"] > 90]:
            SYNC.pop(k, None)
        return [{"lib": k, **v} for k, v in SYNC.items()]


def _sync_set(name, **kw):
    with _sync_lock:
        SYNC[name] = {**SYNC.get(name, {}), **kw, "t": time.time()}


def sync_lib_channel(name):
    """Met la chaîne d'une médiathèque à jour : ajoute ce qui manque. Ne retire jamais rien tout seul."""
    c = next((x for x in tn_channels() if same_name(x["name"], name)), None)
    if not c:
        return 0, 0
    try:
        return channel_fill(c["id"]), 0
    except Exception:
        return 0, 0


def follow_libs(names):
    """Après un changement dans la Médiathèque : Jellyfin → Tunarr → chaîne, en suivant chaque étape."""
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return

    def run(name):
        try:
            _sync_set(name, step="Jellyfin relit le dossier…", done=False, msg="")
            time.sleep(float(os.environ.get("FRS_SYNC_JF", "20")))
            _sync_set(name, step="Tunarr relit la médiathèque…")
            _forget_stats()
            disk = sum(_dir_stats(l["path"])["count"] for l in jf_cache["libs"] if l["name"] == name and os.path.isdir(l["path"]))
            t = None
            for i in range(10):
                t = tunarr_sync_libs(name)
                if t:
                    time.sleep(float(os.environ.get("FRS_SYNC_TN", "15")))
                    n = len(tn_library_programs(t["id"], fresh=True))
                    if n == disk or i >= 5:
                        break
                time.sleep(float(os.environ.get("FRS_SYNC_TN", "15")))
            _sync_set(name, step="Mise à jour de la chaîne…")
            a, r = sync_lib_channel(name)
            # vidéos converties : l'ancienne version (fichier parti à la corbeille) est retirée de la chaîne, la nouvelle vient d'être ajoutée
            k = CONV_DONE.pop(name, 0)
            if k:
                try:
                    c = next((x for x in tn_channels() if same_name(x["name"], name)), None)
                    if c:
                        g = channel_ghosts(tn_programming(c["id"]))
                        if g:
                            repair_all_channels()  # nouvelle version remise à la même place, sur toutes les chaînes
                            g = []
                        if g:
                            bglog("conversion : %d fantôme(s) sur %s pour %d conversion(s), je ne touche à rien" % (len(g), name, k))
                        elif not k_retry.get(name):
                            # Jellyfin n'a pas encore vu disparaître l'ancien fichier : on revient voir dans 3 min
                            k_retry[name] = 1
                            CONV_DONE[name] = CONV_DONE.get(name, 0) + k
                            t = threading.Timer(180, follow_libs, args=([name],))
                            t.daemon = True
                            t.start()
                        else:
                            k_retry.pop(name, None)
                except Exception as e:
                    bglog("conversion : nettoyage de %s : %s" % (name, e))
            parts = []
            if a:
                parts.append("%d ajouté%s" % (a, "s" if a > 1 else ""))
            if r:
                parts.append("%d ancienne%s version%s remplacée%s" % (r, "s" if r > 1 else "", "s" if r > 1 else "", "s" if r > 1 else ""))
            _sync_set(name, step="", done=True, msg="Chaîne à jour" + (" · " + ", ".join(parts) if parts else ""))
            _health["data"] = None
        except Exception as e:
            bglog("synchro %s : %s" % (name, _trace()))
            _sync_set(name, step="", done=True, msg="Pas pu mettre la chaîne à jour (%s)" % e)

    for n in names:
        if SYNC.get(n) and not SYNC[n].get("done"):
            continue  # déjà en cours : la boucle relira tout à la fin
        threading.Thread(target=run, args=(n,), daemon=True).start()


_scan_t = {}
_never = set()   # vidéos que Tunarr refuse de lire (bonus, bandes-annonces…) : on n'insiste pas


def lib_unscanned(lib_id, fresh=False):
    """Les vidéos que Jellyfin a déjà mais que Tunarr n'a pas encore lues : {id Jellyfin: nom}."""
    if lib_id not in _libext:
        try:
            for l in tn_libraries_all():
                _libext[l["id"]] = str(l.get("external") or "")
        except Exception:
            pass
    ids = jf_item_ids(_libext.get(lib_id, ""), fresh)
    if not ids:
        return {}
    known = {x.get("ext") for x in tn_library_programs(lib_id, fresh, alive=False)}
    return {k: v for k, v in ids.items() if k not in known and k not in _never}


def tn_scan_lib(lib_id, every=90):
    """Demande à Tunarr de relire une médiathèque (pas plus d'une fois toutes les 90 s)."""
    if time.time() - _scan_t.get(lib_id, 0) < every:
        return False
    t = next((x for x in tn_libraries_all() if x["id"] == lib_id), None)
    if not t:
        return False
    _scan_t[lib_id] = time.time()
    tn("/api/media-sources/%s/libraries/%s/scan?forceScan=true" % (quote(str(t["source"]), safe=""), quote(str(lib_id), safe="")), "POST")
    return True


_catching = set()


def catch_up_lib(lib_id, wait=None):
    if lib_id in _catching:
        return 0
    _catching.add(lib_id)
    try:
        return _catch_up_lib(lib_id, wait)
    except Exception:
        bglog("rattrapage %s : %s" % (lib_id, _trace()))
        return 0
    finally:
        _catching.discard(lib_id)


def _catch_up_lib(lib_id, wait=None):
    """Tunarr relit la médiathèque, puis ce qui vient d'arriver est mis sur sa chaîne (seulement les nouveautés)."""
    new = lib_unscanned(lib_id, True)
    bglog("rattrapage %s : %d à lire %s" % (lib_id, len(new), list(new.values())[:5]))
    if not new:
        return 0
    if not tn_scan_lib(lib_id):
        bglog("rattrapage %s : relecture déjà demandée il y a moins de 90 s" % lib_id)
        return 0
    name = next((x["name"] for x in tn_libraries_all() if x["id"] == lib_id), "")
    _sync_set(name, step="Tunarr découvre %d nouveauté%s de Jellyfin…" % (len(new), "s" if len(new) > 1 else ""), done=False, msg="")
    for _ in range(12):
        time.sleep(wait if wait is not None else float(os.environ.get("FRS_SYNC_TN", "10")))
        progs = tn_library_programs(lib_id, True)
        got = [x for x in progs if x.get("ext") in new]
        if len(got) >= len(new):
            break
    _never.update(k for k in new if k not in {x.get("ext") for x in got})
    added = 0
    c = next((x for x in tn_channels() if same_name(x["name"], name)), None)
    if c and got:
        on = {x.get("id") for x in tn_programming(c["id"])["lineup"]}
        todo = sorted((x for x in got if x["id"] not in on and x.get("duration")), key=_ep_order)
        if todo:
            added = tn_add(c["id"], todo)
    _sync_set(name, step="", done=True, msg=("%d nouveauté%s sur la chaîne n°%s" % (added, "s" if added > 1 else "", c["number"])) if added else "Tunarr est à jour")
    _health["data"] = None
    return added


def watch_loop():
    """Toutes les 5 minutes : si Jellyfin a des vidéos que Tunarr ne connaît pas encore, on rattrape tout seul."""
    time.sleep(float(os.environ.get("FRS_WATCH_FIRST", "60")))
    while True:
        try:
            if conf.get("jellyfin_key"):
                for t in tn_libraries_all():
                    if t["enabled"] and t.get("external"):
                        try:
                            if lib_unscanned(t["id"], True) and t["id"] not in _catching:
                                threading.Thread(target=catch_up_lib, args=(t["id"],), daemon=True).start()
                        except Exception:
                            bglog("surveillance : " + _trace())
        except Exception:
            pass
        time.sleep(float(os.environ.get("FRS_WATCH_EVERY", "300")))


def auto_add_to_channels(js, last=False):
    """Après l'analyse Tunarr : on cherche les vidéos dans la médiathèque et on les ajoute à la chaîne choisie."""
    todo = [j for j in js if j.get("tchan") and not j.get("tchan_done") and j.get("out")]
    if not todo:
        return
    try:
        libs = tn_libraries()
        jlibs = jf_libraries(force=True)["libs"]
        chans = {c["id"]: c for c in tn_channels()}
    except Exception:
        return
    for j in todo:
        try:
            # médiathèque Tunarr où est arrivé le fichier
            names = lib_names_for(j["out"][0])
            cand = [l for l in libs if any(same_name(l["name"], n) for n in names)] or libs
            wanted = []
            for f in j["out"]:
                stem = os.path.splitext(os.path.basename(f))[0]
                wanted.append({_key(stem), _key(j.get("title") or ""), _key(re.sub(r"\s+-\s+", " ", stem))})
            found = []
            for l in cand:
                for pg in tn_library_programs(l["id"], fresh=True):
                    k = _key(pg["title"])
                    for wi, w in enumerate(wanted):
                        if k and k in w and wi not in [x[0] for x in found]:
                            found.append((wi, pg))
                if len(found) == len(wanted):
                    break
            if found:
                cur_ids = {it.get("id") for it in tn_programming(j["tchan"])["raw"]}
                add = [pg for _, pg in found if pg["id"] not in cur_ids]
                if add:
                    tn_add(j["tchan"], add)
                c = chans.get(j["tchan"]) or {}
                j["tchan_done"] = True
                j["msg"] += " · sur la chaîne n°%s %s" % (c.get("number", "?"), c.get("name", ""))
                if j.get("req"):
                    req_steps(j["req"], {"tunarr": True}, {"channel": tunarr_to_fb_channel(c)} if c else None)
            elif last:
                j["msg"] += " · pas encore visible dans Tunarr" + (" (relie Jellyfin dans Réglages : c'est lui qui prévient Tunarr)" if not conf["jellyfin_key"] else " : réessaie « Mettre sur une chaîne » dans un moment")
        except Exception as e:
            if last:
                j["msg"] += " · ajout à la chaîne raté (%s)" % e


def tunarr_to_fb_channel(c):
    """Clé de la chaîne dans la base des demandes (même nom)."""
    try:
        for k, v in (fb("/channels") or {}).items():
            if _key((v or {}).get("name")) == _key(c.get("name")):
                return k
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- mise à jour de Studio (même depuis le téléphone)
upd = {"t": 0, "latest": "", "state": "", "pct": 0, "error": ""}


def _vnum(v):
    try:
        return int(str(v).strip())
    except ValueError:
        return -1


def check_update(force=False):
    if not force and time.time() - upd["t"] < 6 * 3600:
        return upd
    upd["t"] = time.time()
    try:
        req = urllib.request.Request(UPDATE_URL + "studio-version.json?t=%d" % time.time(), headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as r:
            upd["latest"] = str(json.loads(r.read()).get("version") or "")
        upd["error"] = ""
    except Exception:
        upd["error"] = "Impossible de vérifier (internet ?)"
    return upd


def update_available():
    return getattr(sys, "frozen", False) and _vnum(upd["latest"]) > _vnum(VERSION) >= 0 or (os.environ.get("FRS_FORCE_UPDATABLE") and _vnum(upd["latest"]) > _vnum(VERSION))


def _retry(fn, tries=20, wait=0.5):
    """Windows (antivirus, indexation) bloque parfois un fichier quelques secondes : on réessaie."""
    for i in range(tries):
        try:
            return fn()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def restart_into(exe, folder, env):
    """Arrête le serveur, lance la nouvelle version, puis quitte (sans fenêtre d'erreur, et sans jamais rester bloqué)."""
    UPDATING["on"] = True
    srv = SERVER.get("srv")
    if srv:
        t = threading.Thread(target=srv.shutdown, daemon=True)  # arrête la boucle proprement (sinon : WinError 10038)
        t.start()
        t.join(3)
        try:
            srv.server_close()    # libère le port pour la nouvelle version
        except Exception:
            pass
    flags = (0x00000008 | 0x00000200) if WIN else 0
    try:
        subprocess.Popen([exe, "--silent", "--after-update"], cwd=folder, env=env, creationflags=flags, close_fds=True)
    finally:
        os._exit(0)           # quoi qu'il arrive, l'ancienne version s'en va


UPDATING = {"on": False}


def do_update():
    """Télécharge la nouvelle version, prend la place de l'ancienne et redémarre Studio."""
    exe = sys.executable
    folder = os.path.dirname(exe)
    stamp = int(time.time())
    # le téléchargement se fait dans le dossier de Studio (pas à côté du programme), pour que rien ne le touche
    os.makedirs(os.path.join(DATA, "maj"), exist_ok=True)
    new = os.path.join(DATA, "maj", "FrigoRoseStudio-%d.exe" % stamp)
    moved = None
    try:
        upd.update(state="download", pct=0, error="")
        req = urllib.request.Request(UPDATE_URL + "FrigoRoseStudio.exe?t=%d" % time.time(), headers={"User-Agent": "FrigoRoseStudio/" + VERSION})
        with urllib.request.urlopen(req, timeout=60) as r, open(new, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                b = r.read(1 << 16)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if total:
                    upd["pct"] = 100 * got / total
        with open(new, "rb") as f:
            head = f.read(2)
        if head != b"MZ" or os.path.getsize(new) < 3_000_000:
            raise RuntimeError("fichier reçu abîmé, réessaie")
        upd["state"] = "restart"
        time.sleep(1.5)  # laisse l'antivirus finir de regarder le fichier
        # l'ancienne version prend un nom unique (au cas où une vieille copie serait encore bloquée)
        moved = os.path.join(folder, "FrigoRoseStudio.ancien-%d.exe" % stamp)
        _retry(lambda: os.replace(exe, moved))
        try:
            _retry(lambda: shutil.move(new, exe))
        except OSError:
            _retry(lambda: os.replace(moved, exe))  # on remet l'ancienne : Studio reste utilisable
            moved = None
            raise
        env = {k: v for k, v in os.environ.items() if k not in ("FRS_VERSION", "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL")}
        time.sleep(0.5)
        restart_into(exe, folder, env)
    except Exception as e:
        try:
            if os.path.exists(new):
                os.remove(new)
        except OSError:
            pass
        msg = str(e)
        if isinstance(e, PermissionError):
            msg = "Windows bloque le fichier (antivirus ?). Réessaie dans une minute ; si ça recommence, redémarre le PC."
        upd.update(state="", error="Mise à jour ratée : %s" % msg)


def cleanup_old_exe():
    """Supprime les restes des mises à jour (anciennes versions, téléchargements ratés) — seulement ceux d'avant ce démarrage."""
    if not getattr(sys, "frozen", False):
        return
    started = time.time()
    folder = os.path.dirname(sys.executable)
    me = os.path.normcase(os.path.abspath(sys.executable))
    places = [(folder, r"^FrigoRoseStudio\.(ancien|nouveau)(-\d+)?\.exe$"), (os.path.join(DATA, "maj"), r"^FrigoRoseStudio-\d+\.exe$")]
    for _ in range(40):  # l'ancienne version met parfois un peu de temps à se fermer (antivirus…)
        left = 0
        for d, pat in places:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                f = os.path.join(d, n)
                if not re.match(pat, n, re.I) or os.path.normcase(os.path.abspath(f)) == me:
                    continue
                try:
                    if os.path.getmtime(f) > started - 5 or upd.get("state"):
                        continue  # une mise à jour est peut-être en train de l'écrire
                    os.remove(f)
                except OSError:
                    left += 1
        if not left:
            return
        time.sleep(3)


SERVER = {"srv": None}

# ---------------------------------------------------------------- serveur web local
# ---------- Appli télé dans un navigateur (iPad…) : Studio sert l'appli et relaie Tunarr sur la même adresse ----------
TVAPP_URL = os.environ.get("FRS_TVAPP_URL") or "https://dylker24.github.io/frigo-rose-tv/"
_tvapp = {"t": 0, "b": None}


def tv_app_html():
    """La dernière version de l'appli télé (gardée 10 min, et une copie sur le disque si internet coupe)."""
    cache = os.path.join(DATA, "tvapp.html")
    if _tvapp["b"] and time.time() - _tvapp["t"] < 600:
        return _tvapp["b"]
    try:
        b = urllib.request.urlopen(urllib.request.Request(TVAPP_URL + "?v=%d" % time.time(), headers={"Cache-Control": "no-cache"}), timeout=8).read()
        if len(b) > 20000:
            _tvapp.update(t=time.time(), b=b)
            if cache:
                try:
                    with open(cache, "wb") as f:
                        f.write(b)
                except OSError:
                    pass
            return b
    except Exception:
        pass
    if _tvapp["b"]:
        return _tvapp["b"]
    if cache and os.path.exists(cache):
        with open(cache, "rb") as f:
            _tvapp["b"] = f.read()
        return _tvapp["b"]
    return None


class _NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_tun_opener = urllib.request.build_opener(_NoRedir)


def _tun_rewrite(text, me=""):
    """Les adresses de Tunarr dans les listes de lecture passent elles aussi par Studio."""
    base = _fast(conf.get("tunarr_url", "http://localhost:8000")).rstrip("/")
    out = []
    for line in text.split("\n"):
        l = line.strip()
        if l.startswith(base):
            line = me + "/tv/t" + l[len(base):]
        elif re.match(r"https?://[^/]+:8000/", l):
            line = me + "/tv/t/" + l.split("/", 3)[3]
        elif l.startswith("/") and not l.startswith("/tv/t/"):
            line = "/tv/t" + l
        elif 'URI="/' in line:
            line = line.replace('URI="/', 'URI="/tv/t/')
        out.append(line)
    return "\n".join(out)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if not any(k.lower() == "cache-control" for k in (headers or {})):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def jf_proxy(self, u):
        path = u.path[len("/tv/jf"):]
        if ".." in path or not re.fullmatch(r"/Videos/[0-9a-fA-F-]{20,40}/[\w./-]+", path):
            return self.send(400, {"error": "chemin"})
        url = _fast(conf["jellyfin_url"]) + quote(path, safe="/._-") + ("?" + u.query if u.query else "")
        req = urllib.request.Request(url, headers={"X-Emby-Token": conf["jellyfin_key"], "Authorization": 'MediaBrowser Client="Frigo Rose TV", Device="TV", DeviceId="frigo-rose-tv", Version="%s", Token="%s"' % (VERSION, conf["jellyfin_key"])})
        try:
            r = urllib.request.urlopen(req, timeout=90)
        except urllib.error.HTTPError as e:
            return self.send(e.code, {"error": "Jellyfin %d" % e.code}, headers={"Access-Control-Allow-Origin": "*"})
        except Exception:
            return self.send(502, {"error": "Jellyfin injoignable"}, headers={"Access-Control-Allow-Origin": "*"})
        with r:
            ct = r.headers.get("Content-Type") or "application/octet-stream"
            n = r.headers.get("Content-Length")
            if n is None or "mpegurl" in ct.lower() or path.endswith(".m3u8"):
                b = r.read()
                return self.send(200, b, ct, headers={"Access-Control-Allow-Origin": "*"})
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", n)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    break

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def guard(self):
        # on refuse les requêtes envoyées par d'autres sites : l'origine doit être la page Studio elle-même
        o = self.headers.get("Origin")
        if not o:
            return True
        oh = urlparse(o).hostname
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return oh in ("localhost", "127.0.0.1") or (oh and oh == host)

    def allowed(self):
        """Seulement ce PC et les appareils de ton Tailscale (ton téléphone). Le reste du réseau est refusé."""
        return allowed_client(self.client_address[0])

    def handle_one_request(self):
        try:
            if not self.allowed():
                self.close_connection = True
                try:
                    self.connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
                return
        except Exception:
            return
        return super().handle_one_request()

    def local(self):
        try:
            a = ipaddress.ip_address(self.client_address[0].split("%")[0])
            return a.is_loopback or bool(getattr(a, "ipv4_mapped", None) and a.ipv4_mapped.is_loopback)
        except ValueError:
            return False

    def authed(self):
        """Sur le PC : toujours. Depuis le téléphone : il faut le code (cookie de session)."""
        if self.local():
            return True
        if not conf.get("pin_hash"):
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "frs_s" and hmac.compare_digest(v, session_token()):
                return True
        return False

    def need_auth(self):
        return self.send(401, {"error": "code", "need": "pin" if conf.get("pin_hash") else "nopin"})

    def tun_proxy(self, u, method="GET"):
        path = u.path[len("/tv/t"):]
        if ".." in path or not re.match(r"/(api|stream|images|m3u|hls)/", path):
            return self.send(400, {"error": "chemin"})
        url = _fast(conf.get("tunarr_url", "http://localhost:8000")).rstrip("/") + path + ("?" + u.query if u.query else "")
        hd = {"User-Agent": self.headers.get("User-Agent") or "FrigoRoseTV", "X-Forwarded-For": self.client_address[0]}
        req = urllib.request.Request(url, method=method, headers=hd)
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            r = _tun_opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                loc = e.headers["Location"]
                loc = _tun_rewrite(loc) if not loc.startswith("/tv/t") else loc
                if not loc.startswith("/"):
                    loc = "/tv/t" + path.rsplit("/", 1)[0] + "/" + loc
                self.send_response(e.code)
                self.send_header("Location", loc)
                self.send_header("Content-Length", "0")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            try:
                b = e.read()
            except Exception:
                b = b""
            return self.send(e.code, b, e.headers.get("Content-Type") or "application/json", headers=cors)
        except Exception:
            return self.send(502, {"error": "Tunarr injoignable"}, headers=cors)
        with r:
            ct = r.headers.get("Content-Type") or "application/octet-stream"
            if "mpegurl" in ct.lower() or path.endswith(".m3u8") or path.endswith(".m3u"):
                me = "http://" + (self.headers.get("Host") or "localhost:%d" % PORT)
                b = _tun_rewrite(r.read().decode("utf-8", "replace"), me).encode("utf-8")
                return self.send(200, b, ct, headers=cors)
            n = r.headers.get("Content-Length")
            if (n is None and not path.startswith("/stream")) or (n is not None and int(n) < (1 << 20) and not path.startswith("/stream")):
                return self.send(200, r.read(), ct, headers=cors)
            self.send_response(200)
            self.send_header("Content-Type", ct)
            if n is not None:
                self.send_header("Content-Length", n)
            else:
                self.close_connection = True  # flux sans fin : envoyé tel quel jusqu'à la fermeture
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    return

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path.startswith("/tv/t/api/channels/") and u.path.endswith("/sessions"):
            return self.tun_proxy(u, "DELETE")
        return self.send(404, {"error": "?"})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/tv", "/tv/"):
            b = tv_app_html()
            if not b:
                return self.send(503, "<h1>Frigo Rosé TV</h1><p>L'appli n'a pas pu être chargée, réessaie dans un instant.</p>".encode("utf-8"), "text/html; charset=utf-8")
            return self.send(200, b, "text/html; charset=utf-8")
        if u.path.startswith("/tv/t/"):
            return self.tun_proxy(u)
        if u.path in ("/", "/index.html"):
            return self.send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/manifest.webmanifest":
            return self.send(200, json.dumps({"name": "Frigo Rosé Studio", "short_name": "Studio", "start_url": "/", "display": "standalone",
                                              "background_color": "#0A0A0B", "theme_color": "#0A0A0B", "icons": [{"src": "/icon.png", "sizes": "256x256", "type": "image/png"}]}).encode(), "application/manifest+json")
        if u.path == "/icon.png":
            ic = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.png")
            if os.path.exists(ic):
                with open(ic, "rb") as f:
                    return self.send(200, f.read(), "image/png")
        if u.path.startswith("/tv/jf/Videos/"):
            # la vidéo de Jellyfin passe par Studio (Jellyfin refuse souvent les appareils Tailscale, et la clé reste sur le PC)
            return self.jf_proxy(u)
        if u.path in ("/tv/lineup", "/tv/next"):
            cors = {"Access-Control-Allow-Origin": "*"}
            qs = parse_qs(u.query)
            try:
                if u.path == "/tv/lineup":
                    return self.send(200, tv_lineup((qs.get("num") or [""])[0]), headers=cors)
                return self.send(200, tv_choose_next((qs.get("num") or [""])[0], (qs.get("id") or [""])[0], (qs.get("who") or [""])[0][:40]), headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path in ("/tv/replay", "/tv/replay/play"):
            cors = {"Access-Control-Allow-Origin": "*"}
            try:
                if u.path == "/tv/replay":
                    return self.send(200, {"items": tv_replay()}, headers=cors)
                return self.send(200, tv_replay_play((parse_qs(u.query).get("ext") or [""])[0]), headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path == "/tv/info":
            cors = {"Access-Control-Allow-Origin": "*"}
            qs = parse_qs(u.query)
            try:
                return self.send(200, tv_info((qs.get("pid") or [""])[0], (qs.get("ext") or [""])[0]), headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path == "/api/tidy" and self.local():
            return self.send(200, {k: TIDY[k] for k in ("state", "done", "total", "err", "plan")})
        if u.path == "/tv/cats":
            return self.send(200, {"cats": conf.get("chcats") or {}}, headers={"Access-Control-Allow-Origin": "*"})
        if u.path in ("/tv/vod/chans", "/tv/vod/chan", "/tv/vod/play"):
            cors = {"Access-Control-Allow-Origin": "*"}
            qs = parse_qs(u.query)
            try:
                if u.path == "/tv/vod/play":
                    return self.send(200, tv_vod_play((qs.get("ext") or [""])[0]), headers=cors)
                tv_catalog()
                if u.path == "/tv/vod/chans":
                    return self.send(200, {"chans": _vod["chans"]}, headers=cors)
                c = _vod["by"].get((qs.get("num") or [""])[0])
                if not c:
                    return self.send(404, {"error": "chaîne inconnue"}, headers=cors)
                return self.send(200, c, headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path == "/tv/catalog":
            cors = {"Access-Control-Allow-Origin": "*"}
            try:
                return self.send(200, {"items": tv_catalog()}, headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path == "/tv/img":
            cors = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"}
            qs = parse_qs(u.query)
            iid, kind = (qs.get("id") or [""])[0], (qs.get("t") or ["Primary"])[0]
            try:
                w = max(32, min(1600, int((qs.get("w") or ["600"])[0])))
            except ValueError:
                w = 600
            if not re.fullmatch(r"[0-9a-fA-F-]{20,40}", iid) or kind not in ("Primary", "Backdrop", "Logo", "Thumb"):
                return self.send(400, {"error": "image"}, headers=cors)
            try:
                data, ct = _jf_raw("/Items/%s/Images/%s?maxWidth=%d&quality=82" % (iid, kind, w))
            except Exception:
                return self.send(404, {"error": "pas d'image"}, headers=cors)
            return self.send(200, data, ct, headers=cors)
        if u.path == "/tv/startover":
            cors = {"Access-Control-Allow-Origin": "*"}
            try:
                return self.send(200, tv_startover((parse_qs(u.query).get("num") or [""])[0]), headers=cors)
            except Exception as e:
                return self.send(400, {"error": str(e)}, headers=cors)
        if u.path.startswith("/api/") and not self.authed():
            return self.need_auth()
        if u.path.startswith("/api/tn/"):
            q = parse_qs(u.query)
            try:
                if u.path == "/api/tn/channels":
                    return self.send(200, {"channels": tn_channels()})
                if u.path == "/api/tn/channel":
                    d = tn_programming((q.get("id") or [""])[0])
                    d.pop("raw", None)
                    d["can_undo"] = any(x.startswith((q.get("id") or [""])[0] + "-") for x in os.listdir(BACKUPS))
                    return self.send(200, d)
                if u.path == "/api/tn/health":
                    return self.send(200, {"channels": channel_health((q.get("fresh") or [""])[0] == "1")})
                if u.path == "/api/tn/libs":
                    return self.send(200, {"libs": tn_libraries()})
                if u.path == "/api/tn/lib":
                    lid = (q.get("id") or [""])[0]
                    fr = (q.get("fresh") or [""])[0] == "1"
                    un = lib_unscanned(lid, fr)
                    if un:  # Jellyfin a des vidéos que Tunarr n'a pas encore lues : on rattrape en arrière-plan
                        threading.Thread(target=catch_up_lib, args=(lid,), daemon=True).start()
                    return self.send(200, {"programs": tn_library_programs(lid, fresh=fr), "unscanned": sorted(un.values())})
                if u.path == "/api/tn/for":
                    return self.send(200, {"channel": channel_for_lib((q.get("path") or [""])[0])})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path.startswith("/api/media/"):
            q = parse_qs(u.query)
            a = lambda k: (q.get(k) or [""])[0]
            try:
                if u.path == "/api/media/libs":
                    return self.send(200, {"libs": media_libs()})
                if u.path == "/api/media/ls":
                    return self.send(200, media_ls(a("path")))
                if u.path == "/api/media/search":
                    return self.send(200, {"results": media_search(a("q"))})
                if u.path == "/api/media/info":
                    return self.send(200, media_info(a("path")))
                if u.path == "/api/media/ident":
                    return self.send(200, media_ident(a("path")))
                if u.path == "/api/media/jfimg":
                    _need_key()
                    if not re.fullmatch(r"[0-9a-fA-F-]{20,40}", a("id")):
                        return self.send(400, {"error": "id"})
                    try:
                        data, ct = _jf_raw("/Items/%s/Images/Primary?maxWidth=360&quality=85" % a("id"))
                    except urllib.error.HTTPError:
                        return self.send(404, {"error": "pas d'image"})
                    return self.send(200, data, ct)
                if u.path == "/api/media/drives":
                    return self.send(200, media_drives())
                if u.path == "/api/media/heavy":
                    return self.send(200, media_heavy(a("path")))
                if u.path == "/api/media/pending":
                    return self.send(200, {"libs": media_pending()})
                if u.path == "/api/media/trash":
                    return self.send(200, {"items": trash_list()})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/plinfo" and self.authed():
            url = (parse_qs(u.query).get("url") or [""])[0]
            try:
                r = subprocess.run([tool("yt-dlp"), "--flat-playlist", "-J", "--no-warnings", "--encoding", "utf-8", "--yes-playlist", "--playlist-end", "1000", url],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=NOWIN, env=run_env())
                d = json.loads(r.stdout or "{}")
                ents = [e for e in (d.get("entries") or []) if e]
                return self.send(200, {"title": d.get("title") or "", "count": len(ents) or d.get("playlist_count") or 0, "channel": d.get("channel") or d.get("uploader") or ""})
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if u.path == "/api/dvd" and self.authed():
            ds = dvd_drives()
            for d in ds:
                d["title"] = dvd_title(d["label"]) if d["disc"] else ""
            return self.send(200, {"drives": ds, "makemkv": makemkv_tool()})
        if u.path == "/api/alerts/test" and self.authed():
            ok = notify_phone("Frigo Rosé Studio", "✅ Les alertes de Frigo Rosé Studio arrivent bien sur ton téléphone")
            return self.send(200 if ok else 400, {"ok": ok, "error": "" if ok else "L'envoi n'a pas marché (internet coupé sur le PC ?)"})
        if u.path == "/api/alerts/check" and self.authed():
            threading.Thread(target=lambda: (monitor_once(), autoconv_once()), daemon=True).start()
            return self.send(200, {"ok": True})
        if u.path == "/api/alerts" and self.authed():
            return self.send(200, {"alerts": sorted([dict(v, key=k) for k, v in ALERTS.items()], key=lambda a: -a["t"]),
                                   "topic": conf.get("ntfy_topic", ""), "ntfy": conf.get("ntfy_on", True), "auto": conf.get("auto_convert", True)})
        if u.path == "/api/watch" and self.authed():
            try:
                return self.send(200, {"watch": fb("/watch") or {}, "hidden": [k for k, v in (fb("/profsync") or {}).items() if isinstance(v, dict) and v.get("hidden")]})
            except Exception as e:
                return self.send(200, {"watch": {}, "error": str(e)})
        if u.path == "/api/tvmsg" and self.authed():
            try:
                return self.send(200, {"message": fb("/config/message")})
            except Exception:
                return self.send(200, {"message": None})
        if u.path == "/api/debug":
            return self.send(200, {"log": BGLOG, "never": sorted(_never), "scan": {k: int(time.time() - v) for k, v in _scan_t.items()}})
        if u.path == "/api/pc":
            return self.send(200, {**pc_state(), "away": away_status()})
        if u.path == "/api/yt":
            q = (parse_qs(u.query).get("q") or [""])[0].strip()
            if not q:
                return self.send(400, {"error": "Tape quelque chose à chercher"})
            if not setup["ready"]:
                return self.send(400, {"error": "Studio se prépare encore, réessaie dans un instant"})
            try:
                return self.send(200, {"results": yt_search(q)})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/state":
            with jobs_lock:
                js = [public_job(j) for j in jobs]
            libs = jf_libraries()
            c = {k: v for k, v in conf.items() if k != "jellyfin_key"}
            c["has_key"] = bool(conf["jellyfin_key"])
            c["autostart"] = autostart_get()
            c["watch_msg"] = _watch["msg"]
            c.pop("pin_hash", None); c.pop("secret", None)
            c["has_pin"] = bool(conf.get("pin_hash"))
            c["subs"] = [{k: v for k, v in x.items() if k != "seen"} for x in conf.get("subs") or []]
            c.pop("tunarr_cmd", None); c.pop("jellyfin_cmd", None)
            c["tunarr"] = tunarr_status()
            return self.send(200, {"setup": setup, "jobs": js, "conf": c, "libs": libs["libs"], "jf_error": libs["error"], "version": VERSION, "update": {"latest": upd["latest"], "available": bool(update_available()), "state": upd["state"], "pct": upd["pct"], "error": upd["error"],
                      "busy": any(j["status"] == "en cours" for j in jobs)}, "sync": sync_state(), "win": WIN, "phone": ("http://%s:%d/" % (tailscale_ip(), PORT)) if tailscale_ip() and HOST != "127.0.0.1" else "", "remote": not ipaddress.ip_address(self.client_address[0].split("%")[0]).is_loopback})
        if u.path == "/api/ls":
            q = parse_qs(u.query)
            return self.send(200, list_dir((q.get("path") or [""])[0]))
        self.send(404, {"error": "introuvable"})

    def do_PUT(self):
        if not self.guard():
            return self.send(403, {"error": "refusé"})
        if not self.authed():
            return self.need_auth()
        u = urlparse(self.path)
        if u.path == "/api/tn/logo":
            q = parse_qs(u.query)
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else b""
            try:
                url = channel_logo((q.get("channel") or [""])[0], data, (q.get("name") or ["logo.png"])[0])
                return self.send(200, {"icon": url, "msg": "Logo changé"})
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if u.path != "/api/upload":
            return self.send(404, {"error": "introuvable"})
        q = parse_qs(u.query)
        dest = (q.get("dest") or [""])[0]
        if not dest or not os.path.isdir(dest):
            return self.send(400, {"error": "Choisis une médiathèque qui existe"})
        sub = safe_name((q.get("sub") or [""])[0]) if (q.get("sub") or [""])[0].strip() else ""
        folder = os.path.join(dest, sub) if sub else dest
        os.makedirs(folder, exist_ok=True)
        name = safe_name((q.get("name") or ["video.mp4"])[0])
        n = int(self.headers.get("Content-Length") or 0)
        final = unique_path(os.path.join(folder, name))
        tmp = final + ".envoi"
        try:
            with open(tmp, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left > 0:
                raise IOError("envoi interrompu")
            os.replace(tmp, final)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send(400, {"error": "L'envoi a été coupé, réessaie"})
        j = after_upload(final, folder, (q.get("tchan") or [""])[0], (q.get("sync") or [""])[0] == "1")
        return self.send(200, {"ok": True, "path": final, "job": j["id"]})

    def do_POST(self):
        if not self.guard():
            return self.send(403, {"error": "refusé"})
        u = urlparse(self.path)
        b = self.body()
        if u.path == "/api/login":
            if not conf.get("pin_hash"):
                return self.need_auth()
            if time.time() < _fails["until"]:
                return self.send(429, {"error": "Trop d'essais, attends une minute"})
            if hmac.compare_digest(pin_hash(str(b.get("pin") or "")), conf["pin_hash"]):
                _fails["n"] = 0
                return self.send(200, {"ok": True}, headers={"Set-Cookie": "frs_s=%s; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict" % session_token()})
            _fails["n"] += 1
            if _fails["n"] >= 5:
                _fails.update(n=0, until=time.time() + 60)
            return self.send(403, {"error": "Mauvais code"})
        if not self.authed():
            return self.need_auth()
        if u.path == "/api/pin":
            pin = str(b.get("pin") or "")
            if not re.fullmatch(r"\d{4,8}", pin):
                return self.send(400, {"error": "Le code doit faire 4 à 8 chiffres"})
            conf["pin_hash"] = pin_hash(pin)
            save_conf(conf)
            # le téléphone qui change le code reste connecté
            return self.send(200, {"ok": True}, headers={"Set-Cookie": "frs_s=%s; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict" % session_token()})
        if u.path == "/api/download":
            url = (b.get("url") or "").strip()
            dest = (b.get("dest") or "").strip()
            if not re.match(r"^https?://", url):
                return self.send(400, {"error": "Colle un lien qui commence par https://"})
            if not dest:
                return self.send(400, {"error": "Choisis où ranger la vidéo"})
            pl = bool(b.get("playlist")) if b.get("playlist") is not None else is_playlist(url)
            j = new_job("dl", url=url, dest=dest, quality=b.get("quality") or "1080", sub=b.get("sub") or "", playlist=pl, pl_folder=b.get("pl_folder", True) is not False, thumb=bool(b.get("thumb", True)),
                        title=(b.get("title") or url), req=(b.get("req") or "") if re.fullmatch(r"[-\w]{1,64}", b.get("req") or "") else "", req_title=b.get("req_title") or "",
                        tchan=b.get("tchan") or "")
            conf["last_dest"] = dest
            conf["quality"] = b.get("quality") or conf["quality"]
            save_conf(conf)
            return self.send(200, {"id": j["id"]})
        if u.path == "/api/convert":
            files = [f for f in (b.get("files") or []) if isinstance(f, str)]
            if not files:
                return self.send(400, {"error": "Choisis au moins un fichier"})
            ids = [new_job("conv", src=f, dest=(b.get("dest") or "").strip(), delete=bool(b.get("delete")), title=os.path.basename(f))["id"] for f in files]
            if b.get("dest"):
                conf["last_dest"] = b["dest"]
                save_conf(conf)
            return self.send(200, {"ids": ids})
        if u.path == "/api/cancel":
            with jobs_lock:
                for j in jobs:
                    if j["id"] == b.get("id") and j["status"] in ("attente", "en cours"):
                        j["cancel"] = True
                        if j["status"] == "attente":
                            j.update(status="annulé", msg="Annulé")
                        elif j.get("proc"):
                            try:
                                j["proc"].kill()
                            except Exception:
                                pass
            return self.send(200, {"ok": True})
        if u.path == "/api/retry":
            with jobs_lock:
                for j in jobs:
                    if j["id"] == b.get("id") and j["status"] in ("erreur", "annulé"):
                        j.update(status="attente", pct=0, msg="En attente", error="", cancel=False, _lastfrac=0)
            wake.set()
            return self.send(200, {"ok": True})
        if u.path == "/api/clear":
            with jobs_lock:
                jobs[:] = [j for j in jobs if j["status"] in ("attente", "en cours")]
            return self.send(200, {"ok": True})
        if u.path == "/api/config":
            for k in ("jellyfin_url", "jellyfin_key", "tunarr_url"):
                if k in b and isinstance(b[k], str):
                    conf[k] = b[k].strip()
            if isinstance(b.get("folders"), list):
                conf["folders"] = [f for f in b["folders"] if isinstance(f, str) and f.strip()]
            save_conf(conf)
            _tn["t"] = 0
            libs = jf_libraries(force=True)
            return self.send(200, {"ok": True, "libs": libs["libs"], "jf_error": libs["error"]})
        if u.path == "/api/convert_dir":
            root = (b.get("path") or "").strip()
            if not os.path.isdir(root):
                return self.send(400, {"error": "Dossier introuvable"})
            n, skipped = queue_folder(root, (b.get("dest") or "").strip(), bool(b.get("delete")), b.get("recursive", True) is not False)
            return self.send(200, {"added": n, "skipped": skipped})
        if u.path == "/api/watch":
            for k in ("watch_src", "watch_dest"):
                if isinstance(b.get(k), str):
                    conf[k] = b[k].strip()
            for k in ("watch_on", "watch_delete", "auto_convert", "ntfy_on"):
                if k in b:
                    conf[k] = bool(b[k])
            if b.get("parallel"):
                conf["parallel"] = max(1, min(4, int(b["parallel"])))
            if conf["watch_on"] and not os.path.isdir(conf.get("watch_src") or ""):
                conf["watch_on"] = False
                save_conf(conf)
                return self.send(400, {"error": "Choisis d'abord le dossier à surveiller"})
            save_conf(conf)
            _watch["seen"].clear()
            return self.send(200, {"ok": True})
        if u.path == "/api/away":
            return self.send(200, away_set(bool(b.get("on"))))
        if u.path == "/api/hidewin":
            try:
                return self.send(200, {"done": hide_services()})
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if u.path == "/api/tvmsg":
            try:
                if b.get("clear"):
                    fb("/config/message", "PUT", {"text": "", "at": int(time.time() * 1000)})
                    return self.send(200, {"ok": True})
                t = str(b.get("text") or "").strip()[:220]
                if not t:
                    return self.send(400, {"error": "Écris ton message"})
                now = int(time.time() * 1000)
                fb("/config/message", "PUT", {"text": t, "from": str(b.get("from") or "").strip()[:30], "at": now, "until": now + 24 * 3600 * 1000})
                return self.send(200, {"ok": True})
            except Exception:
                return self.send(400, {"error": "Impossible d'envoyer le message (pas d'internet ?)"})
        if u.path == "/api/autostart":
            try:
                return self.send(200, {"on": autostart_set(bool(b.get("on")))})
            except Exception as e:
                return self.send(400, {"error": "Impossible de régler le démarrage automatique (%s)" % e})
        if u.path == "/api/mkdir":
            p = os.path.join(b.get("path") or "", (b.get("name") or "").strip())
            try:
                os.makedirs(p, exist_ok=True)
                return self.send(200, {"path": p})
            except OSError:
                return self.send(400, {"error": "Impossible de créer ce dossier"})
        if u.path == "/api/open":
            p = b.get("path") or ""
            if WIN and os.path.exists(p):
                subprocess.Popen(["explorer", "/select,", p] if os.path.isfile(p) else ["explorer", p])
            return self.send(200, {"ok": True})
        if u.path.startswith("/api/media/"):
            try:
                act = u.path[len("/api/media/"):]
                if act == "idsearch":
                    return self.send(200, {"results": media_idsearch(b.get("id") or "", b.get("kind") or "", b.get("q") or "", b.get("year") or "")})
                if act == "idapply":
                    return self.send(200, {"ok": media_idapply(b.get("id") or "", b.get("result"), b.get("lib") or "", bool(b.get("keep")))})
                if act == "dvd":
                    return self.send(200, {"job": dvd_import(b.get("drive") or "", b.get("name") or "", b.get("dest") or "", b.get("tchan") or "", b.get("eject", True) is not False)})
                if act == "merge":
                    return self.send(200, {"job": media_merge(b.get("paths") or [], b.get("name") or ""), "name": merge_name(sorted(b.get("paths") or []))})
                if act == "mergename":
                    return self.send(200, {"name": merge_name(sorted(b.get("paths") or [], key=lambda x: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.basename(x))]))})
                if act == "rename":
                    return self.send(200, {"path": media_rename(b.get("path") or "", b.get("name") or "")})
                if act == "move":
                    return self.send(200, {"paths": media_move(b.get("paths") or [], b.get("dest") or "")})
                if act == "mkdir":
                    _check_inside(b.get("path") or "")
                    pth = os.path.join(b["path"], safe_name(b.get("name") or ""))
                    os.makedirs(pth, exist_ok=True)
                    return self.send(200, {"path": pth})
                if act == "trash":
                    return self.send(200, media_trash(b.get("paths") or [], b.get("unlink", True) is not False))
                if act == "restore":
                    return self.send(200, {"n": trash_restore(set(b.get("ids") or []))})
                if act == "empty":
                    return self.send(200, {"n": trash_empty(set(b["ids"]) if b.get("ids") else None)})
                if act == "newlib":
                    return self.send(200, media_newlib(b.get("name") or "", b.get("kind") or "videos", b.get("channel", True) is not False, int(b["number"]) if str(b.get("number") or "").isdigit() else None, b.get("drive") or ""))
                if act == "renamelib":
                    return self.send(200, {"name": media_renamelib(b.get("name") or "", b.get("new") or "")})
                if act == "removelib":
                    media_removelib(b.get("name") or "")
                    return self.send(200, {"ok": True})
                if act == "scan":
                    media_scan(b.get("name") or "", bool(b.get("fill")))
                    return self.send(200, {"ok": True})
                if act == "convert":
                    return self.send(200, {"ids": media_convert(b.get("paths") or [], bool(b.get("night")))})
                if act == "mkchannel":
                    name = b.get("name") or ""
                    ch = tn_create_channel(name, int(b["number"]) if str(b.get("number") or "").isdigit() else None)

                    def fill_later():
                        tunarr_sync_libs(name)
                        for _ in range(6):
                            time.sleep(float(os.environ.get("FRS_TUNARR_DELAY", "20")))
                            try:
                                if channel_fill(ch["id"]):
                                    return
                            except Exception:
                                pass
                    threading.Thread(target=fill_later, daemon=True).start()
                    return self.send(200, {"channel": ch})
                if act == "fillnew":
                    return self.send(200, {"added": channel_fill(b.get("channel") or "")})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
            return self.send(404, {"error": "introuvable"})
        if u.path == "/api/tn/set":
            try:
                cid = b.get("channel") or ""
                if "name" in b:
                    return self.send(200, {"name": channel_rename(cid, b["name"]), "msg": "Chaîne renommée"})
                if "number" in b:
                    return self.send(200, {"number": channel_renumber(cid, b["number"]), "msg": "Numéro changé"})
                if b.get("delete"):
                    return self.send(200, {"msg": "« %s » supprimée" % channel_delete(cid)})
                if b.get("logo_reset"):
                    tn_channel_put(cid, icon_path="")
                    return self.send(200, {"msg": "Logo retiré"})
                return self.send(400, {"error": "Rien à changer"})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/tn/ghosts":
            try:
                cid = b.get("channel") or ""
                f, _r, ft = repair_ghosts(cid)  # d'abord : remettre la nouvelle version à la même place quand elle existe
                n, titles = remove_ghosts(cid)   # puis retirer ce qui n'existe vraiment plus
                _health["data"] = None
                parts = []
                if f:
                    parts.append("%d remis dans leur nouvelle version : %s" % (f, ", ".join(ft[:4])))
                if n:
                    parts.append("%d introuvable%s retiré%s : %s" % (n, "s" if n > 1 else "", "s" if n > 1 else "", ", ".join(titles[:4])))
                return self.send(200, {"removed": n, "fixed": f, "titles": titles, "msg": " · ".join(parts) if parts else "Aucun fantôme sur cette chaîne"})
            except Exception as e:
                return self.send(400, {"error": str(e)})
        if u.path == "/api/tn/fill":
            try:
                n = channel_fill(b.get("channel") or "")
                return self.send(200, {"added": n, "msg": ("%d programme%s ajouté%s à la fin de la chaîne" % (n, "s" if n > 1 else "", "s" if n > 1 else "")) if n else "Tout est déjà sur la chaîne"})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/tn/add":
            try:
                cid = b.get("channel") or ""
                ids = set(b.get("ids") or [])
                pool = []
                for lid in b.get("libs") or []:
                    pool += tn_library_programs(lid)
                progs = [p for p in pool if p["id"] in ids]
                seen, uniq = set(), []
                for p in progs:
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        uniq.append(p)
                if b.get("order") == "shuffle":
                    import random
                    random.shuffle(uniq)
                n = tn_add(cid, uniq, b.get("announce", True) is not False)
                return self.send(200, {"msg": "%d programme%s ajouté%s à la fin de la chaîne" % (n, "s" if n > 1 else "", "s" if n > 1 else "")})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/chcats":
            cid, cats = str(b.get("channel") or ""), b.get("cats")
            if not re.fullmatch(r"[-\w]{8,64}", cid):
                return self.send(400, {"error": "chaîne inconnue"})
            m = dict(conf.get("chcats") or {})
            if cats is None:
                m.pop(cid, None)
            else:
                m[cid] = [c for c in cats if c in ("acteurs", "comedie", "films", "series", "jeunesse", "divert")]
            conf["chcats"] = m
            save_conf(conf)
            return self.send(200, {"ok": True, "chcats": m})
        if u.path == "/api/news":
            try:
                if b.get("action") == "remove" and re.fullmatch(r"[-\w]{1,64}", b.get("id") or ""):
                    fb("/news/" + b["id"], "DELETE")
                    return self.send(200, {"ok": True})
                d = fb("/news") or {}
                out = sorted(({"id": k, **v} for k, v in d.items() if isinstance(v, dict)), key=lambda x: -(x.get("at") or 0))[:30]
                return self.send(200, {"news": out})
            except Exception as e:
                return self.send(400, {"error": "Base des demandes injoignable"})
        if u.path == "/api/tn/edit":
            try:
                return self.send(200, {"msg": tn_edit(b.get("channel") or "", b.get("action"), b)})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/tn/job":
            # « Mettre sur une chaîne » après coup, depuis la file d'attente
            with jobs_lock:
                j = next((x for x in jobs if x["id"] == b.get("id")), None)
            if not j or j["status"] != "fini":
                return self.send(400, {"error": "Vidéo introuvable"})
            j["tchan"], j["tchan_done"] = b.get("channel") or "", False
            threading.Thread(target=lambda: (tunarr_scan_for([j]), time.sleep(float(os.environ.get("FRS_TUNARR_DELAY", "20"))), auto_add_to_channels([j], False),
                                             time.sleep(60), auto_add_to_channels([j], True)), daemon=True).start()
            return self.send(200, {"msg": "Studio l'ajoute dès que Tunarr la voit (moins d'une minute)"})
        if u.path == "/api/restart":
            k = b.get("what")
            if k == "pc":
                if not WIN:
                    return self.send(400, {"error": "Seulement sur Windows"})
                subprocess.Popen(["shutdown", "/r", "/t", "60", "/c", "Frigo Rosé Studio : redémarrage demandé depuis le téléphone"], creationflags=NOWIN)
                return self.send(200, {"msg": "Le PC redémarre dans 1 minute. Studio reviendra tout seul si le démarrage automatique est activé."})
            if k == "pc_cancel":
                if WIN:
                    subprocess.Popen(["shutdown", "/a"], creationflags=NOWIN)
                return self.send(200, {"msg": "Redémarrage annulé"})
            if k not in ("tunarr", "jellyfin"):
                return self.send(400, {"error": "?"})
            try:
                msg = restart(k)
                _pc["t"] = 0
                _tn["t"] = 0
                return self.send(200, {"msg": msg})
            except Exception as e:
                return self.send(400, {"error": str(e), "trace": _trace()})
        if u.path == "/api/subs":
            act = b.get("action")
            with _subs_lock:
                subs = conf.setdefault("subs", [])
                if act == "add":
                    url = (b.get("url") or "").strip()
                    if not re.match(r"^https?://", url):
                        url = "https://" + url if re.match(r"^(www\.|m\.)?youtube\.com/", url) else url
                    if not re.match(r"^https?://", url):
                        return self.send(400, {"error": "Colle le lien de la chaîne YouTube (ex : youtube.com/@StudioDanielle)"})
                    url = re.sub(r"^https?://(m\.)?youtube\.com/", "https://www.youtube.com/", url).rstrip("/")
                    url = re.sub(r"^(https://www\.youtube\.com/@[^/?#]+)/(videos|featured|streams)$", r"\1", url)
                    if not b.get("dest"):
                        return self.send(400, {"error": "Choisis la médiathèque"})
                    if any(x["url"] == url for x in subs):
                        return self.send(400, {"error": "Déjà dans tes abonnements"})
                    sb = {"id": uuid.uuid4().hex[:8], "url": url, "dest": b["dest"], "sub": b.get("sub") or "", "quality": b.get("quality") or "1080", "name": "", "seen": [], "last": 0,
                          "tchan": b.get("tchan") or ""}
                    try:
                        n = check_sub(sb, first_take=max(0, min(20, int(b.get("take") or 0))))
                    except Exception as e:
                        return self.send(400, {"error": str(e), "trace": _trace()})
                    subs.append(sb)
                    save_conf(conf)
                    return self.send(200, {"ok": True, "name": sb["name"], "queued": n})
                sb = next((x for x in subs if x["id"] == b.get("id")), None)
                if not sb:
                    return self.send(404, {"error": "Abonnement introuvable"})
                if act == "remove":
                    subs.remove(sb)
                    save_conf(conf)
                    return self.send(200, {"ok": True})
                if act == "check":
                    try:
                        n = check_sub(sb)
                    except Exception as e:
                        sb["error"] = str(e)
                        save_conf(conf)
                        return self.send(400, {"error": str(e), "trace": _trace()})
                    save_conf(conf)
                    return self.send(200, {"ok": True, "queued": n})
            return self.send(400, {"error": "?"})
        if u.path == "/api/tidy":
            if b.get("plan"):
                try:
                    return self.send(200, {"plan": tidy_plan()})
                except Exception as e:
                    return self.send(400, {"error": str(e)})
            if TIDY["state"] in ("prépare", "renomme", "tunarr"):
                return self.send(409, {"error": "Déjà en cours"})
            threading.Thread(target=tidy_run, args=(set(b.get("ids") or []) or None,), daemon=True).start()
            return self.send(200, {"ok": True})
        if u.path == "/api/update":
            if b.get("check"):
                check_update(force=True)
                return self.send(200, {"latest": upd["latest"], "available": bool(update_available()), "error": upd["error"]})
            if not update_available():
                return self.send(400, {"error": "Studio est déjà à jour"})
            if upd["state"]:
                return self.send(200, {"ok": True})
            if any(j["status"] == "en cours" for j in jobs) and not b.get("force"):
                return self.send(409, {"error": "Une vidéo est en cours : attends qu'elle finisse (ou annule-la)"})
            threading.Thread(target=do_update, daemon=True).start()
            return self.send(200, {"ok": True})
        if u.path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        self.send(404, {"error": "introuvable"})


PAGE = r"""__PAGE__"""


def main():
    srv = None
    for _ in range(30 if "--after-update" in sys.argv else 1):
        try:
            srv = ThreadingHTTPServer((HOST, PORT), H)
            break
        except OSError:
            time.sleep(0.5)
    if not srv:
        # déjà lancée : on ouvre juste la page
        webbrowser.open("http://localhost:%d/" % PORT)
        return
    SERVER["srv"] = srv
    threading.Thread(target=cleanup_old_exe, daemon=True).start()
    def _upd_loop():
        time.sleep(20)
        while True:
            check_update()
            time.sleep(1800)
    threading.Thread(target=_upd_loop, daemon=True).start()
    threading.Thread(target=ensure_tools, daemon=True).start()
    threading.Thread(target=worker, args=("dl",), daemon=True).start()
    for _ in range(4):
        threading.Thread(target=worker, args=("conv",), daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=subs_loop, daemon=True).start()
    threading.Thread(target=away_loop, daemon=True).start()
    threading.Thread(target=services_boot, daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=autopilot_loop, daemon=True).start()
    if not os.environ.get("FRS_NO_BROWSER") and "--silent" not in sys.argv:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open("http://localhost:%d/" % PORT)), daemon=True).start()
    try:
        srv.serve_forever()
    except Exception:
        pass
    # le serveur s'est arrêté : pendant une mise à jour, on laisse la nouvelle version se lancer
    while UPDATING["on"]:
        time.sleep(1)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    page = os.path.join(getattr(sys, "_MEIPASS", here), "page.html")
    if os.path.exists(page):
        with open(page, encoding="utf-8") as f:
            PAGE = f.read()
    main()
