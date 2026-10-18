"""Hall of Likes: visitor counter + leaderboards of the people who like ThePhantasmAl on AniList.

Runs every 15 minutes from GitHub Actions (.github/workflows/likers.yml).

How it works
  1. Reads the NEW like / reply / mention notifications since the last run
     (needs the ANILIST_TOKEN secret; notifications are NOT marked as read).
  2. Gives points to whoever sent them:
       - like                      10 points
       - reply, mention, message   25 points (no combo)
       - combo: likes from the same person less than 5 minutes apart build a streak
             like #1-4 of the streak  x1    (10 pts)
             like #5-9                x1.5  (15 pts)
             like #10-24              x2    (20 pts)
             like #25-49              x2.5  (25 pts)
             like #50+                x3    (30 pts, cap)
  3. Builds 4 leaderboards (all-time, last 30 days, last 7 days, last 24 hours).
     People ELIMINATED on Hajime's kill list stay ranked but get a skull.
  4. Reads the visitor count from your own Cloudflare Worker (VISITOR_COUNTER_URL).
  5. Draws the profile images in likers/img/ and writes likers/data.json for the
     full-list page (likers/index.html).

Files
  data/likers_state.json   per-user totals, the last 35 days of events, last notification read
  likers/data.json         what the full-list page shows
  likers/img/*.png         the images used in the AniList bio
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER_ID = 5928981                       # ThePhantasmAl
API = "https://graphql.anilist.co"
TOKEN = os.environ.get("ANILIST_TOKEN", "").strip()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _counter_url():
    """Worker address: VISITOR_COUNTER_URL variable, else the first line of data/visitor_counter_url.txt."""
    url = os.environ.get("VISITOR_COUNTER_URL", "").strip()
    if not url:
        try:
            with open(os.path.join(ROOT, "data", "visitor_counter_url.txt"), encoding="utf-8") as f:
                url = f.readline().strip()
        except OSError:
            url = ""
    if url and not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/").removesuffix("/hit.gif").removesuffix("/stats")


COUNTER_URL = _counter_url()
OUT_ROOT = os.environ.get("LIKERS_OUT", ROOT)          # tests write somewhere else
STATE = os.path.join(OUT_ROOT, "data", "likers_state.json")
DATA_OUT = os.path.join(OUT_ROOT, "likers", "data.json")
IMG_DIR = os.path.join(OUT_ROOT, "likers", "img")
KILL_LIST = os.path.join(ROOT, "hajime", "kill_list.json")
FONT_DIR = os.path.join(ROOT, "tools", "fonts")

LIKE_POINTS = 10
REPLY_POINTS = 25
COMBO_GAP = 300                          # seconds between two likes to keep a streak alive
COMBO_STEPS = [(50, 3.0), (25, 2.5), (10, 2.0), (5, 1.5), (1, 1.0)]
KEEP_EVENTS_DAYS = 35                    # older events are only kept in the all-time totals
MAX_NOTIF_PAGES = 100                    # AniList stops at 5,000 entries (100 pages of 50)
PACE = 2.2                               # seconds between two AniList requests (stays under 30/min)
RUN_BUDGET = 10 * 60                     # seconds a run may spend reading history; the rest waits for the next run
TOTAL_SCAN_HOURS = 6                     # how often the lifetime "likes received" is recounted
MAX_ACTIVITY_PAGES = 100                 # AniList stops at 5,000 entries (100 pages of 50)

LIKE_TYPES = {"ActivityLikeNotification", "ActivityReplyLikeNotification",
              "ThreadLikeNotification", "ThreadCommentLikeNotification"}
REPLY_TYPES = {"ActivityReplyNotification", "ActivityMentionNotification",
               "ActivityMessageNotification", "ThreadCommentReplyNotification",
               "ThreadCommentMentionNotification"}
NOTIF_ENUMS = ["ACTIVITY_LIKE", "ACTIVITY_REPLY_LIKE", "THREAD_LIKE", "THREAD_COMMENT_LIKE",
               "ACTIVITY_REPLY", "ACTIVITY_MENTION", "ACTIVITY_MESSAGE",
               "THREAD_COMMENT_REPLY", "THREAD_COMMENT_MENTION"]

_frag = "\n".join(f"      ... on {t} {{ id userId createdAt user {{ name avatar {{ medium }} }} }}"
                  for t in sorted(LIKE_TYPES | REPLY_TYPES))
NOTIF_Q = f"""
query ($page: Int, $types: [NotificationType]) {{
  Page(page: $page, perPage: 50) {{
    pageInfo {{ hasNextPage }}
    notifications(type_in: $types, resetNotificationCount: false) {{
      __typename
{_frag}
    }}
  }}
}}"""
ACTIVITY_Q = """
query ($id: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    activities(userId: $id, sort: ID_DESC) {
      ... on TextActivity { likeCount }
      ... on ListActivity { likeCount }
      ... on MessageActivity { likeCount }
    }
  }
}"""


# ------------------------------------------------------------------ helpers

def now_ts():
    override = os.environ.get("LIKERS_NOW")              # tests only
    return int(override) if override else int(time.time())


_last_call = [0.0]
STARTED = time.time()


def out_of_time():
    return time.time() - STARTED > RUN_BUDGET


def gql(query, variables, auth=False, tries=6):
    """One AniList request, spaced PACE seconds from the previous one.
    429 (too many requests): waits what AniList asks (Retry-After, usually 60 s) and retries."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "ThePhantasmAl-likers/1.1"}
    if auth:
        headers["Authorization"] = "Bearer " + TOKEN
    for attempt in range(tries):
        gap = PACE - (time.time() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        _last_call[0] = time.time()
        try:
            req = urllib.request.Request(API, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if data.get("errors"):
                raise RuntimeError(data["errors"])
            return data["data"]
        except urllib.error.HTTPError as e:
            if e.code == 400:
                raise RuntimeError(f"HTTP 400: {e.read()[:300]!r}")
            wait = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            wait = max(wait, 61 if e.code == 429 else 5 * (attempt + 1))
            print(f"HTTP {e.code}, waiting {wait}s (retry {attempt + 1}/{tries - 1})")
            time.sleep(wait)
        except RuntimeError:
            raise
        except Exception as e:
            print(f"error {e}, retry {attempt + 1}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("AniList unreachable")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False, separators=(",", ":") if indent is None else None)
    os.replace(tmp, path)


def combo_mult(streak):
    for need, mult in COMBO_STEPS:
        if streak >= need:
            return mult
    return 1.0


def fmt(n):
    return f"{int(n):,}"


def fmt_mult(m):
    return f"×{m:g}"


# ------------------------------------------------------------------ data

def new_state():
    return {"last_notif_id": 0, "users": {}, "events": [], "visitors": {"total": None, "today": None},
            "lifetime_likes": {"value": None, "at": 0},
            "history": {"done": False, "page": 1, "top_id": None, "people": {}, "items": []}}


def read_history(state):
    """First time only: read the whole notification history, a slice per run.
    Everything is kept aside until the end, then scored oldest first so old combos count.
    Returns True once the history is complete."""
    h = state["history"]
    if h["done"]:
        return True
    seen = {it[0] for it in h["items"]}
    page = h["page"]
    try:
        while page <= MAX_NOTIF_PAGES:
            if out_of_time():
                print(f"History: paused at page {page} ({len(h['items'])} notifications so far), "
                      f"continues next run")
                break
            d = gql(NOTIF_Q, {"page": page, "types": NOTIF_ENUMS}, auth=True)["Page"]
            for n in d["notifications"]:
                if not n or "id" not in n:
                    continue
                if h["top_id"] is None:
                    h["top_id"] = n["id"]                 # newest one when the history read began
                if n["id"] > h["top_id"] or n["id"] in seen:
                    continue                              # newer ones are read normally afterwards
                seen.add(n["id"])
                uid = n.get("userId")
                info = n.get("user") or {}
                h["people"][str(uid)] = [info.get("name"), (info.get("avatar") or {}).get("medium")]
                h["items"].append([n["id"], n["__typename"], uid, n.get("createdAt")])
            if page % 20 == 0:
                print(f"History: page {page}, {len(h['items'])} notifications")
            if not d["pageInfo"]["hasNextPage"]:
                h["done"] = True
                break
            page += 1
    except RuntimeError as e:
        if "Page depth" in str(e):
            # AniList never pages past 5,000 entries: that is as far back as the history goes
            print(f"History: reached AniList's 5,000-entry limit at page {page}; that's all it can give")
            h["done"] = True
            page += 1
        else:
            print(f"History: stopped at page {page} ({e}); continues next run")
    # new notifications push older ones down, so the next run restarts one page earlier (duplicates are skipped)
    h["page"] = max(1, page - 1)
    if page > MAX_NOTIF_PAGES:
        h["done"] = True
    if not h["done"]:
        return False
    notifs = [{"id": i, "__typename": t, "userId": u, "createdAt": ts,
               "user": {"name": (h["people"].get(str(u)) or [None])[0],
                        "avatar": {"medium": (h["people"].get(str(u)) or [None, None])[1]}}}
              for i, t, u, ts in h["items"]]
    added = ingest(state, notifs)
    state["last_notif_id"] = max(state["last_notif_id"], h["top_id"] or 0)
    print(f"History complete: {len(notifs)} notifications, {added} scored")
    state["history"] = {"done": True}
    return True


def fetch_new_notifications(last_id):
    found, page = [], 1
    while page <= MAX_NOTIF_PAGES:
        d = gql(NOTIF_Q, {"page": page, "types": NOTIF_ENUMS}, auth=True)["Page"]
        reached_old = False
        for n in d["notifications"]:
            if not n or "id" not in n:
                continue
            if n["id"] <= last_id:
                reached_old = True
                break
            found.append(n)
        if reached_old or not d["pageInfo"]["hasNextPage"]:
            break
        page += 1
    return found


def ingest(state, notifs):
    """Score new notifications. Points are decided when a notification arrives, so the
    combo a like belonged to never changes afterwards."""
    users = state["users"]
    added = 0
    for n in sorted(notifs, key=lambda x: (x.get("createdAt") or 0, x["id"])):
        state["last_notif_id"] = max(state["last_notif_id"], n["id"])
        uid = n.get("userId")
        kind = n.get("__typename")
        if not uid or uid == USER_ID or kind not in LIKE_TYPES | REPLY_TYPES:
            continue
        ts = int(n.get("createdAt") or now_ts())
        info = n.get("user") or {}
        u = users.setdefault(str(uid), {"name": "?", "avatar": None, "pts": 0, "likes": 0,
                                        "replies": 0, "best": 1.0, "last_like": 0, "streak": 0})
        u["name"] = info.get("name") or u["name"]
        u["avatar"] = (info.get("avatar") or {}).get("medium") or u["avatar"]
        if kind in LIKE_TYPES:
            if ts >= u["last_like"]:
                u["streak"] = u["streak"] + 1 if ts - u["last_like"] <= COMBO_GAP else 1
                u["last_like"] = ts
                streak = u["streak"]
            else:                                         # arrived late: no combo
                streak = 1
            mult = combo_mult(streak)
            pts = round(LIKE_POINTS * mult)
            u["likes"] += 1
            u["best"] = max(u["best"], mult)
            state["events"].append([n["id"], uid, ts, "L", pts, mult])
        else:
            pts = REPLY_POINTS
            u["replies"] += 1
            state["events"].append([n["id"], uid, ts, "R", pts, 1.0])
        u["pts"] += pts
        added += 1
    cutoff = now_ts() - KEEP_EVENTS_DAYS * 86400
    state["events"] = [e for e in state["events"] if e[2] >= cutoff]
    return added


def lifetime_likes(state):
    """Sum of likeCount on all my activities, recounted every TOTAL_SCAN_HOURS."""
    info = state["lifetime_likes"]
    scan = info.get("scan")                               # a recount in progress: {page, total}
    if scan is None:
        if info["value"] is not None and now_ts() - info["at"] < TOTAL_SCAN_HOURS * 3600:
            return info["value"]
        scan = {"page": 1, "total": 0}
    try:
        while scan["page"] <= MAX_ACTIVITY_PAGES:
            if out_of_time():
                info["scan"] = scan
                print(f"Lifetime likes: paused at page {scan['page']}, continues next run")
                return info["value"]
            d = gql(ACTIVITY_Q, {"id": USER_ID, "page": scan["page"]}, auth=bool(TOKEN))["Page"]
            scan["total"] += sum((a or {}).get("likeCount") or 0 for a in d["activities"])
            if not d["pageInfo"]["hasNextPage"]:
                break
            scan["page"] += 1
        state["lifetime_likes"] = {"value": scan["total"], "at": now_ts()}
        print(f"Lifetime likes on activities: {scan['total']} ({scan['page']} page(s))")
    except Exception as e:
        if "Page depth" in str(e):
            # AniList never pages past 5,000 activities: count what it can give
            state["lifetime_likes"] = {"value": scan["total"], "at": now_ts()}
            print(f"Lifetime likes on your newest 5,000 activities: {scan['total']}")
        else:
            info["scan"] = scan
            print(f"Lifetime likes: stopped at page {scan['page']} ({e}); continues next run")
    return state["lifetime_likes"]["value"]


def read_visitors(state):
    if not COUNTER_URL:
        print("VISITOR_COUNTER_URL not set: visitor count not updated")
        return state["visitors"]
    try:
        req = urllib.request.Request(COUNTER_URL + "/stats",
                                     headers={"User-Agent": "ThePhantasmAl-likers/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        state["visitors"] = {"total": int(d["total"]), "today": int(d.get("today", 0))}
    except Exception as e:
        print(f"Visitor counter unreachable ({e}); keeping the last value")
    return state["visitors"]


def killed_ids():
    data = load(KILL_LIST, {})
    if "blacklist" in data:
        return {str(i) for i in data["blacklist"]}
    return {str(e["id"]) for e in data.get("events", []) if e.get("status") == "eliminated"}


def boards(state):
    users = state["users"]
    dead = killed_ids()
    now = now_ts()

    def entry(uid, pts, likes, replies, best):
        u = users.get(str(uid), {})
        return {"id": int(uid), "name": u.get("name", "?"), "avatar": u.get("avatar"),
                "pts": pts, "likes": likes, "replies": replies, "best": best,
                "elim": str(uid) in dead}

    def ranked(rows):
        rows = [r for r in rows if r["pts"] > 0]
        rows.sort(key=lambda r: (-r["pts"], -r["likes"], r["name"].lower()))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return rows

    out = {"alltime": ranked([entry(k, u["pts"], u["likes"], u["replies"], u["best"])
                              for k, u in users.items()])}
    for name, secs in (("month", 30 * 86400), ("week", 7 * 86400), ("day", 86400)):
        agg = {}
        for _, uid, ts, kind, pts, mult in state["events"]:
            if ts < now - secs:
                continue
            a = agg.setdefault(uid, [0, 0, 0, 1.0])
            a[0] += pts
            if kind == "L":
                a[1] += 1
                a[3] = max(a[3], mult)
            else:
                a[2] += 1
        out[name] = ranked([entry(uid, *a) for uid, a in agg.items()])
    return out


# ------------------------------------------------------------------ drawing

S = 2                                    # images are drawn at 2x for sharp text
C_PANEL = (21, 31, 46, 255)
C_PANEL2 = (27, 39, 56, 255)
C_PANEL_TOP = (31, 42, 64, 255)
C_BORDER = (88, 46, 128, 255)
C_LINE = (28, 41, 64, 255)
C_LINE2 = (34, 49, 74, 255)
C_TEXT = (255, 255, 255, 255)
C_SOFT = (169, 184, 204, 255)
C_MUTED = (139, 160, 184, 255)
C_ACCENT = (208, 139, 255, 255)
C_ACCENT_DARK = (26, 15, 38, 255)
C_TITLE = (199, 125, 255, 255)
C_GOLD = (245, 196, 81, 255)
C_SILVER = (199, 210, 222, 255)
C_BRONZE = (216, 146, 90, 255)
C_DEAD = (110, 122, 140, 255)

FONT_FILES = {
    "display": ("ChakraPetch-Bold.ttf", "chakrapetch/ChakraPetch-Bold.ttf"),
    "display-semi": ("ChakraPetch-SemiBold.ttf", "chakrapetch/ChakraPetch-SemiBold.ttf"),
    "body": ("Overpass.ttf", "overpass/Overpass%5Bwght%5D.ttf"),
}
_fonts = {}
_avatars = {}


def font(kind, size, weight=700):
    from PIL import ImageFont
    key = (kind, size, weight)
    if key in _fonts:
        return _fonts[key]
    fname, remote = FONT_FILES[kind]
    path = os.path.join(FONT_DIR, fname)
    if not os.path.exists(path):
        try:
            os.makedirs(FONT_DIR, exist_ok=True)
            url = "https://raw.githubusercontent.com/google/fonts/main/ofl/" + remote
            with urllib.request.urlopen(url, timeout=30) as r, open(path, "wb") as f:
                f.write(r.read())
        except Exception as e:
            print(f"Font download failed ({e}); using a fallback font")
    try:
        f = ImageFont.truetype(path, size * S)
        if kind == "body":
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
    except Exception:
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf", size * S)
        except Exception:
            f = ImageFont.load_default()
    _fonts[key] = f
    return f


class Canvas:
    def __init__(self, w, h):
        from PIL import Image, ImageDraw
        self.img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x, y, w, h, fill, radius=0, outline=None, width=1):
        self.d.rounded_rectangle([x * S, y * S, (x + w) * S - 1, (y + h) * S - 1], radius=radius * S,
                                 fill=fill, outline=outline, width=width * S)

    def line(self, x1, y1, x2, y2, fill, width=1):
        self.d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=fill, width=width * S)

    def width(self, txt, f):
        return self.d.textlength(txt, font=f) / S

    def fit(self, txt, f, maxw):
        if self.width(txt, f) <= maxw:
            return txt
        while txt and self.width(txt + "…", f) > maxw:
            txt = txt[:-1]
        return txt + "…"

    def text(self, x, y, txt, f, fill, anchor="la", maxw=None):
        """anchor: first letter l/m/r (left/middle/right), second a/m (top/middle)."""
        if maxw:
            txt = self.fit(txt, f, maxw)
        a = anchor[0] + ("t" if anchor[1] == "a" else "m")
        self.d.text((x * S, y * S), txt, font=f, fill=fill, anchor=a)
        return self.width(txt, f)

    def paste(self, im, x, y):
        self.img.alpha_composite(im, (int(x * S), int(y * S)))

    def save(self, name):
        os.makedirs(IMG_DIR, exist_ok=True)
        self.img.save(os.path.join(IMG_DIR, name), optimize=True)


def avatar(url, name, size, ring):
    """Round avatar with a colored ring; falls back to the first letter of the name."""
    from PIL import Image, ImageDraw
    px = size * S
    key = (url, size)
    base = _avatars.get(key)
    if base is None:
        base = Image.new("RGBA", (px, px), (42, 58, 82, 255))
        if url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ThePhantasmAl-likers/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    pic = Image.open(io.BytesIO(r.read())).convert("RGBA")
                side = min(pic.size)
                pic = pic.crop(((pic.width - side) // 2, (pic.height - side) // 2,
                                (pic.width + side) // 2, (pic.height + side) // 2))
                base = pic.resize((px, px), Image.LANCZOS)
                url = "ok"
            except Exception:
                url = None
        if url != "ok":
            dd = ImageDraw.Draw(base)
            dd.text((px / 2, px / 2), (name[:1] or "?").upper(), font=font("body", int(size * 0.42), 800),
                    fill=C_TEXT, anchor="mm")
        _avatars[key] = base
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    out.paste(base, (0, 0), mask)
    ImageDraw.Draw(out).ellipse([0, 0, px - 1, px - 1], outline=ring, width=2 * S)
    return out


def skull(c, x, y, s=12):
    """Small skull for people ELIMINATED on the kill list (top-left corner at x, y)."""
    col, dark = (230, 230, 236, 255), (21, 31, 46, 255)
    c.d.ellipse([x * S, y * S, (x + s) * S, (y + s * 0.85) * S], fill=col)
    c.d.rectangle([(x + s * 0.22) * S, (y + s * 0.6) * S, (x + s * 0.78) * S, (y + s) * S], fill=col)
    for ex in (0.3, 0.7):
        cx, cy, r = x + s * ex, y + s * 0.45, s * 0.14
        c.d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=dark)
    for tx in (0.42, 0.58):
        c.line(x + s * tx, y + s * 0.82, x + s * tx, y + s, dark, 1)


def eye_icon(c, x, y, s, col):
    c.d.ellipse([(x + 1) * S, (y + s * 0.25) * S, (x + s - 1) * S, (y + s * 0.75) * S], outline=col, width=2 * S)
    r = s * 0.14
    c.d.ellipse([(x + s / 2 - r) * S, (y + s / 2 - r) * S, (x + s / 2 + r) * S, (y + s / 2 + r) * S],
                outline=col, width=2 * S)


def heart_icon(c, x, y, s, col):
    import math
    pts = []
    for i in range(64):
        t = 2 * math.pi * i / 64
        hx = 16 * math.sin(t) ** 3
        hy = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append(((x + s / 2 + hx * s / 36) * S, (y + s * 0.45 - hy * s / 36) * S))
    c.d.polygon(pts, outline=col, width=2 * S)


def panel(c, x, y, w, h):
    c.rect(x, y, w, h, C_PANEL, radius=12, outline=C_BORDER, width=1)


def name_with_skull(c, x, y, row, f, maxw, color=C_TEXT, anchor="la"):
    """Draws a name (muted + skull when eliminated). y is the vertical middle."""
    if not row["elim"]:
        return c.text(x, y, row["name"], f, color, anchor[0] + "m", maxw)
    name = c.fit(row["name"], f, maxw - 16)
    w = c.width(name, f) + 16
    left = x - w / 2 if anchor[0] == "m" else x
    c.text(left, y, name, f, C_DEAD, "lm")
    skull(c, left + w - 12, y - 6, 12)
    return w


def draw_header(st):
    W, H = 700, 168
    c = Canvas(W, H)
    c.text(W / 2, 4, "HALL OF LIKES", font("display", 24), C_TITLE, "ma")
    c.text(W / 2, 36, "Updated every 15 min", font("body", 11, 600), C_MUTED, "ma")
    tiles = [("TOTAL VISITORS", st["visitors"]["total"], st["visitors"]["today"], eye_icon),
             ("LIKES RECEIVED", st["likes_total"], st["likes_today"], heart_icon)]
    tw = (W - 14) / 2
    for i, (label, total, today, icon) in enumerate(tiles):
        x, y, h = i * (tw + 14), 58, 108
        panel(c, x, y, tw, h)
        c.rect(x + 22, y + 30, 48, 48, (63, 34, 92, 255), radius=12)
        icon(c, x + 33, y + 41, 26, C_ACCENT)
        c.text(x + 86, y + 22, label, font("body", 12, 700), C_MUTED)
        c.text(x + 86, y + 38, "—" if total is None else fmt(total), font("display", 38), C_TEXT)
        if today is not None:
            c.text(x + 86, y + 84, f"+{fmt(today)} today", font("body", 12, 700), C_ACCENT)
    c.save("header.png")


def draw_alltime(rows):
    W = 700
    rest = rows[3:10]
    H = 52 + 220 + 18 + 26 + max(len(rest), 1) * 34 + 18
    c = Canvas(W, H)
    panel(c, 0, 0, W, H)
    c.text(20, 26, "ALL-TIME TOP LIKERS", font("display", 17), C_TEXT, "lm")
    c.text(W - 20, 26, "Full list →", font("body", 13, 700), C_ACCENT, "rm")

    gap, pw = 12, (W - 40 - 24) / 3
    bottom = 52 + 220
    spots = [(0, 1, C_SILVER, 190, 52), (1, 0, C_GOLD, 220, 64), (2, 2, C_BRONZE, 180, 48)]
    for col, idx, ring, h, av in spots:
        x = 20 + col * (pw + gap)
        y = bottom - h
        c.rect(x, y, pw, h, C_PANEL_TOP if idx == 0 else C_PANEL2, radius=10)
        c.rect(x, y, pw, 4, ring, radius=2)
        cx = x + pw / 2
        if idx >= len(rows):
            c.text(cx, y + h / 2, "Your spot?", font("body", 13, 700), C_MUTED, "mm")
            continue
        r = rows[idx]
        ty = y + 16
        if idx == 0:
            c.text(cx, ty, "TOP LIKER", font("body", 11, 800), C_GOLD, "ma")
            ty += 18
        c.paste(avatar(r["avatar"], r["name"], av, ring), cx - av / 2, ty)
        ty += av + 6
        c.text(cx, ty, f"#{idx + 1}", font("display", 13), ring, "ma")
        ty += 24
        name_with_skull(c, cx, ty, r, font("body", 15 if idx == 0 else 14, 700), pw - 16, anchor="mm")
        ty += 12
        c.text(cx, ty, fmt(r["pts"]), font("display", 26 if idx == 0 else 22),
               C_ACCENT if idx == 0 else C_TEXT, "ma")
        ty += 34 if idx == 0 else 30
        c.text(cx, ty, f"{fmt(r['likes'])} likes  ·  best {fmt_mult(r['best'])}", font("body", 11, 600),
               C_MUTED, "ma", maxw=pw - 12)

    y = bottom + 18
    cols = [(30, "#", "l"), (66, "USER", "l"), (440, "LIKES", "r"), (530, "BEST", "r"), (680, "POINTS", "r")]
    for x, label, al in cols:
        c.text(x, y + 8, label, font("body", 11, 700), C_MUTED, al + "m")
    c.line(20, y + 20, W - 20, y + 20, C_LINE2)
    y += 26
    if not rest:
        c.text(W / 2, y + 17, "Ranks 4-10 are still open. Like a post to get in!", font("body", 13, 600),
               C_MUTED, "mm")
    for r in rest:
        my = y + 17
        c.text(30, my, str(r["rank"]), font("display", 14), C_MUTED, "lm")
        name_with_skull(c, 66, my, r, font("body", 14, 600), 300)
        c.text(440, my, fmt(r["likes"]), font("body", 14, 400), C_SOFT, "rm")
        c.text(530, my, fmt_mult(r["best"]), font("body", 14, 700), C_ACCENT, "rm")
        c.text(680, my, fmt(r["pts"]), font("display", 14), C_TEXT, "rm")
        c.line(20, y + 34, W - 20, y + 34, C_LINE)
        y += 34
    c.save("alltime.png")


def draw_period(fname, title, rows, show_combo):
    W, H = 228, 372
    c = Canvas(W, H)
    panel(c, 0, 0, W, H)
    c.text(W / 2, 22, title, font("display", 14), C_TEXT, "mm")
    y = 40
    top = rows[:10]
    if not top:
        c.text(W / 2, 170, "Nobody yet...", font("body", 13, 600), C_MUTED, "mm")
        c.text(W / 2, 192, "be the first!", font("body", 13, 600), C_MUTED, "mm")
    for r in top:
        my = y + 14
        c.text(14, my, str(r["rank"]), font("display", 13), C_MUTED, "lm")
        pts_w = c.width(fmt(r["pts"]), font("display", 13))
        right = W - 14 - pts_w - 6
        c.text(W - 14, my, fmt(r["pts"]), font("display", 13), C_TEXT, "rm")
        if show_combo and r["best"] >= 1.5:
            label = fmt_mult(r["best"])
            bw = c.width(label, font("body", 10, 800)) + 8
            c.rect(right - bw, my - 8, bw, 16, C_ACCENT, radius=4)
            c.text(right - bw / 2, my + 1, label, font("body", 10, 800), C_ACCENT_DARK, "mm")
            right -= bw + 6
        name_with_skull(c, 36, my, r, font("body", 13, 600), right - 36)
        c.line(12, y + 28, W - 12, y + 28, C_LINE)
        y += 28
    c.text(W / 2, H - 22, "Full list →", font("body", 12, 700), C_ACCENT, "mm")
    c.save(fname)


def draw_legend():
    W, H = 700, 72
    c = Canvas(W, H)
    c.rect(0, 0, W, H, C_PANEL, radius=12, outline=C_LINE2)
    c.text(16, 20, "HOW POINTS WORK  ·  likes less than 5 min apart build a combo  ·  replies & mentions 25 pts",
           font("body", 11, 700), C_MUTED, "lm", maxw=W - 32)
    chips = [("10 pts per like", (34, 49, 74, 255), C_TEXT), ("Combo 5+ → ×1.5", (42, 33, 64, 255), C_ACCENT),
             ("10+ → ×2", (51, 37, 79, 255), C_ACCENT), ("25+ → ×2.5", (61, 41, 96, 255), C_ACCENT),
             ("50+ → ×3", (74, 45, 117, 255), C_TEXT)]
    x = 16
    f = font("body", 12, 700)
    for label, bg, fg in chips:
        w = c.width(label, f) + 18
        c.rect(x, 36, w, 24, bg, radius=6)
        c.text(x + w / 2, 49, label, f, fg, "mm")
        x += w + 8
    c.save("legend.png")


# ------------------------------------------------------------------ main

def main():
    state = load(STATE, None) or new_state()
    if "history" not in state and state.get("last_notif_id", 0) > 0:
        state["history"] = {"done": True}                 # saved by the first version, history already read
    for k, v in new_state().items():
        state.setdefault(k, v)

    if TOKEN:
        if read_history(state):
            try:
                notifs = fetch_new_notifications(state["last_notif_id"])
                added = ingest(state, notifs)
                print(f"{len(notifs)} new notification(s), {added} scored")
            except RuntimeError as e:
                print(f"Could not read new notifications ({e}); trying again next run")
        save(STATE, state)                                # keep progress even if drawing fails
    else:
        print("No ANILIST_TOKEN: notifications not read (images redrawn from saved data)")

    visitors = read_visitors(state)
    tracked = sum(u["likes"] for u in state["users"].values())
    life = lifetime_likes(state)
    likes_total = max(life or 0, tracked)
    likes_today = sum(1 for e in state["events"] if e[3] == "L" and e[2] >= now_ts() - 86400)
    b = boards(state)

    save(STATE, state)
    data = {"updated": datetime.fromtimestamp(now_ts(), timezone.utc).isoformat(),
            "visitors": visitors, "likes": {"total": likes_total, "today": likes_today},
            "rules": {"like": LIKE_POINTS, "reply": REPLY_POINTS, "combo_gap_s": COMBO_GAP,
                      "combo": [[n, m] for n, m in reversed(COMBO_STEPS)]},
            "boards": b}
    save(DATA_OUT, data, indent=1)

    st = {"visitors": visitors, "likes_total": likes_total, "likes_today": likes_today}
    draw_header(st)
    draw_alltime(b["alltime"])
    draw_period("month.png", "LAST 30 DAYS", b["month"], False)
    draw_period("week.png", "LAST 7 DAYS", b["week"], True)
    draw_period("day.png", "LAST 24 HOURS", b["day"], True)
    draw_legend()
    print(f"Leaderboards: {len(b['alltime'])} liker(s) all-time, {len(b['day'])} in the last 24 h")


if __name__ == "__main__":
    main()
