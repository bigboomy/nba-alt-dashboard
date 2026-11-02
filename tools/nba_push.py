#!/usr/bin/env python3
"""
nba_push.py v1 - web-push alerts to the dashboard's home-screen app (iPhone/Android).
Needs: pip install pywebpush==2.5.0  (pulls py-vapid, http-ece, cryptography)
Env:   PUSH_SUBS      JSON: one subscription object or a list of them (the "device code" from the app)
       VAPID_PRIVATE  base64url raw P-256 private key (kept only in the task prompt)

Commands:
  send run.json data.json   one alert for this run's picks; badge = picks on today's dashboard
  test                      a test alert ("Alerts are working")

Exit: 0 sent to at least one device, 3 nothing to send (run made no picks), 1 error / all devices failed.
Prints {"sent": n, "failed": [...], "expired": [...]}; an expired device (HTTP 404/410) must re-enable
alerts in the app and send a new device code.
"""
import sys, json, os

URL = "https://bigboomy.github.io/nba-alt-dashboard/"
CLAIMS = {"sub": "https://bigboomy.github.io"}


def die(m):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(1)


def subs():
    raw = os.environ.get("PUSH_SUBS", "").strip()
    if not raw:
        die("PUSH_SUBS not set")
    try:
        s = json.loads(raw)
    except Exception as e:
        die(f"PUSH_SUBS is not JSON: {e}")
    s = s if isinstance(s, list) else [s]
    s = [x for x in s if isinstance(x, dict) and x.get("endpoint") and isinstance(x.get("keys"), dict)]
    if not s:
        die("PUSH_SUBS has no valid subscription")
    return s


def picks_of(obj):
    out = []
    for g in (obj or {}).get("games", []) or []:
        for p in g.get("picks", []) or []:
            out.append((g, p))
    return out


def fmt(g, p):
    return f'{p.get("p", "?")} {p.get("s", "")} O{p.get("alt", "?")} @{p.get("o", "?")}'


def message(run, data):
    mine = picks_of(run)
    if not mine:
        return None
    games = []
    for g, _ in mine:
        if g.get("id") not in [x.get("id") for x in games]:
            games.append(g)
    head = " · ".join(f'{g.get("id")} {g.get("tip", "")}'.strip() for g in games[:3])
    lines = [fmt(g, p) for g, p in mine[:4]]
    more = len(mine) - len(lines)
    body = head + "\n" + "\n".join(lines) + (f"\n+{more} more" if more > 0 else "")
    total = len(picks_of(data)) if data else len(mine)
    return {"title": f"NBA picks: {len(mine)} new", "body": body, "badge": max(total, len(mine)),
            "url": URL, "tag": "nba-picks"}


def send(payload):
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        die("pywebpush missing: pip install --break-system-packages pywebpush==2.5.0")
    key = os.environ.get("VAPID_PRIVATE", "").strip()
    if not key:
        die("VAPID_PRIVATE not set")
    sent, failed, expired = 0, [], []
    for s in subs():
        host = s["endpoint"].split("/")[2]
        try:
            webpush(subscription_info=s, data=json.dumps(payload), vapid_private_key=key,
                    vapid_claims=dict(CLAIMS), ttl=3600, content_encoding="aes128gcm",
                    headers={"Urgency": "high"}, timeout=20)
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            (expired if code in (404, 410) else failed).append(f"{host}: {code or ''} {str(e)[:160]}")
        except Exception as e:
            failed.append(f"{host}: {type(e).__name__} {str(e)[:160]}")
    print(json.dumps({"sent": sent, "failed": failed, "expired": expired, "title": payload["title"]}))
    sys.exit(0 if sent else 1)


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    if a[0] == "test":
        send({"title": "NBA alerts are working", "body": "You'll get one alert per slate run that makes picks.",
              "badge": 0, "url": URL, "tag": "nba-test"})
    elif a[0] == "send" and len(a) >= 2:
        run = load(a[1])
        if run is None:
            die("run.json unreadable")
        msg = message(run, load(a[2]) if len(a) > 2 else None)
        if not msg:
            print(json.dumps({"sent": 0, "note": "run made no picks"}))
            sys.exit(3)
        send(msg)
    else:
        die(__doc__)


if __name__ == "__main__":
    main()
