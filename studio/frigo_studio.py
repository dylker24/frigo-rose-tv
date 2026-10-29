# -*- coding: utf-8 -*-
"""Frigo Rosé Studio : télécharger des vidéos (YouTube…) et convertir des fichiers en MP4,
rangés directement dans les dossiers Jellyfin.

L'appli tourne sur le PC et s'ouvre dans le navigateur (http://localhost:8795).
Elle s'appuie sur yt-dlp (téléchargement), ffmpeg (conversion) et deno (nécessaire à yt-dlp pour YouTube),
téléchargés automatiquement au premier lancement sur Windows.
"""
import hashlib, hmac, secrets, json, os, re, shutil, string, subprocess, sys, threading, time, urllib.request, urllib.error, uuid, webbrowser, zipfile, io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

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
def jf(path, method="GET", body=None, timeout=8):
    url = conf["jellyfin_url"].rstrip("/") + path
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
                libs.append({"name": v.get("Name", ""), "type": v.get("CollectionType") or "", "path": loc})
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
        if w % 2 or h % 2:
            args += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
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
                j = None if busy >= lim else next((x for x in jobs if x["status"] == "attente" and x["kind"] == kind), None)
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
    req = urllib.request.Request(conf.get("tunarr_url", "http://localhost:8000").rstrip("/") + path, data=b"" if method == "POST" else None, method=method)
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


def do_convert(j):
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
    if j.get("delete"):
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
        tpl = os.path.join(folder, "%(playlist_title)s" if not sub else "", "%(playlist_index)02d - %(title)s.%(ext)s")
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
            with urllib.request.urlopen(conf["jellyfin_url"].rstrip("/") + "/System/Info/Public", timeout=4):
                pass
        return True
    except Exception:
        return False


def away_status():
    return {"on": bool(conf.get("away")), "nosleep": bool(conf.get("away")) and WIN, "autostart": autostart_get(),
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
        with urllib.request.urlopen(conf["jellyfin_url"].rstrip("/") + "/System/Info/Public", timeout=3) as r:
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


def after_upload(path, dest, tchan=""):
    """Fichier reçu : s'il est déjà en MP4 H.264, c'est fini ; sinon on le convertit à la même place."""
    if os.path.splitext(path)[1].lower() in VIDEO_EXT and needs_conversion(path):
        return new_job("conv", src=path, dest=dest, delete=True, title=os.path.basename(path), uploaded=True, tchan=tchan)
    j = new_job("up", title=os.path.basename(path), out=[path], pct=100, msg="Reçu · déjà en MP4", status="fini", tchan=tchan)
    refresh_soon(j)
    return j


# ---------------------------------------------------------------- programmation des chaînes Tunarr (onglet « Chaînes »)
BACKUPS = os.path.join(DATA, "sauvegardes-chaines")
os.makedirs(BACKUPS, exist_ok=True)


def tn(path, method="GET", body=None, timeout=20):
    req = urllib.request.Request(conf.get("tunarr_url", "http://localhost:8000").rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None),
                                 headers={"Content-Type": "application/json"})
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


def tn_library_programs(lib_id, fresh=False):
    """Tout le contenu d'une médiathèque Tunarr (on passe par sa base, pas par sa recherche qui oublie certains titres)."""
    c = _libc.get(lib_id)
    if c and not fresh and time.time() - c[0] < 30:
        return c[1]
    r = tn("/api/media-libraries/%s/programs" % lib_id, timeout=60)
    arr = r if isinstance(r, list) else (r or {}).get("result") or []
    out = []
    for x in arr:
        pg = x.get("program") or x
        if not pg.get("uuid") and not x.get("id"):
            continue
        out.append({"id": pg.get("uuid") or x.get("id"), "title": pg.get("title") or "", "duration": x.get("duration") or pg.get("duration") or 0,
                    "type": pg.get("type") or "", "year": pg.get("year"), "show": (pg.get("grandparent") or {}).get("title") if isinstance(pg.get("grandparent"), dict) else "",
                    "season": pg.get("seasonNumber") or (pg.get("parent") or {}).get("index") if isinstance(pg.get("parent"), dict) else pg.get("seasonNumber"),
                    "episode": pg.get("episodeNumber") or pg.get("index")})
    _libc[lib_id] = (time.time(), out)
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
        items.append({"i": i, "type": t, "id": it.get("id"), "title": title, "duration": it.get("duration") or it.get("durationMs") or 0})
    return {"name": (r.get("name") or "").strip(), "number": r.get("number"), "lineup": items, "raw": r.get("lineup") or []}


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


