# YouTube live monitor

Alerts when a YouTube channel starts a livestream: a macOS notification with
sound, plus an optional [ntfy.sh](https://ntfy.sh) push to the phone.

Runs every 2 minutes under launchd (`com.ytlive.live-monitor`).

## Setup

```sh
cp env.local.example env.local     # set HANDLE, and NTFY_TOPIC if you want pushes
./check_live.py @SomeChannel       # sanity check: prints one JSON line

sed -e "s|__REPO_DIR__|$PWD|" -e "s|__HOME__|$HOME|" \
    com.ytlive.live-monitor.plist > ~/Library/LaunchAgents/com.ytlive.live-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ytlive.live-monitor.plist
```

`env.local` is untracked on purpose. An ntfy topic name is the only thing
protecting the feed — anyone who knows it can both read your pushes and publish
to it — so it must not live in a public repo. Generate an unguessable one with
`python3 -c "import secrets; print('yt-live-' + secrets.token_urlsafe(9))"`.

## Why not just grep the page for "LIVE"

The obvious one-liner does not work:

```sh
curl -s "https://www.youtube.com/@SomeChannel/streams" | grep -o '"label":"[^"]*LIVE[^"]*"'
```

Plenty of streamers prefix every title with something like `🔴LIVE | `, so this
matches every VOD on the page whether or not anything is on air.

Two more things that look like signals but are not:

- **`/@handle/live`.** If the channel pins a perpetual "upcoming" placeholder —
  a weekly-schedule video whose scheduled start is years in the future — the
  endpoint resolves to that placeholder rather than to any real broadcast.
- **`"isLive":true` in the page JSON.** True for anything that ever *was* a
  livestream, VODs included. The field that means "on air right now" is
  `liveBroadcastDetails.isLiveNow`.

## What is actually checked

In the `ytInitialData` blob on `/streams`, each tile carries a
`thumbnailBadgeViewModel`. The `badgeStyle` distinguishes the three states
cleanly:

| State | badgeStyle | text |
|---|---|---|
| On air now | `THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE` | `LIVE` |
| Finished VOD | `THUMBNAIL_OVERLAY_BADGE_STYLE_DEFAULT` | `1:52:01` |
| Scheduled | `THUMBNAIL_OVERLAY_BADGE_STYLE_DEFAULT` | `Upcoming` |

A tile wearing the LIVE badge is then confirmed against the watch page's
`liveBroadcastDetails.isLiveNow` before anything alerts, so a stale tile cannot
fire a false alarm.

## Files

- `check_live.py` — detection only. Prints one JSON line; exits `0` live,
  `1` not live, `2` error. Takes a channel handle as `argv[1]` or `$YT_HANDLE`,
  so `./check_live.py @LofiGirl` is an easy way to exercise the live path.
- `monitor.sh` — state machine and alerting. Alerts once per broadcast, not
  once per poll.
- `com.ytlive.live-monitor.plist` — launchd template; see Setup.
- `env.local.example` — template for the untracked local config.

## Alerts

Phone pushes go to your ntfy topic. Subscribe in the ntfy app, or watch from a
terminal:

```sh
curl -s "https://ntfy.sh/$NTFY_TOPIC/json"
```

Nothing private is sent — only the stream title and URL.

Notifications are published as a JSON body rather than `X-Title` headers
because stream titles are often non-ASCII and header values get mangled.

`terminal-notifier` is optional. If it works it makes the Mac notification
clickable straight through to the stream; the monitor verifies it actually
succeeded and falls back to `osascript` otherwise, so a broken install cannot
swallow an alert.

Getting it authorized was awkward and is worth recording in case it regresses
after a `brew upgrade`. The Homebrew build is ad-hoc signed (no Team ID). On
macOS 26 a fresh install exits 3 with "Could not request notification
permission", does not appear in System Settings > Notifications, and cannot be
reset with `tccutil reset UserNotification` (exit 70 — notification permissions
no longer live in TCC). Launching the bundle once with `open -a` rather than
exec'ing the binary is what creates the authorization record and lets the
permission be granted.

Note that `defaults export com.apple.ncprefs` does NOT reliably show the
granted state — it read as absent even while alerts worked. The only
trustworthy check is the exit code:

```sh
terminal-notifier -title test -message test; echo $?   # 0 = working, 3 = denied
```

If the checks fail for ~1 hour straight (YouTube layout change, network), it
sends one "monitor is broken" alert instead of failing silently.

## Operating

```sh
tail -f ~/Library/Logs/yt-live-monitor.log     # only logs edges + errors
launchctl kickstart -k gui/$(id -u)/com.ytlive.live-monitor   # force a check
launchctl bootout   gui/$(id -u)/com.ytlive.live-monitor      # stop
```

To re-arm an alert for a stream already alerted on: `rm .last_live_video`.
