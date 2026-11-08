# -*- coding: utf-8 -*-
"""Dynamusique : la musique de Dylan et Léna.

Tourne sur le PC (comme Frigo Rosé Studio) et s'ouvre sur les téléphones via Tailscale.
- télécharge le son des vidéos YouTube (yt-dlp) et les range dans « Musique\\Dynamusique »
- profils, j'aime, playlists, historique
- sert l'appli téléphone (à ajouter sur l'écran d'accueil) et les fichiers audio
"""
import hashlib, io, ipaddress, json, os, random, re, secrets, shutil, socket, subprocess, sys, threading, time, traceback
import urllib.request, uuid, webbrowser, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
import email.utils, http.client, encodings.idna, json.decoder, mimetypes  # noqa: F401  (tout chargé dès le départ pour le .exe)
try:
    import winreg
except ImportError:
    winreg = None

APP = "Dynamusique"
PORT = int(os.environ.get("DYN_PORT", "8797"))
HOST = os.environ.get("DYN_HOST", "0.0.0.0")
WIN = os.name == "nt"
NOWIN = 0x08000000 if WIN else 0
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

if WIN:
    LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    DATA = os.path.join(LOCAL, "Dynamusique")
    STUDIO_BIN = os.path.join(LOCAL, "FrigoRoseStudio", "bin")
else:
    DATA = os.environ.get("DYN_DATA") or os.path.join(os.path.expanduser("~"), ".dynamusique")
    STUDIO_BIN = ""
BIN = os.path.join(DATA, "bin")
DB = os.path.join(DATA, "bibliotheque.json")
MUSIC = os.environ.get("DYN_MUSIC") or os.path.join(os.path.expanduser("~"), "Music", "Dynamusique")
for d in (DATA, BIN, MUSIC):
    os.makedirs(d, exist_ok=True)


