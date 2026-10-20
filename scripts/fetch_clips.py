"""Magyar, all-time legnezettebb Twitch klipek lekerese -> clips.json"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
MIN_VIEWS = int(os.environ.get("MIN_VIEWS", "1000"))         # streamers.txt (es elo csatornak)
MIN_VIEWS_LOW = int(os.environ.get("MIN_VIEWS_LOW", "100"))  # skibidik.txt streamerek
DAYS = int(os.environ.get("DAYS", "0"))                      # 0 = all time, kulonben ennyi napra vissza
PER_STREAMER = 100                                           # streamerenkent a top ennyi klip (max 100)
MAX_PER_STREAMER = int(os.environ.get("MAX_PER_STREAMER", "30"))  # egy streamertol max ennyi klip (hogy ne dominaljon senki)
MAX_CLIPS = 500                                              # ennyi klip kerul a listaba (skibidik klipek ezen felul)
STREAM_PAGES = 5                                             # elo csatorna kereses: hany oldal (x100)
# False: csak a streamers.txt es skibidik.txt streamerei szerepelnek.
# True: az eppen elo magyar csatornak is bekerulnek.
LIVE_DISCOVERY = os.environ.get("LIVE_DISCOVERY", "0") == "1"


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
    return http(url, headers={"Client-Id": CLIENT_ID, "Authorization": "Bearer " + token})


def read_logins(path):
    if not os.path.exists(path):
        print(f"{path}: a fajl nem letezik a repo gyokereben")
        return []
    with open(path, encoding="utf-8") as f:
        lines = [l.strip().lower().lstrip("@") for l in f if l.strip() and not l.startswith("#")]
    # ha valaki a teljes linket irta be, abbol csak a nevet tartjuk meg
    return [l.rstrip("/").split("/")[-1] for l in lines]


def resolve(logins, token):
    users, found_logins = {}, set()
    for i in range(0, len(logins), 100):
        for u in api("users", {"login": logins[i:i + 100]}, token)["data"]:
            users[u["id"]] = u["display_name"]
            found_logins.add(u["login"])
    return users, [l for l in logins if l not in found_logins]


def find_streamers(token):
    """uid -> (nev, min_nezettseg, skibidik_e, listabol_e)"""
    found = {}
    if LIVE_DISCOVERY:
        cursor = None
        for _ in range(STREAM_PAGES):
            params = {"language": "hu", "first": 100}
            if cursor:
                params["after"] = cursor
            res = api("streams", params, token)
            for s in res["data"]:
                found[s["user_id"]] = (s["user_name"], MIN_VIEWS, False, False)
            cursor = res.get("pagination", {}).get("cursor")
            if not cursor:
                break
    for path, min_views, low in (("streamers.txt", MIN_VIEWS, False), ("skibidik.txt", MIN_VIEWS_LOW, True)):
        logins = read_logins(path)
        users, missing = resolve(logins, token)
        print(f"{path}: {len(logins)} nev, ebbol {len(users)} megtalalva a Twitchen")
        if missing:
            print("  nem talalhato (login nev kell, nem a megjelenitett nev):", ", ".join(missing))
        for uid, name in users.items():
            found[uid] = (name, min_views, low, True)
    return found


def main():
    token = get_token()
    streamers = find_streamers(token)
    print(f"{len(streamers)} streamer osszesen")
    window = {}
    if DAYS > 0:
        now = datetime.now(timezone.utc)
        window = {
            "started_at": (now - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    fetched = 0
    clips = {}
    low_ids = set()
    for uid, (name, min_views, low, listed) in streamers.items():
        try:
            data = api("clips", {"broadcaster_id": uid, "first": PER_STREAMER, **window}, token)["data"]
        except Exception as e:
            print("hiba:", name, e)
            continue
        fetched += len(data)
        hu_clips = [c for c in data if c.get("language") == "hu"]
        mine = sorted((c for c in hu_clips if c["view_count"] >= min_views),
                      key=lambda c: c["view_count"], reverse=True)
        hu, kept = len(hu_clips), min(len(mine), MAX_PER_STREAMER)
        for c in mine[:MAX_PER_STREAMER]:
            clips[c["id"]] = {
                "id": c["id"],
                "title": c["title"],
                "broadcaster_id": c["broadcaster_id"],
                "broadcaster_name": c["broadcaster_name"],
                "view_count": c["view_count"],
                "created_at": c["created_at"],
                "url": c["url"],
            }
            if low:
                low_ids.add(c["id"])
        if listed:
            print(f"  {name}: {len(data)} klip lekerve, ebbol hu nyelvu {hu}, megtartva {kept}")
        time.sleep(0.1)
    by_views = lambda c: c["view_count"]
    normal = sorted((c for c in clips.values() if c["id"] not in low_ids), key=by_views, reverse=True)[:MAX_CLIPS]
    extra = [c for c in clips.values() if c["id"] in low_ids]
    result = sorted(normal + extra, key=by_views, reverse=True)
    with open("clips.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"{fetched} klip lekerve, ebbol {len(result)} maradt ({len(extra)} a skibidik listabol)")


if __name__ == "__main__":
    main()