def tn_save(cid, lineup):
    items = [x for x in (_clean_item(i) for i in lineup) if x]
    tn("/api/channels/%s/programming" % cid, "POST", {"type": "manual", "lineup": items, "append": False}, timeout=60)
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
    tn("/api/channels/%s/programming" % cid, "POST", {"type": "manual", "lineup": items, "append": True}, timeout=60)
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


def do_update():
    """Télécharge la nouvelle version, prend la place de l'ancienne et redémarre Studio."""
    exe = sys.executable
    folder = os.path.dirname(exe)
    stamp = int(time.time())
    new = os.path.join(folder, "FrigoRoseStudio.nouveau-%d.exe" % stamp)
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
            _retry(lambda: os.replace(new, exe))
        except OSError:
            _retry(lambda: os.replace(moved, exe))  # on remet l'ancienne : Studio reste utilisable
            moved = None
            raise
        env = {k: v for k, v in os.environ.items() if k not in ("FRS_VERSION", "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL")}
        time.sleep(0.5)
        try:
            SERVER["srv"].server_close()   # libère le port pour la nouvelle version
        except Exception:
            pass
        flags = (0x00000008 | 0x00000200) if WIN else 0
        subprocess.Popen([exe, "--silent", "--after-update"], cwd=folder, env=env, creationflags=flags, close_fds=True)
        os._exit(0)
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
    """Supprime les restes des mises à jour (anciennes versions, téléchargements ratés)."""
    if not getattr(sys, "frozen", False):
        return
    folder = os.path.dirname(sys.executable)
    me = os.path.normcase(os.path.abspath(sys.executable))
    for _ in range(120):  # l'ancienne version met parfois un peu de temps à se fermer (antivirus…)
        left = []
        for n in os.listdir(folder):
            if re.match(r"^FrigoRoseStudio\.(ancien|nouveau)(-\d+)?\.exe$", n, re.I):
                f = os.path.join(folder, n)
                if os.path.normcase(os.path.abspath(f)) == me:
                    continue
                try:
                    os.remove(f)
                except OSError:
                    left.append(n)
        if not left:
            return
        time.sleep(3)


SERVER = {"srv": None}

# ---------------------------------------------------------------- serveur web local
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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

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

    def do_GET(self):
        u = urlparse(self.path)
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
                    return self.send(200, {"programs": tn_library_programs((q.get("id") or [""])[0], fresh=(q.get("fresh") or [""])[0] == "1")})
                if u.path == "/api/tn/for":
                    return self.send(200, {"channel": channel_for_lib((q.get("path") or [""])[0])})
            except Exception as e:
                return self.send(400, {"error": str(e)})
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
                return self.send(400, {"error": str(e)})
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
                      "busy": any(j["status"] == "en cours" for j in jobs)}, "win": WIN, "phone": ("http://%s:%d/" % (tailscale_ip(), PORT)) if tailscale_ip() and HOST != "127.0.0.1" else "", "remote": not ipaddress.ip_address(self.client_address[0].split("%")[0]).is_loopback})
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
        j = after_upload(final, folder, (q.get("tchan") or [""])[0])
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
            j = new_job("dl", url=url, dest=dest, quality=b.get("quality") or "1080", sub=b.get("sub") or "", playlist=pl, thumb=bool(b.get("thumb", True)),
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
            for k in ("watch_on", "watch_delete"):
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
        if u.path == "/api/tn/fill":
            try:
                n = channel_fill(b.get("channel") or "")
                return self.send(200, {"added": n, "msg": ("%d programme%s ajouté%s à la fin de la chaîne" % (n, "s" if n > 1 else "", "s" if n > 1 else "")) if n else "Tout est déjà sur la chaîne"})
            except Exception as e:
                return self.send(400, {"error": str(e)})
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
                return self.send(400, {"error": str(e)})
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
                return self.send(400, {"error": str(e)})
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
                return self.send(400, {"error": str(e)})
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
                        return self.send(400, {"error": str(e)})
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
                        return self.send(400, {"error": str(e)})
                    save_conf(conf)
                    return self.send(200, {"ok": True, "queued": n})
            return self.send(400, {"error": "?"})
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
    if not os.environ.get("FRS_NO_BROWSER") and "--silent" not in sys.argv:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open("http://localhost:%d/" % PORT)), daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    page = os.path.join(getattr(sys, "_MEIPASS", here), "page.html")
    if os.path.exists(page):
        with open(page, encoding="utf-8") as f:
            PAGE = f.read()
    main()