def log(msg):
    try:
        with open(os.path.join(DATA, "journal.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- bibliothèque
COLORS = ["#FF5A36", "#8B5CF6", "#14B8A6", "#F59E0B", "#3B82F6", "#EC4899", "#22C55E"]
lock = threading.RLock()


def new_db():
    return {"songs": {}, "profiles": [
        {"id": "dylan", "name": "Dylan", "color": "#FF5A36", "likes": [], "playlists": [], "recent": [], "plays": {}},
        {"id": "lena", "name": "Léna", "color": "#8B5CF6", "likes": [], "playlists": [], "recent": [], "plays": {}},
    ]}


def load_db():
    try:
        with open(DB, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("songs", {})
        d.setdefault("profiles", [])
        for p in d["profiles"]:
            for k, v in (("likes", []), ("playlists", []), ("recent", []), ("plays", {})):
                p.setdefault(k, v)
        return d
    except FileNotFoundError:
        return new_db()
    except Exception:
        log("bibliothèque illisible, copie de secours : " + traceback.format_exc())
        try:
            shutil.copy(DB, DB + ".abime-%d" % int(time.time()))
        except OSError:
            pass
        return new_db()


db = load_db()
VERSION_DB = {"n": 0}


def save_db():
    with lock:
        VERSION_DB["n"] += 1
        tmp = DB + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DB)
        # petite sauvegarde quotidienne
        bk = os.path.join(DATA, "sauvegarde-%s.json" % time.strftime("%Y-%m-%d"))
        if not os.path.exists(bk):
            try:
                shutil.copy(DB, bk)
                old = sorted(x for x in os.listdir(DATA) if x.startswith("sauvegarde-"))
                for x in old[:-7]:
                    os.remove(os.path.join(DATA, x))
            except OSError:
                pass


def profile(pid):
    for p in db["profiles"]:
        if p["id"] == pid:
            return p
    raise ValueError("Profil introuvable")


def playlist(p, plid):
    for pl in p["playlists"]:
        if pl["id"] == plid:
            return pl
    raise ValueError("Playlist introuvable")


# ---------------------------------------------------------------- outils (yt-dlp, ffmpeg, deno)
TOOLS = {
    "yt-dlp": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "ffmpeg": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "deno": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
}
setup = {"ready": False, "step": "Préparation…", "pct": 0, "error": ""}


def tool(name):
    exe = name + (".exe" if WIN else "")
    for d in (BIN, STUDIO_BIN):
        if d and os.path.exists(os.path.join(d, exe)):
            return os.path.join(d, exe)
    return shutil.which(name) or os.path.join(BIN, exe)


def run_env():
    e = dict(os.environ)
    e["PATH"] = os.pathsep.join(x for x in (BIN, STUDIO_BIN, e.get("PATH", "")) if x)
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def fetch(url, share):
    req = urllib.request.Request(url, headers={"User-Agent": "Dynamusique"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf, got, base = io.BytesIO(), 0, setup["pct"]
        while True:
            c = r.read(1 << 16)
            if not c:
                break
            buf.write(c)
            got += len(c)
            if total:
                setup["pct"] = int(base + share * got / total)
        return buf.getvalue()


def ensure_tools():
    try:
        if WIN:
            need = [n for n in ("yt-dlp", "ffmpeg", "deno") if not os.path.exists(tool(n))]
            share = 100.0 / max(1, len(need))
            for n in need:
                setup["step"] = "Téléchargement de %s…" % n
                data = fetch(TOOLS[n], share)
                if n == "yt-dlp":
                    with open(os.path.join(BIN, "yt-dlp.exe"), "wb") as f:
                        f.write(data)
                else:
                    z = zipfile.ZipFile(io.BytesIO(data))
                    want = {"ffmpeg": ["ffmpeg.exe", "ffprobe.exe"], "deno": ["deno.exe"]}[n]
                    for m in z.namelist():
                        b = m.rsplit("/", 1)[-1]
                        if b in want:
                            with z.open(m) as s, open(os.path.join(BIN, b), "wb") as d:
                                shutil.copyfileobj(s, d)
        if not os.environ.get("DYN_FAKE"):
            miss = [n for n in ("yt-dlp", "ffmpeg") if not (os.path.exists(tool(n)) or shutil.which(n))]
            if miss:
                raise RuntimeError("outils introuvables : " + ", ".join(miss))
        setup.update(ready=True, step="Prêt", pct=100)
        threading.Thread(target=update_ytdlp, daemon=True).start()
    except Exception as e:
        log(traceback.format_exc())
        setup["error"] = "Préparation impossible (%s). Vérifie internet puis relance Dynamusique." % e


def update_ytdlp():
    """YouTube change souvent : yt-dlp se met à jour au lancement (sauf si c'est celui de Studio, qui s'en occupe)."""
    p = tool("yt-dlp")
    if WIN and p.startswith(BIN):
        try:
            subprocess.run([p, "-U"], capture_output=True, timeout=120, creationflags=NOWIN, env=run_env())
        except Exception:
            pass


def nice_err(msg):
    m = msg.lower()
    if "sign in to confirm" in m or "bot" in m:
        return "YouTube bloque un peu, réessaie dans quelques minutes."
    if "private" in m:
        return "Cette vidéo est privée."
    if "unavailable" in m or "not available" in m:
        return "Vidéo indisponible."
    if "copyright" in m:
        return "Vidéo retirée par YouTube."
    return msg.strip()[:160] or "Erreur inconnue"


def ytdlp(args, timeout=90):
    r = subprocess.run([tool("yt-dlp"), "--no-warnings", "--encoding", "utf-8"] + args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=NOWIN, env=run_env())
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
        raise RuntimeError(nice_err(err[-1][6:] if err else r.stderr[-200:]))
    return r.stdout


YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def yt_id(url):
    m = YT_ID.search(url or "")
    return m.group(1) if m else (url if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or "") else None)


def thumb(vid):
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid


def yt_search(q, n=15):
    if os.environ.get("DYN_FAKE"):
        return [{"id": "fake%07d" % i, "title": "%s (Clip Officiel) %d" % (q, i), "channel": "Artiste %d - Topic" % i, "duration": 180 + i,
                 "thumb": ""} for i in range(1, 6)]
    data = json.loads(ytdlp(["--flat-playlist", "-J", "ytsearch%d:%s" % (n, q)]) or "{}")
    out = []
    for e in data.get("entries") or []:
        vid = e.get("id")
        if vid and e.get("ie_key") in (None, "Youtube"):
            out.append({"id": vid, "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
                        "duration": e.get("duration") or 0, "thumb": thumb(vid)})
    return out


def yt_playlist(url):
    if os.environ.get("DYN_FAKE"):
        return "Playlist test", [{"id": "fakepl%05d" % i, "title": "Son %d" % i} for i in range(1, 4)]
    data = json.loads(ytdlp(["--flat-playlist", "-J", url], timeout=180) or "{}")
    items = [{"id": e["id"], "title": e.get("title") or ""} for e in (data.get("entries") or []) if e.get("id")]
    return data.get("title") or "Playlist YouTube", items


JUNK = re.compile(r"\s*[\(\[](?:[^\)\]]*?(?:official|officiel|clip|lyrics?|paroles|audio|video|vidéo|visuali[sz]er|hd|hq|4k|remaster(?:ed)?|music video|explicit)[^\)\]]*)[\)\]]", re.I)


def clean(title, info):
    """Titre / artiste propres à partir des infos YouTube."""
    artist = info.get("artist") or info.get("creator") or ""
    track = info.get("track") or ""
    if artist and track:
        return track.strip(), artist.split(",")[0].strip()
    t = JUNK.sub("", title or "").strip()
    t = re.sub(r"\s*\|.*$", "", t).strip()
    ch = (info.get("channel") or info.get("uploader") or "").strip()
    ch = re.sub(r"\s*-\s*Topic$|VEVO$|\s*Officiel$|\s*Official$", "", ch, flags=re.I).strip()
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
    if m:
        return m.group(2).strip(' "'), m.group(1).strip()
    return t.strip(' "') or title, ch


# ---------------------------------------------------------------- téléchargements
jobs = []          # {id, yt, title, thumb, state: attente|telechargement|fini|erreur, pct, error, profile, playlist}
jobq = []
jcond = threading.Condition()


def add_job(vid, title="", pid=None, plid=None):
    with lock:
        for s in db["songs"].values():
            if s.get("yt") == vid:
                if pid and plid:
                    attach(s["id"], pid, plid)
                return {"id": None, "already": s["id"]}
    with jcond:
        for j in jobs:
            if j["yt"] == vid and j["state"] in ("attente", "telechargement"):
                return j
        j = {"id": uuid.uuid4().hex[:10], "yt": vid, "title": title or "Recherche du titre…", "thumb": thumb(vid), "state": "attente",
             "pct": 0, "error": "", "profile": pid, "playlist": plid, "t": time.time()}
        jobs.append(j)
        jobq.append(j)
        jcond.notify()
        return j


def attach(sid, pid, plid):
    with lock:
        try:
            pl = playlist(profile(pid), plid)
        except ValueError:
            return
        if sid not in pl["songs"]:
            pl["songs"].append(sid)
            save_db()


def fake_download(j, base):
    for i in range(1, 6):
        j["pct"] = i * 20
        time.sleep(0.15)
    subprocess.run([tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=%d:duration=20" % random.randint(300, 800),
                    "-c:a", "aac", base + ".m4a"], check=True)
    subprocess.run([tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "gradients=s=320x180:seed=%d" % random.randint(1, 999),
                    "-frames:v", "1", base + ".jpg"])
    return {"title": j["title"] if not j["title"].startswith("Recherche") else "Son test " + j["yt"][-3:], "channel": "Artiste test",
            "duration": 20, "id": j["yt"]}


PCT = re.compile(r"\[download\]\s+([\d.]+)%")


def download(j):
    sid = uuid.uuid4().hex[:12]
    base = os.path.join(MUSIC, sid)
    if os.environ.get("DYN_FAKE"):
        info = fake_download(j, base)
    else:
        cmd = [tool("yt-dlp"), "--no-warnings", "--encoding", "utf-8", "--newline", "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio/best",
               "-x", "--audio-format", "m4a", "--audio-quality", "0", "--write-thumbnail", "--convert-thumbnails", "jpg",
               "--write-info-json", "-o", base + ".%(ext)s",
               "https://www.youtube.com/watch?v=" + j["yt"]]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=NOWIN, env=run_env())
        errs = []
        for line in p.stdout:
            m = PCT.search(line)
            if m:
                j["pct"] = min(99, int(float(m.group(1))))
            elif line.startswith("ERROR"):
                errs.append(line[6:].strip())
        p.wait()
        if p.returncode != 0 or not os.path.exists(base + ".m4a"):
            for ext in (".m4a", ".jpg", ".info.json", ".webm", ".part"):
                try:
                    os.remove(base + ext)
                except OSError:
                    pass
            raise RuntimeError(nice_err(errs[-1] if errs else "téléchargement raté"))
        try:
            with open(base + ".info.json", encoding="utf-8") as f:
                info = json.load(f)
            os.remove(base + ".info.json")
        except (OSError, ValueError):
            info = {}
    title, artist = clean(info.get("title") or j["title"], info)
    song = {"id": sid, "yt": j["yt"], "title": title, "artist": artist or "Artiste inconnu", "duration": int(info.get("duration") or 0),
            "file": sid + ".m4a", "cover": sid + ".jpg" if os.path.exists(base + ".jpg") else "", "added": int(time.time()), "by": j.get("profile")}
    with lock:
        db["songs"][sid] = song
        if j.get("profile") and j.get("playlist"):
            try:
                pl = playlist(profile(j["profile"]), j["playlist"])
                pl["songs"].append(sid)
            except ValueError:
                pass
        save_db()
    j["song"] = sid
    j["title"] = title


def worker():
    while True:
        with jcond:
            while not jobq or not setup["ready"]:
                jcond.wait(2)
            j = jobq.pop(0)
        j["state"] = "telechargement"
        try:
            download(j)
            j["state"], j["pct"] = "fini", 100
        except Exception as e:
            log(traceback.format_exc())
            j["state"], j["error"] = "erreur", str(e)
        j["t"] = time.time()
        with jcond:
            # on garde les 30 derniers terminés
            done = [x for x in jobs if x["state"] in ("fini", "erreur")]
            for x in done[:-30]:
                jobs.remove(x)


def add_link(url, pid, plid):
    """Lien YouTube : une vidéo ou une playlist entière (qui devient une playlist Dynamusique)."""
    if "list=" in url and not (yt_id(url) and "watch" in url and "start_radio" in url):
        name, items = yt_playlist(url)
        if not items:
            raise ValueError("Playlist vide")
        if not plid and pid:
            with lock:
                pl = {"id": uuid.uuid4().hex[:8], "name": name[:60], "songs": [], "created": int(time.time())}
                profile(pid)["playlists"].append(pl)
                save_db()
            plid = pl["id"]
        for it in items:
            add_job(it["id"], it["title"], pid, plid)
        return {"count": len(items), "playlist": plid, "name": name}
    vid = yt_id(url)
    if not vid:
        raise ValueError("Ce lien n'est pas un lien YouTube")
    r = add_job(vid, "", pid, plid)
    return {"count": 1, "already": r.get("already")}


# ---------------------------------------------------------------- accès (PC + Tailscale)
TS_NET = ipaddress.ip_network("100.64.0.0/10")
TS6_NET = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def allowed(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        if os.environ.get("DYN_ALLOW_ALL"):
            return True
        return a.is_loopback or (a.version == 4 and a in TS_NET) or (a.version == 6 and a in TS6_NET)
    except ValueError:
        return False


def tailscale_ip():
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ipaddress.ip_address(ip) in TS_NET:
                return ip
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.100.100.100", 80))
        ip = s.getsockname()[0]
        s.close()
        if ipaddress.ip_address(ip) in TS_NET:
            return ip
    except OSError:
        pass
    return ""


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart(on=None):
    if not (WIN and winreg and getattr(sys, "frozen", False)):
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            if on is True:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, '"%s" --silent' % sys.executable)
            elif on is False:
                try:
                    winreg.DeleteValue(k, APP)
                except OSError:
                    pass
            try:
                winreg.QueryValueEx(k, APP)
                return True
            except OSError:
                return False
    except OSError:
        return False


# ---------------------------------------------------------------- API
def public_state(pid=None):
    with lock:
        return {"v": VERSION_DB["n"], "setup": setup, "songs": list(db["songs"].values()), "profiles": db["profiles"],
                "jobs": [{k: j[k] for k in ("id", "yt", "title", "thumb", "state", "pct", "error", "profile", "playlist")} for j in jobs],
                "address": ("http://%s:%d" % (tailscale_ip(), PORT)) if tailscale_ip() else "", "autostart": autostart(),
                "music": MUSIC}


def api(path, b):
    pid = b.get("profile")
    if path == "/api/search":
        q = (b.get("q") or "").strip()
        if not q:
            return []
        if yt_id(q) and ("youtu" in q):
            return {"link": True}
        return yt_search(q)
    if path == "/api/add":
        url = (b.get("url") or "").strip()
        if b.get("yt"):
            r = add_job(b["yt"], b.get("title") or "", pid, b.get("playlist"))
            return {"count": 1, "already": r.get("already")}
        return add_link(url, pid, b.get("playlist"))
    if path == "/api/job/retry":
        for j in jobs:
            if j["id"] == b.get("id") and j["state"] == "erreur":
                j.update(state="attente", pct=0, error="")
                with jcond:
                    jobq.append(j)
                    jcond.notify()
        return {}
    if path == "/api/job/clear":
        with jcond:
            jobs[:] = [j for j in jobs if j["state"] in ("attente", "telechargement")]
        return {}
    with lock:
        if path == "/api/profile/save":
            if b.get("id"):
                p = profile(b["id"])
            else:
                p = {"id": uuid.uuid4().hex[:8], "likes": [], "playlists": [], "recent": [], "plays": {},
                     "color": COLORS[len(db["profiles"]) % len(COLORS)]}
                db["profiles"].append(p)
            p["name"] = (b.get("name") or p.get("name") or "Profil").strip()[:24]
            if b.get("color"):
                p["color"] = b["color"]
            save_db()
            return p
        if path == "/api/profile/delete":
            if len(db["profiles"]) <= 1:
                raise ValueError("Il faut garder au moins un profil")
            db["profiles"] = [p for p in db["profiles"] if p["id"] != b.get("id")]
            save_db()
            return {}
        p = profile(pid)
        if path == "/api/like":
            sid = b["song"]
            if sid in p["likes"]:
                p["likes"].remove(sid)
            else:
                p["likes"].insert(0, sid)
            save_db()
            return {"liked": sid in p["likes"]}
        if path == "/api/played":
            sid = b["song"]
            if sid in db["songs"]:
                p["recent"] = [sid] + [x for x in p["recent"] if x != sid][:49]
                p["plays"][sid] = p["plays"].get(sid, 0) + 1
                save_db()
            return {}
        if path == "/api/playlist/save":
            if b.get("id"):
                pl = playlist(p, b["id"])
            else:
                pl = {"id": uuid.uuid4().hex[:8], "songs": [], "created": int(time.time())}
                p["playlists"].append(pl)
            pl["name"] = (b.get("name") or pl.get("name") or "Nouvelle playlist").strip()[:60]
            if "songs" in b and isinstance(b["songs"], list):
                pl["songs"] = [s for s in b["songs"] if s in db["songs"]]
            save_db()
            return pl
        if path == "/api/playlist/delete":
            p["playlists"] = [x for x in p["playlists"] if x["id"] != b.get("id")]
            save_db()
            return {}
        if path == "/api/playlist/add":
            pl = playlist(p, b["id"])
            for sid in b.get("songs") or []:
                if sid in db["songs"] and sid not in pl["songs"]:
                    pl["songs"].append(sid)
            save_db()
            return pl
        if path == "/api/playlist/remove":
            pl = playlist(p, b["id"])
            pl["songs"] = [s for s in pl["songs"] if s != b.get("song")]
            save_db()
            return pl
        if path == "/api/song/edit":
            s = db["songs"][b["id"]]
            s["title"] = (b.get("title") or s["title"]).strip()[:120]
            s["artist"] = (b.get("artist") or s["artist"]).strip()[:80]
            save_db()
            return s
        if path == "/api/song/delete":
            s = db["songs"].pop(b["id"], None)
            if s:
                for pr in db["profiles"]:
                    pr["likes"] = [x for x in pr["likes"] if x != s["id"]]
                    pr["recent"] = [x for x in pr["recent"] if x != s["id"]]
                    pr["plays"].pop(s["id"], None)
                    for pl in pr["playlists"]:
                        pl["songs"] = [x for x in pl["songs"] if x != s["id"]]
                for f in (s.get("file"), s.get("cover")):
                    if f:
                        try:
                            os.remove(os.path.join(MUSIC, f))
                        except OSError:
                            pass
                save_db()
            return {}
    if path == "/api/autostart":
        return {"on": autostart(bool(b.get("on")))}
    raise ValueError("Action inconnue")


def read_asset(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return f.read()


MANIFEST = {"name": "Dynamusique", "short_name": "Dynamusique", "start_url": "/", "display": "standalone", "background_color": "#0B0B0D",
            "theme_color": "#0B0B0D", "icons": [{"src": "/icon.png", "sizes": "512x512", "type": "image/png"}]}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def guard(self):
        if not allowed(self.client_address[0]):
            self.send(403, "Accès réservé au PC et aux appareils Tailscale", "text/plain; charset=utf-8")
            return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.guard():
            return
        u = urlparse(self.path)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self.send(200, read_asset("page.html"), "text/html; charset=utf-8")
            if p == "/icon.png":
                return self.send(200, read_asset("icon.png"), "image/png", {"Cache-Control": "max-age=86400"})
            if p == "/manifest.webmanifest":
                return self.send(200, MANIFEST, "application/manifest+json")
            if p == "/api/state":
                return self.send(200, public_state())
            if p == "/api/ping":
                return self.send(200, {"v": VERSION_DB["n"], "busy": any(j["state"] in ("attente", "telechargement") for j in jobs),
                                       "jobs": [{k: j[k] for k in ("id", "state", "pct", "title")} for j in jobs]})
            m = re.fullmatch(r"/(audio|cover)/([0-9a-f]{12})", p)
            if m:
                s = db["songs"].get(m.group(2))
                f = s and (s["file"] if m.group(1) == "audio" else s.get("cover"))
                if not f:
                    return self.send(404, {"error": "introuvable"})
                return self.file(os.path.join(MUSIC, f), "audio/mp4" if m.group(1) == "audio" else "image/jpeg")
            self.send(404, {"error": "introuvable"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def file(self, path, ctype):
        """Envoi avec « Range » : obligatoire pour que l'iPhone lise l'audio et puisse avancer dans le son."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return self.send(404, {"error": "fichier manquant"})
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
        n = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", "max-age=31536000, immutable")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = n
            while left > 0:
                c = f.read(min(1 << 16, left))
                if not c:
                    break
                self.wfile.write(c)
                left -= len(c)

    def do_POST(self):
        if not self.guard():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}") if n else {}
            self.send(200, api(urlparse(self.path).path, b))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError, RuntimeError) as e:
            self.send(400, {"error": str(e) or "Erreur"})
        except subprocess.TimeoutExpired:
            self.send(400, {"error": "YouTube met trop de temps à répondre, réessaie."})
        except Exception as e:
            log(traceback.format_exc())
            self.send(500, {"error": "Erreur : %s" % e})


def main():
    try:
        srv = ThreadingHTTPServer((HOST, PORT), H)
    except OSError:
        webbrowser.open("http://localhost:%d/" % PORT)  # déjà lancé
        return
    first = not os.path.exists(os.path.join(DATA, ".lance"))
    if first:
        open(os.path.join(DATA, ".lance"), "w").close()
        autostart(True)  # démarre avec Windows : la musique est toujours dispo sur les téléphones
    threading.Thread(target=ensure_tools, daemon=True).start()
    for _ in range(2):
        threading.Thread(target=worker, daemon=True).start()
    if "--silent" not in sys.argv and not os.environ.get("DYN_NO_BROWSER"):
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open("http://localhost:%d/" % PORT)), daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
