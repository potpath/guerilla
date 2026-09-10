#!/usr/bin/env python3
"""Decides whether a YouTube channel is live RIGHT NOW, and prints one JSON line.

Usage: check_live.py @SomeHandle

Why this is not a grep: the obvious approach of grepping the channel page for
"LIVE" also matches *video titles*, and many streamers prefix every title with
something like "LIVE |", so every past VOD looks live. The real signal is
structural, not textual: in the ytInitialData blob each stream tile carries a
thumbnailBadgeViewModel whose badgeStyle is THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE
only while the broadcast is actually running. A finished VOD's badge is
..._STYLE_DEFAULT holding a duration ("1:52:01"), and a scheduled one is
..._STYLE_DEFAULT holding "Upcoming".

The /@handle/live endpoint is deliberately NOT used as the primary signal. If a
channel pins a perpetual "upcoming" placeholder (a weekly-schedule video whose
start timestamp is years in the future), /live resolves to that placeholder
rather than to any real broadcast.

Exit codes: 0 = live, 1 = not live, 2 = error (network, layout change, ...).
"""

import json
import os
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
LIVE_BADGE = "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE"
TIMEOUT = 20


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        # Without an explicit language YouTube sometimes serves a consent
        # interstitial that contains no ytInitialData at all.
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def extract_json(html, marker):
    """Pull the JS object literal that follows `marker` out of the page.

    Uses raw_decode rather than a regex: these blobs contain escaped braces and
    "</script>" sequences inside string values, so any non-greedy regex that
    stops at the first `};` is one unusual stream title away from breaking.
    """
    i = html.find(marker)
    if i == -1:
        return None
    i = html.find("{", i)
    if i == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, i)
        return obj
    except ValueError:
        return None


def walk(node, key, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            walk(v, key, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, key, out)


def find_live_tiles(data):
    """Return [{videoId, title}] for tiles currently wearing the LIVE badge."""
    lockups = []
    walk(data, "lockupViewModel", lockups)
    live = []
    for lk in lockups:
        badges = []
        walk(lk, "thumbnailBadgeViewModel", badges)
        if not any(b.get("badgeStyle") == LIVE_BADGE for b in badges):
            continue
        video_id = lk.get("contentId")
        meta = []
        walk(lk, "lockupMetadataViewModel", meta)
        title = meta[0].get("title", {}).get("content") if meta else None
        if video_id:
            live.append({"videoId": video_id, "title": title})
    return live


def confirm(video_id):
    """Second opinion straight from the player, so one stale tile can't alert.

    liveBroadcastDetails.isLiveNow is the authoritative flag; it goes false the
    moment the broadcast ends, well before the channel page tile catches up.
    """
    html = fetch("https://www.youtube.com/watch?v=" + video_id)
    pr = extract_json(html, "ytInitialPlayerResponse")
    if not pr:
        return None
    vd = pr.get("videoDetails", {})
    mf = pr.get("microformat", {}).get("playerMicroformatRenderer", {})
    lbd = mf.get("liveBroadcastDetails", {})
    return {
        "isLiveNow": bool(lbd.get("isLiveNow")),
        "isUpcoming": bool(vd.get("isUpcoming")),
        "startTimestamp": lbd.get("startTimestamp"),
        "title": vd.get("title"),
        "concurrentViewers": vd.get("viewCount"),
    }


def main():
    handle = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YT_HANDLE")
    if not handle:
        print(json.dumps({"status": "error",
                          "error": "no channel handle (argv[1] or $YT_HANDLE)"}))
        return 2
    url = "https://www.youtube.com/{}/streams".format(handle)

    try:
        html = fetch(url)
    except (urllib.error.URLError, OSError) as e:
        print(json.dumps({"status": "error", "error": "fetch: %s" % e}))
        return 2

    data = extract_json(html, "var ytInitialData")
    if data is None:
        # Distinguishing this from "not live" matters: silently treating a
        # layout change as offline is how a monitor goes quiet for weeks.
        print(json.dumps({"status": "error",
                          "error": "ytInitialData not found (page layout changed?)"}))
        return 2

    tiles = find_live_tiles(data)
    if not tiles:
        print(json.dumps({"status": "offline"}))
        return 1

    tile = tiles[0]
    try:
        det = confirm(tile["videoId"])
    except (urllib.error.URLError, OSError) as e:
        det = None
        confirm_err = str(e)
    else:
        confirm_err = None

    if det is not None and not det["isLiveNow"]:
        print(json.dumps({"status": "offline", "note": "stale LIVE badge on %s"
                          % tile["videoId"]}))
        return 1

    out = {
        "status": "live",
        "videoId": tile["videoId"],
        "title": (det or {}).get("title") or tile["title"],
        "url": "https://www.youtube.com/watch?v=" + tile["videoId"],
        "startTimestamp": (det or {}).get("startTimestamp"),
        "concurrentViewers": (det or {}).get("concurrentViewers"),
        # Recorded so the log shows when an alert rested on the badge alone.
        "confirmed": det is not None,
    }
    if confirm_err:
        out["confirmError"] = confirm_err
    if len(tiles) > 1:
        out["alsoLive"] = [t["videoId"] for t in tiles[1:]]
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
