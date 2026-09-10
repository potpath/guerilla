#!/bin/bash
# Alerts when a YouTube channel starts a livestream.
# Intended to run every 2 minutes via launchd (see com.ytlive.live-monitor.plist).
#
# check_live.py does the detection; this script owns the state machine, so an
# alert fires once per broadcast rather than every two minutes for five hours.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Local, untracked config: HANDLE (e.g. @SomeChannel) and NTFY_TOPIC.
# Kept out of the repo because an ntfy topic name is the only thing protecting
# the feed: anyone who knows it can both read the pushes and publish to it.
# shellcheck source=/dev/null
[ -f "$DIR/env.local" ] && . "$DIR/env.local"
HANDLE="${HANDLE:-}"
NTFY_TOPIC="${NTFY_TOPIC:-}"
if [ -z "$HANDLE" ]; then
  echo "monitor.sh: HANDLE is not set (see env.local.example)" >&2
  exit 2
fi
PY="/usr/bin/python3"           # stdlib-only script: no homebrew dependency
CURL="/usr/bin/curl"
# Resolved as an absolute path rather than looked up in PATH: launchd runs this
# script with a minimal PATH that excludes /opt/homebrew/bin, so the lookup
# failed only under launchd and quietly fell through to the osascript branch -
# whose notifications belong to Script Editor, so clicking a live alert opened
# Script Editor instead of the stream.
TN=""
for _c in /opt/homebrew/bin/terminal-notifier /usr/local/bin/terminal-notifier \
          "$(command -v terminal-notifier 2>/dev/null || true)"; do
  if [ -x "$_c" ]; then TN="$_c"; break; fi
done
STATE="${STATE:-$DIR/.last_live_video}"   # videoId we have already alerted for
ERRSTATE="$DIR/.error_streak"   # consecutive failures, for the "monitor broke" alert
ERR_ALERT_AFTER=30              # 30 * 2min = alert if blind for ~1 hour

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

notify_mac() {
  local title="$1" body="$2" url="$3"
  # terminal-notifier is preferred because it can carry a click-through to the
  # stream, which osascript cannot. But being installed is NOT proof it works:
  # on macOS 26 an ad-hoc-signed build can be installed and permanently denied
  # notification permission, failing with exit 3 every time. So we require it to
  # actually succeed, and fall through to osascript otherwise - a broken
  # notifier must never silently swallow a live alert.
  if [ -n "$TN" ]; then
    local tn_args=(-title "$title" -message "$body" -sound Glass)
    [ -n "$url" ] && tn_args+=(-open "$url")
    if "$TN" "${tn_args[@]}" >/dev/null 2>&1; then
      return 0
    fi
  fi
  # Passed as argv rather than interpolated into the AppleScript source: stream
  # titles contain quotes and emoji that would otherwise break the script.
  local fallback_body="$body"
  [ -n "$url" ] && fallback_body="$body
$url"
  /usr/bin/osascript - "$title" "$fallback_body" <<'OSA' >/dev/null 2>&1
on run argv
  display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
end run
OSA
}

notify_ntfy() {
  local title="$1" body="$2" url="$3" tags="$4" priority="$5"
  [ -n "$NTFY_TOPIC" ] || return 0   # phone pushes are optional
  # Published as a JSON body, not as X-Title/X-Message headers: stream titles
  # are often non-ASCII, and non-ASCII header values get mangled or rejected.
  "$PY" - "$NTFY_TOPIC" "$title" "$body" "$url" "$tags" "$priority" <<'PYA' 2>/dev/null |
import json, sys
topic, title, body, url, tags, priority = sys.argv[1:7]
msg = {"topic": topic, "title": title, "message": body,
       "tags": [tags], "priority": int(priority)}
if url:
    msg["click"] = url
sys.stdout.write(json.dumps(msg))
PYA
  "$CURL" -s -m 15 -X POST -H "Content-Type: application/json" -d @- \
    "https://ntfy.sh/" >/dev/null
}

OUT=$("$PY" "$DIR/check_live.py" "$HANDLE" 2>&1)
RC=$?

case "$RC" in
  0)
    echo 0 > "$ERRSTATE"
    VIDEO_ID=$(echo "$OUT" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["videoId"])')
    TITLE=$(echo "$OUT" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["title"] or "")')
    URL="https://www.youtube.com/watch?v=$VIDEO_ID"
    LAST=$(cat "$STATE" 2>/dev/null || echo "")

    if [ "$VIDEO_ID" = "$LAST" ]; then
      exit 0                    # already alerted for this broadcast; stay quiet
    fi

    log "LIVE: $VIDEO_ID - $TITLE"
    notify_mac "$HANDLE is LIVE 🔴" "$TITLE" "$URL"
    notify_ntfy "$HANDLE is LIVE 🔴" "$TITLE
$URL" "$URL" "red_circle" 4
    echo "$VIDEO_ID" > "$STATE"
    ;;
  1)
    echo 0 > "$ERRSTATE"
    # Only log the offline->online edge, otherwise this logs 720 times a day.
    if [ -s "$STATE" ]; then
      log "stream ended ($(cat "$STATE"))"
      : > "$STATE"
    fi
    ;;
  *)
    STREAK=$(( $(cat "$ERRSTATE" 2>/dev/null || echo 0) + 1 ))
    echo "$STREAK" > "$ERRSTATE"
    log "ERROR (streak $STREAK): $OUT"
    # A monitor that fails silently is worse than no monitor: say so once,
    # then stay quiet until it recovers.
    if [ "$STREAK" -eq "$ERR_ALERT_AFTER" ]; then
      notify_mac "Live monitor is broken" "No successful check in ~1h. See ~/Library/Logs/yt-live-monitor.log" ""
      notify_ntfy "Live monitor is broken" "No successful check in ~1h - YouTube layout change?" "" "warning" 4
    fi
    ;;
esac
