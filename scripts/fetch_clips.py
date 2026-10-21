"""Magyar Twitch klipek lekerese -> clips.json

Ket reszbol all:
1) Normal klipek: csak a streamers.txt es skibidik.txt streamerei.
2) Hires klipek: a magyar all-time legnezettebb klipek, TOKMINDEGY kitol
   (elo magyar csatornak + top jatekok + a listas streamerek alapjan keresve).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
MIN_VIEWS = int(os.environ.get("MIN_VIEWS", "1000"))         # streamers.txt
MIN_VIEWS_LOW = int(os.environ.get("MIN_VIEWS_LOW", "100"))  # skibidik.txt
DAYS = int(os.environ.get("DAYS", "0"))                      # 0 = all time, kulonben ennyi napra vissza
PER_STREAMER = 100                                           # streamerenkent a top ennyi klip lekerese (max 100)
MAX_PER_STREAMER = int(os.environ.get("MAX_PER_STREAMER", "30"))  # egy listas streamertol max ennyi klip
MAX_CLIPS = 500                                              # normal klipek max szama (skibidik ezen felul)
# Hires klipek (a kor 2 nagyon nezett klipje)
FAMOUS_DISCOVERY = os.environ.get("FAMOUS_DISCOVERY", "1") == "1"
FAMOUS_POOL = int(os.environ.get("FAMOUS_POOL", "40"))       # ennyi hires klip legyen a kesz listaban
FAMOUS_PER_STREAMER = 5                                      # egy streamertol max ennyi hires klip
STREAM_PAGES = 5                                             # elo magyar csatornak: hany oldal (x100)
GAME_SCAN = int(os.environ.get("GAME_SCAN", "100"))          # ennyi top jatek top klipjeit is atnezi


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_token():
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }).encode()
    return http("https://id.twitch.tv/oauth2/token", data=body)["access_token"]


def api(path, params, token):
    url = "https://api.twitch.tv/helix/" + path + "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(3):
        try:
            return http(url, headers={"Client-Id": CLIENT_ID, "Authorization": "Bearer " + token})
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(10)
                continue
            raise


def time_window():
    if DAYS <= 0:
        return {}
    now = datetime.now(timezone.utc)
    return {
        "started_at": (now - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def clips_for(params, token):
    return api("clips", {"first": PER_STREAMER, **time_window(), **params}, token)["data"]


def to_clip(c):
    return {
        "id": c["id"],
        "title": c["title"],
        "broadcaster_id": c["broadcaster_id"],
        "broadcaster_name": c["broadcaster_name"],
        "view_count": c["view_count"],
        "created_at": c["created_at"],
        "url": c["url"],
    }


def read_logins(path):
    if not os.path.exists(path):
        print(f"{path}: a fajl nem letezik a repo gyokereben")
        return []
    with open(path, encoding="utf-8") as f:
        lines = [l.strip().lower().lstrip("@") for l in f if l.strip() and not l.startswith("#")]
    return [l.rstrip("/").split("/")[-1] for l in lines]  # ha teljes linket irtak be


def resolve(logins, token):
    users, found_logins = {}, set()
    for i in range(0, len(logins), 100):
        for u in api("users", {"login": logins[i:i + 100]}, token)["data"]:
            users[u["id"]] = u["display_name"]
            found_logins.add(u["login"])
    return users, [l for l in logins if l not in found_logins]


def find_streamers(token):
    """uid -> (nev, min_nezettseg, skibidik_e)"""
    found = {}
    for path, min_views, low in (("streamers.txt", MIN_VIEWS, False), ("skibidik.txt", MIN_VIEWS_LOW, True)):
        logins = read_logins(path)
        users, missing = resolve(logins, token)
        print(f"{path}: {len(logins)} nev, ebbol {len(users)} megtalalva a Twitchen")
        if missing:
            print("  nem talalhato (login nev kell, nem a megjelenitett nev):", ", ".join(missing))
        for uid, name in users.items():
            found[uid] = (name, min_views, low)
    return found


def discover_famous(token, known_uids):
    """Magyar klipek keresese streamer-listatol fuggetlenul (csak a hires listahoz)."""
    found = {}
    live, cursor = {}, None
    for _ in range(STREAM_PAGES):
        params = {"language": "hu", "first": 100}
        if cursor:
            params["after"] = cursor
        res = api("streams", params, token)
        for s in res["data"]:
            live[s["user_id"]] = s["user_name"]
        cursor = res.get("pagination", {}).get("cursor")
        if not cursor:
            break
    print(f"hires keresés: {len(live)} elo magyar csatorna")
    for uid, name in live.items():
        if uid in known_uids:
            continue
        try:
            data = clips_for({"broadcaster_id": uid}, token)
        except Exception as e:
            print("hiba:", name, e)
            continue
        for c in data:
            if c.get("language") == "hu":
                found[c["id"]] = to_clip(c)
        time.sleep(0.1)
    if GAME_SCAN > 0:
        for g in api("games/top", {"first": min(GAME_SCAN, 100)}, token)["data"]:
            try:
                data = clips_for({"game_id": g["id"]}, token)
            except Exception as e:
                print("hiba (jatek):", g.get("name"), e)
                continue
            for c in data:
                if c.get("language") == "hu":
                    found[c["id"]] = to_clip(c)
            time.sleep(0.1)
    return found


def main():
    token = get_token()
    streamers = find_streamers(token)
    print(f"{len(streamers)} listas streamer")
    normal_clips, low_ids, candidates = {}, set(), {}
    fetched = 0
    for uid, (name, min_views, low) in streamers.items():
        try:
            data = clips_for({"broadcaster_id": uid}, token)
        except Exception as e:
            print("hiba:", name, e)
            continue
        fetched += len(data)
        hu_clips = [c for c in data if c.get("language") == "hu"]
        for c in hu_clips:
            candidates[c["id"]] = to_clip(c)
        mine = sorted((c for c in hu_clips if c["view_count"] >= min_views),
                      key=lambda c: c["view_count"], reverse=True)[:MAX_PER_STREAMER]
        for c in mine:
            normal_clips[c["id"]] = to_clip(c)
            if low:
                low_ids.add(c["id"])
        print(f"  {name}: {len(data)} klip lekerve, ebbol hu nyelvu {len(hu_clips)}, megtartva {len(mine)}")
        time.sleep(0.1)

    if FAMOUS_DISCOVERY:
        candidates.update(discover_famous(token, set(streamers)))
    famous, per = [], {}
    for c in sorted(candidates.values(), key=lambda c: c["view_count"], reverse=True):
        if len(famous) >= FAMOUS_POOL:
            break
        if per.get(c["broadcaster_id"], 0) >= FAMOUS_PER_STREAMER:
            continue
        per[c["broadcaster_id"]] = per.get(c["broadcaster_id"], 0) + 1
        famous.append(c)

    by_views = lambda c: c["view_count"]
    normal = sorted((c for c in normal_clips.values() if c["id"] not in low_ids), key=by_views, reverse=True)[:MAX_CLIPS]
    extra = [c for c in normal_clips.values() if c["id"] in low_ids]
    out = {c["id"]: dict(c) for c in normal + extra}
    for c in famous:
        out.setdefault(c["id"], dict(c))["famous"] = True
    result = sorted(out.values(), key=by_views, reverse=True)
    with open("clips.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"{fetched} klip lekerve a listas streamerektol, a fajlban {len(result)} klip van, ebbol {len(famous)} hires")
    for c in famous[:5]:
        print(f"  hires: {c['broadcaster_name']} - {c['title']} ({c['view_count']})")


if __name__ == "__main__":
    main()
