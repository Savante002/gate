"""Hajime's kill list: hunts the people who unfollow ThePhantasmAl on AniList.

Runs every 15 minutes from GitHub Actions (.github/workflows/killlist.yml).

How it works
  1. Reads the public follower list and compares it with the last scan.
  2. Someone who disappeared is checked first:
       - account deleted/banned  -> GHOST (no points, never hunted)
       - otherwise               -> WANTED: a bounty and a cooldown that depend on how long
                                    they had followed (15 min for brand-new up to 24 h max).
     Repeat offenders (unfollowed before) get REPEAT OFFENDER xN and the bounty doubles each time.
  3. Follows again before the cooldown ends -> BEGGED FOR MERCY (-1000 points).
  4. Cooldown over -> ELIMINATED: your account unfollows them for real (+1000 points).
     Needs the ANILIST_TOKEN secret; without it nobody is unfollowed (they wait).
  5. Eliminations are permanent: if your account follows an eliminated person again
     (by hand or by a bot), the next run unfollows them again.
     To forgive someone, add their username (or id) to data/pardons.txt.
  6. Optional notifications: DISCORD_WEBHOOK and/or NTFY_TOPIC secrets.

Safety
  - ToggleFollow is only sent when you DO follow that person (checked right before),
    otherwise it would follow them instead.
  - A drop of more than 40% of followers (20+ followers) is treated as an AniList glitch.
  - Reads stay far below AniList's rate limit (1 s between requests in loops).
  - The token only comes from the GitHub secret; it is never written in this public repo.

Files
  data/followers_state.json   followers {id: {name, avatar, since}}, score, offenses
  data/pardons.txt            usernames or ids you forgave (optional, one per line)
  hajime/kill_list.json       what the page shows (events, score, history, blacklist)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

USER_ID = 5928981                       # ThePhantasmAl (the id never changes, even after a rename)
API = "https://graphql.anilist.co"
PAGE_URL = "https://savante002.github.io/gate/hajime/"
TOKEN = os.environ.get("ANILIST_TOKEN", "").strip()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "followers_state.json")
PARDONS = os.path.join(ROOT, "data", "pardons.txt")
OUT = os.path.join(ROOT, "hajime", "kill_list.json")
KEEP_DAYS = 400                         # list entries older than this are dropped (score is kept)
HISTORY_DAYS = 400
POINTS = 1000
BOUNTY_PER_DAY = 1000                   # ¥ per day of following
VETERAN_BOUNTY = 500_000                # people already following before the first scan
HEARTBEAT_HOURS = 24                    # save at least once a day so GitHub keeps the schedule alive

FOLLOWERS_Q = """
query ($id: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    followers(userId: $id, sort: ID) { id name avatar { medium } }
  }
}"""
FOLLOWING_Q = """
query ($id: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    following(userId: $id, sort: ID) { id name }
  }
}"""
EXISTS_Q = "query ($id: Int) { User(id: $id) { id name } }"
IS_FOLLOWING_Q = "query ($id: Int) { User(id: $id) { id name isFollowing } }"
TOGGLE_M = "mutation ($id: Int) { ToggleFollow(userId: $id) { id isFollowing } }"


class NotFound(Exception):
    """AniList says this user does not exist (deleted or banned account)."""


def now_utc():
    override = os.environ.get("KILLLIST_NOW")          # used by the tests only
    if override:
        return datetime.fromisoformat(override)
    return datetime.now(timezone.utc).replace(microsecond=0)


def gql(query, variables, auth=False, tries=4):
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "ThePhantasmAl-killlist/3.0"}
    if auth:
        headers["Authorization"] = "Bearer " + TOKEN
    for attempt in range(tries):
        try:
            req = urllib.request.Request(API, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if data.get("errors"):
                raise RuntimeError(data["errors"])
            return data["data"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound()
            wait = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            print(f"HTTP {e.code}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(max(wait, 5 * (attempt + 1)))
        except (NotFound, RuntimeError):
            raise
        except Exception as e:
            print(f"error {e}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("AniList unreachable")


def fetch_list(query):
    people, page = {}, 1
    while True:
        d = gql(query, {"id": USER_ID, "page": page})["Page"]
        key = "followers" if "followers" in d else "following"
        for u in d[key]:
            people[str(u["id"])] = {"name": u["name"], "avatar": (u.get("avatar") or {}).get("medium")}
        if not d["pageInfo"]["hasNextPage"]:
            return people
        page += 1
        time.sleep(1.0)


def fetch_followers():
    return fetch_list(FOLLOWERS_Q)


def fetch_following():
    return fetch_list(FOLLOWING_Q)


def account_exists(uid):
    """True / False (deleted or banned) / None (could not check this time)."""
    try:
        gql(EXISTS_Q, {"id": int(uid)})
        return True
    except NotFound:
        return False
    except RuntimeError:
        return None


def i_follow(uid):
    return bool(gql(IS_FOLLOWING_Q, {"id": int(uid)}, auth=True)["User"]["isFollowing"])


def unfollow(uid):
    """Unfollow uid, but only if you currently follow them (ToggleFollow is a toggle).
    Returns 'unfollowed', 'not-following', 'ghost' or 'failed'."""
    try:
        if not i_follow(uid):
            return "not-following"
        res = gql(TOGGLE_M, {"id": int(uid)}, auth=True)["ToggleFollow"]
    except NotFound:
        return "ghost"
    return "unfollowed" if not res["isFollowing"] else "failed"


def notify(title, text, tag="dart"):
    """Send a notification to Discord and/or ntfy (both optional, failures are ignored)."""
    if DISCORD_WEBHOOK:
        try:
            body = json.dumps({"username": "Hajime", "content": f"**{title}**\n{text}\n<{PAGE_URL}>"}).encode()
            req = urllib.request.Request(DISCORD_WEBHOOK, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "ThePhantasmAl-killlist/3.0"})
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            print(f"discord notification failed: {e}", file=sys.stderr)
    if NTFY_TOPIC:
        try:
            req = urllib.request.Request("https://ntfy.sh/" + urllib.parse.quote(NTFY_TOPIC),
                                         data=text.encode(), headers={
                                             "Title": title.encode("ascii", "ignore").decode().strip() or "Kill list",
                                             "Tags": tag, "Click": PAGE_URL})
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            print(f"ntfy notification failed: {e}", file=sys.stderr)


def cooldown_minutes(days):
    if days is None:                     # veteran: followed before tracking started
        return 24 * 60
    if days < 1:
        return 15
    if days < 7:
        return 60
    if days < 30:
        return 4 * 60
    if days < 182:
        return 12 * 60
    return 24 * 60


def human_minutes(m):
    return f"{m} min" if m < 60 else f"{m // 60} h"


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_pardons():
    try:
        with open(PARDONS, encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


def migrate(state, events):
    """Older files had no 'since', 'status', score, offenses or history."""
    for info in state.get("followers", {}).values():
        info.setdefault("since", None)
    for e in events:
        if "status" not in e:
            e["status"] = "mercy" if e.get("spared") else "eliminated"
            if e.get("spared_at"):
                e["mercy_at"] = e["spared_at"]
        e.pop("spared", None)
        e.pop("spared_at", None)
        e.setdefault("offense", 1)
    state.setdefault("score", 0)
    if "offenses" not in state:
        counts = {}
        for e in events:
            if e["status"] != "ghost":
                counts[str(e["id"])] = counts.get(str(e["id"]), 0) + 1
        state["offenses"] = counts


def add_history(history, now, count):
    """One point per hour: [hour iso, followers]."""
    hour = now.replace(minute=0, second=0).isoformat()
    if history and history[-1][0] == hour:
        history[-1][1] = count
    else:
        history.append([hour, count])
    cutoff = (now - timedelta(days=HISTORY_DAYS)).isoformat()
    return [p for p in history if p[0] >= cutoff][-12000:]


def main():
    now = now_utc()
    iso = now.isoformat()
    try:
        current = fetch_followers()
    except (RuntimeError, NotFound) as e:
        raise SystemExit(f"AniList unreachable ({e}); nothing changed")

    state = load(STATE, None)
    out = load(OUT, {"events": []})
    events = out.get("events", [])
    history = out.get("history", [])
    pardons = load_pardons()
    log = []

    def pardoned(e):
        return str(e["id"]) in pardons or e["name"].lower() in pardons

    if state is None:
        # first scan: everyone already here is a veteran (follow date unknown)
        state = {"started": iso, "score": 0, "offenses": {},
                 "followers": {uid: dict(info, since=None) for uid, info in current.items()}}
        log.append(f"first scan: {len(current)} veterans registered")
    else:
        migrate(state, events)
        old = state["followers"]
        if len(old) >= 20 and len(current) < len(old) * 0.6:
            raise SystemExit(f"suspicious drop {len(old)} -> {len(current)}, skipping this run")

        # 1. people who disappeared: ghost or WANTED
        for uid, info in old.items():
            if uid in current:
                continue
            exists = account_exists(uid)
            time.sleep(1.0)
            if exists is None:                       # could not check: try again next run
                current[uid] = info
                log.append(f"could not check {info['name']}, retry next run")
                continue
            if exists is False:
                events.append({"id": int(uid), "name": info["name"], "avatar": info.get("avatar"),
                               "at": iso, "status": "ghost"})
                log.append(f"GHOST {info['name']} (account deleted)")
                continue
            since = info.get("since")
            days = None if since is None else (now - datetime.fromisoformat(since)).total_seconds() / 86400
            cd = cooldown_minutes(days)
            offense = state["offenses"].get(uid, 0) + 1
            state["offenses"][uid] = offense
            base = VETERAN_BOUNTY if days is None else max(BOUNTY_PER_DAY, round(days * BOUNTY_PER_DAY, -2))
            bounty = int(base * 2 ** (offense - 1))
            events.append({"id": int(uid), "name": info["name"], "avatar": info.get("avatar"),
                           "at": iso, "status": "wanted", "veteran": days is None,
                           "followed_days": None if days is None else round(days, 2),
                           "offense": offense, "bounty": bounty, "cooldown_min": cd,
                           "deadline": (now + timedelta(minutes=cd)).isoformat()})
            log.append(f"WANTED {info['name']} (cooldown {cd} min, bounty {bounty}, offense {offense})")
            repeat = f" REPEAT OFFENDER x{offense}." if offense > 1 else ""
            notify(f"🎯 WANTED: {info['name']}",
                   f"{info['name']} unfollowed you.{repeat} Bounty ¥{bounty:,}. "
                   f"Execution in {human_minutes(cd)} unless they follow again.", "dart")

        # 2. WANTED people: came back -> mercy; time is up -> eliminated
        for e in events:
            if e["status"] != "wanted":
                continue
            uid = str(e["id"])
            if uid in current:
                e["status"] = "mercy"
                e["mercy_at"] = iso
                state["score"] -= POINTS
                log.append(f"MERCY {e['name']}")
                notify(f"🙏 {e['name']} BEGGED FOR MERCY",
                       f"{e['name']} followed you again in time. Score: {state['score']:,}.", "pray")
            elif now >= datetime.fromisoformat(e["deadline"]):
                if not TOKEN:
                    log.append(f"{e['name']} is due, but no ANILIST_TOKEN secret: waiting")
                    continue
                try:
                    result = unfollow(uid)
                except RuntimeError as err:
                    log.append(f"could not unfollow {e['name']} ({err}), retry next run")
                    continue
                if result == "failed":
                    log.append(f"unfollow of {e['name']} did not stick, retry next run")
                    continue
                if result == "ghost":
                    e["status"] = "ghost"
                    e["ghost_at"] = iso
                    log.append(f"GHOST {e['name']} (deleted before execution)")
                    continue
                e["status"] = "eliminated"
                e["eliminated_at"] = iso
                e["unfollowed"] = result == "unfollowed"
                state["score"] += POINTS
                log.append(f"ELIMINATED {e['name']} ({result})")
                notify(f"🔫 ELIMINATED: {e['name']}",
                       f"{e['name']} is off your list for good. Score: {state['score']:,}.", "gun")
                time.sleep(1.0)

        # 3. remember followers; newcomers get a 'since' date
        for uid, info in list(current.items()):
            prev = old.get(uid)
            since = prev.get("since") if prev else iso
            current[uid] = dict(info, since=since)
        state["followers"] = current

    # 4. pardons + permanent blacklist
    for e in events:
        if e["status"] == "eliminated" and pardoned(e):
            e["status"] = "pardoned"
            e["pardoned_at"] = iso
            log.append(f"PARDONED {e['name']}")
    blacklist = sorted({e["id"] for e in events if e["status"] == "eliminated"})
    if TOKEN and blacklist and state.get("checked"):
        try:
            following = fetch_following()
        except (RuntimeError, NotFound) as err:
            following = {}
            log.append(f"could not read your followings ({err}), retry next run")
        for uid in [str(i) for i in blacklist if str(i) in following]:
            name = following[uid]["name"]
            try:
                result = unfollow(uid)
            except RuntimeError as err:
                log.append(f"could not re-unfollow {name} ({err})")
                continue
            if result == "unfollowed":
                log.append(f"RE-ELIMINATED {name} (you followed them again)")
                notify(f"🔫 {name} stays eliminated",
                       f"Your account followed {name} again, so the robot unfollowed them. "
                       f"Add them to data/pardons.txt to forgive.", "gun")
            time.sleep(1.0)

    cutoff = now - timedelta(days=KEEP_DAYS)
    events = [e for e in events if e["status"] == "wanted" or datetime.fromisoformat(e["at"]) >= cutoff][-1000:]
    history = add_history(history, now, len(current))

    new_out = {"score": state["score"], "followers": len(current), "hunting_since": state["started"],
               "blacklist": blacklist, "events": events, "history": history}
    strip_out = lambda d: {k: v for k, v in d.items() if k not in ("updated", "history")}
    changed = strip_out(out) != strip_out(new_out) or [p[1] for p in out.get("history", [])[-1:]] != [len(current)]
    stale = (now - datetime.fromisoformat(out.get("updated", "2000-01-01T00:00:00+00:00"))) > timedelta(hours=HEARTBEAT_HOURS)
    old_state = load(STATE, None)
    strip = lambda d: {k: v for k, v in d.items() if k != "checked"}
    state_changed = old_state is None or strip(old_state) != strip(state)
    saved = changed or stale or state_changed
    if saved:
        state["checked"] = iso
        save(STATE, state)
        save(OUT, dict(new_out, updated=iso))
    for line in log:
        print(line)
    print(f"{len(current)} followers, score {state['score']}, {len(events)} entries, saved={saved}")


if __name__ == "__main__":
    main()
