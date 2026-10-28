# -*- coding: utf-8 -*-
"""Frigo Rosé Studio : télécharger des vidéos (YouTube…) et convertir des fichiers en MP4,
rangés directement dans les dossiers Jellyfin.

L'appli tourne sur le PC et s'ouvre dans le navigateur (http://localhost:8795).
Elle s'appuie sur yt-dlp (téléchargement), ffmpeg (conversion) et deno (nécessaire à yt-dlp pour YouTube),
téléchargés automatiquement au premier lancement sur Windows.
"""
import json, os, re, shutil, string, subprocess, sys, threading, time, urllib.request, urllib.error, uuid, webbrowser, zipfile, io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP = "Frigo Rosé Studio"
VERSION = "1.0"
PORT = int(os.environ.get("FRS_PORT", "8795"))
HOST = os.environ.get("FRS_HOST", "127.0.0.1")
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
                "watch_on": False, "watch_src": "", "watch_dest": "", "watch_delete": False, "parallel": 2}
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
         "t": time.time(), "proc": None, "cancel": False, **kw}
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
    if not conf["jellyfin_key"]:
        return
    with jobs_lock:
        _ref["jobs"].append(j)
        if _ref["timer"]:
            _ref["timer"].cancel()
        delay = 0.5 if os.environ.get("FRS_FAST_REFRESH") else 15

        def go():
            with jobs_lock:
                js, _ref["jobs"] = _ref["jobs"], []
            if jf_refresh():
                for x in js:
                    x["msg"] += " · Jellyfin actualisé"
        _ref["timer"] = threading.Timer(delay, go)
        _ref["timer"].daemon = True
        _ref["timer"].start()


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
           "--progress-template", "download:FRP %(progress.downloaded_bytes)s/%(progress.total_bytes)s/%(progress.total_bytes_estimate)s/%(info.playlist_index)s/%(info.n_entries)s",
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
                d, t, te, idx, n = line[4:].split("/")
                tot = float(t) if t not in ("NA", "None", "") else (float(te) if te not in ("NA", "None", "") else 0)
                frac = float(d) / tot if tot else 0
                # vidéo et son arrivent en 2 fichiers : chaque moitié compte pour la moitié de la barre
                if frac < 0.02 and j.get("_lastfrac", 0) > 0.9:
                    part = min(part + 1, 1)
                j["_lastfrac"] = frac
                one = (part + frac) / 2
                if idx not in ("NA", "None", "") and n not in ("NA", "None", ""):
                    one = (int(idx) - 1 + one) / max(1, int(n))
                j["pct"] = max(0, min(95, 95 * one))
            except (ValueError, ZeroDivisionError):
                pass
        elif line.startswith("ERROR:"):
            last_err = line[6:].strip()
        elif "[download] Destination" in line or "Merging formats" in line:
            pass
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


# ---------------------------------------------------------------- serveur web local
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
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
        # page locale uniquement : on refuse les requêtes envoyées par d'autres sites
        o = self.headers.get("Origin")
        return not o or urlparse(o).hostname in ("localhost", "127.0.0.1")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/state":
            with jobs_lock:
                js = [public_job(j) for j in jobs]
            libs = jf_libraries()
            c = {k: v for k, v in conf.items() if k != "jellyfin_key"}
            c["has_key"] = bool(conf["jellyfin_key"])
            c["autostart"] = autostart_get()
            c["watch_msg"] = _watch["msg"]
            return self.send(200, {"setup": setup, "jobs": js, "conf": c, "libs": libs["libs"], "jf_error": libs["error"], "version": VERSION, "win": WIN})
        if u.path == "/api/ls":
            q = parse_qs(u.query)
            return self.send(200, list_dir((q.get("path") or [""])[0]))
        self.send(404, {"error": "introuvable"})

    def do_POST(self):
        if not self.guard():
            return self.send(403, {"error": "refusé"})
        u = urlparse(self.path)
        b = self.body()
        if u.path == "/api/download":
            url = (b.get("url") or "").strip()
            dest = (b.get("dest") or "").strip()
            if not re.match(r"^https?://", url):
                return self.send(400, {"error": "Colle un lien qui commence par https://"})
            if not dest:
                return self.send(400, {"error": "Choisis où ranger la vidéo"})
            pl = bool(b.get("playlist")) if b.get("playlist") is not None else is_playlist(url)
            j = new_job("dl", url=url, dest=dest, quality=b.get("quality") or "1080", sub=b.get("sub") or "", playlist=pl, thumb=bool(b.get("thumb", True)), title=url)
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
            for k in ("jellyfin_url", "jellyfin_key"):
                if k in b and isinstance(b[k], str):
                    conf[k] = b[k].strip()
            if isinstance(b.get("folders"), list):
                conf["folders"] = [f for f in b["folders"] if isinstance(f, str) and f.strip()]
            save_conf(conf)
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
        if u.path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        self.send(404, {"error": "introuvable"})


PAGE = r"""__PAGE__"""


def main():
    try:
        srv = ThreadingHTTPServer((HOST, PORT), H)
    except OSError:
        # déjà lancée : on ouvre juste la page
        webbrowser.open("http://localhost:%d/" % PORT)
        return
    threading.Thread(target=ensure_tools, daemon=True).start()
    threading.Thread(target=worker, args=("dl",), daemon=True).start()
    for _ in range(4):
        threading.Thread(target=worker, args=("conv",), daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
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
