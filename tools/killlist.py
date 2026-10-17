"""Hajime's kill list: records who unfollowed ThePhantasmAl on AniList.

Runs every hour from GitHub Actions (.github/workflows/killlist.yml).
Reads the public follower list (no token needed), compares it with the last
check, and writes hajime/kill_list.json for the page.

Files:
  data/followers_state.json   last known followers {id: name}
  hajime/kill_list.json       what the page shows (events + follower count)
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

USER_ID = 5928981                       # ThePhantasmAl (the id never changes, even after a rename)
API = "https://graphql.anilist.co"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "followers_state.json")
OUT = os.path.join(ROOT, "hajime", "kill_list.json")
KEEP_DAYS = 400                         # events older than this are dropped

QUERY = """
query ($id: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    followers(userId: $id, sort: ID) { id name avatar { medium } }
  }
}
"""


def gql(variables, tries=4):
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(API, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "ThePhantasmAl-killlist/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if data.get("errors"):
                raise RuntimeError(data["errors"])
            return data["data"]
        except urllib.error.HTTPError as e:
            wait = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            print(f"HTTP {e.code}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(max(wait, 5 * (attempt + 1)))
        except Exception as e:  # network hiccup
            print(f"error {e}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("AniList unreachable, nothing changed")


def fetch_followers():
    people, page = {}, 1
    while True:
        d = gql({"id": USER_ID, "page": page})["Page"]
        for u in d["followers"]:
            people[str(u["id"])] = {"name": u["name"], "avatar": (u.get("avatar") or {}).get("medium")}
        if not d["pageInfo"]["hasNextPage"]:
            return people
        page += 1
        time.sleep(1.0)                 # stay far below AniList's rate limit


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


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    current = fetch_followers()
    state = load(STATE, None)
    out = load(OUT, {"events": []})
    events = out.get("events", [])

    if state is None:
        print(f"first run: {len(current)} followers saved, kill list starts now")
    else:
        old = state["followers"]
        # safety: a sudden huge drop is almost always an API glitch, not a mass exodus
        if old and len(current) < len(old) * 0.6:
            raise SystemExit(f"suspicious drop {len(old)} -> {len(current)}, skipping this run")
        gone = [uid for uid in old if uid not in current]
        back = [uid for uid in current if uid not in old]
        for uid in gone:
            events.append({"id": int(uid), "name": old[uid]["name"], "avatar": old[uid].get("avatar"),
                           "at": now.isoformat(), "spared": False})
            print("unfollowed:", old[uid]["name"])
        for uid in back:                # an old target followed again -> spared
            for ev in reversed(events):
                if str(ev["id"]) == uid and not ev["spared"]:
                    ev["spared"] = True
                    ev["spared_at"] = now.isoformat()
                    print("spared:", ev["name"])
                    break

    cutoff = now - timedelta(days=KEEP_DAYS)
    events = [e for e in events if datetime.fromisoformat(e["at"]) >= cutoff][-1000:]
    save(STATE, {"checked": now.isoformat(), "followers": current})
    save(OUT, {"updated": now.isoformat(), "followers": len(current), "events": events})
    print(f"{len(current)} followers, {len(events)} kill-list entries")


if __name__ == "__main__":
    main()
