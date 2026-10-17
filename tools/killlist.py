"""Hajime's kill list: hunts the people who unfollow ThePhantasmAl on AniList.

Runs every 15 minutes from GitHub Actions (.github/workflows/killlist.yml).

How it works
  1. Reads the public follower list and compares it with the last scan.
  2. Someone who disappeared becomes WANTED: a bounty and a cooldown that depend on
     how long they had followed (15 min for brand-new followers up to 24 h max).
  3. If they follow again before the cooldown ends -> BEGGED FOR MERCY (-1000 points).
  4. When the cooldown ends -> ELIMINATED: your account unfollows them for real
     (+1000 points). Needs the ANILIST_TOKEN secret; without it nothing is unfollowed.

Safety
  - ToggleFollow is only sent when you DO follow that person (checked right before),
    otherwise it would follow them instead.
  - A sudden drop of more than 40% of followers is treated as an AniList glitch: no changes.
  - Reads stay far below AniList's rate limit (1 s between pages).

Files
  data/followers_state.json   followers seen {id: {name, avatar, since}} + score
  hajime/kill_list.json       what the page shows
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

USER_ID = 5928981                       # ThePhantasmAl (the id never changes, even after a rename)
API = "https://graphql.anilist.co"
TOKEN = os.environ.get("ANILIST_TOKEN", "").strip()   # GitHub secret, never written in this public repo
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "followers_state.json")
OUT = os.path.join(ROOT, "hajime", "kill_list.json")
KEEP_DAYS = 400                         # list entries older than this are dropped (score is kept)
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
IS_FOLLOWING_Q = "query ($id: Int) { User(id: $id) { id name isFollowing } }"
TOGGLE_M = "mutation ($id: Int) { ToggleFollow(userId: $id) { id isFollowing } }"


def now_utc():
    override = os.environ.get("KILLLIST_NOW")          # used by the tests only
    if override:
        return datetime.fromisoformat(override)
    return datetime.now(timezone.utc).replace(microsecond=0)


def gql(query, variables, auth=False, tries=4):
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "ThePhantasmAl-killlist/2.0"}
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
            wait = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            print(f"HTTP {e.code}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(max(wait, 5 * (attempt + 1)))
        except Exception as e:
            print(f"error {e}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("AniList unreachable")


def fetch_followers():
    people, page = {}, 1
    while True:
        d = gql(FOLLOWERS_Q, {"id": USER_ID, "page": page})["Page"]
        for u in d["followers"]:
            people[str(u["id"])] = {"name": u["name"], "avatar": (u.get("avatar") or {}).get("medium")}
        if not d["pageInfo"]["hasNextPage"]:
            return people
        page += 1
        time.sleep(1.0)


def i_follow(uid):
    return bool(gql(IS_FOLLOWING_Q, {"id": int(uid)}, auth=True)["User"]["isFollowing"])


def unfollow(uid):
    """Unfollow uid, but only if you currently follow them (ToggleFollow is a toggle)."""
    if not i_follow(uid):
        return "not-following"
    res = gql(TOGGLE_M, {"id": int(uid)}, auth=True)["ToggleFollow"]
    return "unfollowed" if not res["isFollowing"] else "failed"


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


def migrate(state, events):
    """Older files (version 1) had no 'since', 'status' or score."""
    for info in state.get("followers", {}).values():
        info.setdefault("since", None)
    for e in events:
        if "status" not in e:
            e["status"] = "mercy" if e.get("spared") else "eliminated"
            if e.get("spared_at"):
                e["mercy_at"] = e["spared_at"]
        e.pop("spared", None)
        e.pop("spared_at", None)
    state.setdefault("score", 0)


def main():
    now = now_utc()
    iso = now.isoformat()
    try:
        current = fetch_followers()
    except RuntimeError as e:
        raise SystemExit(f"{e}; nothing changed")

    state = load(STATE, None)
    out = load(OUT, {"events": []})
    events = out.get("events", [])
    log = []

    if state is None:
        # first scan: everyone already here is a veteran (follow date unknown)
        state = {"started": iso, "score": 0,
                 "followers": {uid: dict(info, since=None) for uid, info in current.items()}}
        log.append(f"first scan: {len(current)} veterans registered")
    else:
        migrate(state, events)
        old = state["followers"]
        if len(old) >= 20 and len(current) < len(old) * 0.6:
            raise SystemExit(f"suspicious drop {len(old)} -> {len(current)}, skipping this run")

        # new unfollows -> WANTED
        for uid, info in old.items():
            if uid in current:
                continue
            since = info.get("since")
            days = None if since is None else (now - datetime.fromisoformat(since)).total_seconds() / 86400
            cd = cooldown_minutes(days)
            bounty = VETERAN_BOUNTY if days is None else max(BOUNTY_PER_DAY, round(days * BOUNTY_PER_DAY, -2))
            events.append({"id": int(uid), "name": info["name"], "avatar": info.get("avatar"),
                           "at": iso, "status": "wanted", "veteran": days is None,
                           "followed_days": None if days is None else round(days, 2),
                           "bounty": int(bounty), "cooldown_min": cd,
                           "deadline": (now + timedelta(minutes=cd)).isoformat()})
            log.append(f"WANTED {info['name']} (cooldown {cd} min, bounty {int(bounty)})")

        # wanted people who came back -> BEGGED FOR MERCY; expired -> ELIMINATED
        for e in events:
            if e["status"] != "wanted":
                continue
            uid = str(e["id"])
            if uid in current:
                e["status"] = "mercy"
                e["mercy_at"] = iso
                state["score"] -= POINTS
                log.append(f"MERCY {e['name']}")
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
                e["status"] = "eliminated"
                e["eliminated_at"] = iso
                e["unfollowed"] = result == "unfollowed"
                state["score"] += POINTS
                log.append(f"ELIMINATED {e['name']} ({result})")
                time.sleep(1.0)

        # remember followers; newcomers get a 'since' date
        for uid, info in current.items():
            prev = old.get(uid)
            since = prev["since"] if prev and "since" in prev else (None if prev else iso)
            old_since = since
            current[uid] = dict(info, since=old_since)
        state["followers"] = current

    cutoff = now - timedelta(days=KEEP_DAYS)
    events = [e for e in events if e["status"] == "wanted" or datetime.fromisoformat(e["at"]) >= cutoff][-1000:]

    new_out = {"score": state["score"], "followers": len(current), "hunting_since": state["started"],
               "events": events}
    changed = {k: v for k, v in out.items() if k != "updated"} != new_out
    stale = (now - datetime.fromisoformat(out.get("updated", "2000-01-01T00:00:00+00:00"))) > timedelta(hours=HEARTBEAT_HOURS)
    old_state = load(STATE, None)
    strip = lambda d: {k: v for k, v in d.items() if k != "checked"}
    state_changed = old_state is None or strip(old_state) != strip(state)
    if changed or stale or state_changed:
        state["checked"] = iso
        save(STATE, state)
        save(OUT, dict(new_out, updated=iso))
    for line in log:
        print(line)
    print(f"{len(current)} followers, score {state['score']}, {len(events)} entries, saved={changed or stale or state_changed}")


if __name__ == "__main__":
    main()
