#!/usr/bin/env python3
"""
One-time RuneMetrics xp-monthly backfill (RuneMetrics goes offline 10 Nov 2026).

Pulls monthly XP gains for each RS3 skill and saves:
  rm_backfill/<player_key>/skill_<id>.json   raw responses (source of truth, doubles as cache)
  rm_backfill/<player_key>/summary.csv       flattened month rows for the monthly_rollups table

Load-safety rules (Jagex best practices + RuneMetrics' aggressive limits):
  * One request at a time, never parallel, with a fixed delay plus jitter between calls.
  * Identifying User-Agent with a contact handle.
  * Cache: a skill already saved on disk is never requested again, so re-runs are free.
  * Redirects are never followed. RuneMetrics answers some players (and will answer
    everyone once retired) with a 302 to an HTML "unavailable" page; that stops the
    player after one call instead of being retried as a bad payload.
  * Probe first: if skill 0 says the profile is missing or private, stop after 1 call.
  * Only retry what's retryable: 429, 5xx, timeouts, dropped connections, empty bodies.
    Never retry other 4xx, 3xx, or a non-JSON body.
  * Honour Retry-After on 429; otherwise exponential backoff capped at 5 minutes.
  * Circuit breaker: a skill that exhausts its retries ends the whole run (max 3 calls
    while the API is unhealthy); 3 unexpected payloads in a row does too.
  * Fixed skill list (ids 0-28): no probing for extra ids.

Data notes (checked against live responses 2026-11-05):
  * RuneMetrics skill ids are 0-based (0 = Attack). Index Lite uses 0 = Overall,
    so hiscore_skill_id = skill_id + 1. Both are written to the CSV.
  * xp values are whole XP and match Index Lite exactly (no tenths).
  * 12 months are returned; the newest is the current month to date.

Stdlib only, Python 3.10+. Usage:
  python runemetrics_backfill.py "Your Name" ["Other Name" ...]

Note: RuneMetrics went offline on 10 Nov 2026, so this is kept for reference and for
anyone who saved responses before then.
"""
import csv
import os
import http.client
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://apps.runescape.com/runemetrics/xp-monthly"
USER_AGENT = os.environ.get("RSXP_USER_AGENT") or (
    f"RS3 XP Planner backfill (contact: {os.environ.get('RSXP_CONTACT') or 'not set'})")
SKILL_IDS = list(range(29))     # RuneMetrics ids 0-28: Attack .. Necromancy
DELAY_S = 5.0                   # base gap between requests (~3 min per player)
JITTER_S = 2.0                  # random extra 0-2 s so calls aren't perfectly periodic
MAX_RETRIES = 3                 # attempts per skill, retryable failures only
MAX_BACKOFF_S = 300
CIRCUIT_BREAKER = 3             # consecutive unexpected payloads before aborting the run
TIMEOUT_S = 30


class Fatal(Exception):
    """Stop everything: the API is throttling or erroring persistently."""


class PlayerUnavailable(Exception):
    """Profile missing or private: skip this player without further calls."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None             # urllib then raises HTTPError with the 3xx code


_opener = urllib.request.build_opener(_NoRedirect)


def player_key(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def polite_sleep(base: float = DELAY_S) -> None:
    time.sleep(base + random.uniform(0, JITTER_S))


def backoff(attempt: int, retry_after: str | None = None) -> float:
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), MAX_BACKOFF_S)
    return min(DELAY_S * 2 ** attempt, MAX_BACKOFF_S)


def fetch(name: str, skill_id: int) -> dict | None:
    """One skill, with retries limited to retryable failures. None = gave up."""
    url = f"{BASE}?{urllib.parse.urlencode({'searchName': name, 'skillid': skill_id})}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with _opener.open(req, timeout=TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            if not body.strip():
                raise ConnectionError("empty body (likely throttled)")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise Fatal(f"skill {skill_id}: non-JSON response ({body[:80]!r}); not retrying")
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise PlayerUnavailable(
                    f"{name}: redirected to {exc.headers.get('Location')!r} "
                    "(profile unavailable, or RuneMetrics retired)")
            if exc.code == 429 or exc.code >= 500:
                if attempt == MAX_RETRIES:
                    break
                wait = backoff(attempt, exc.headers.get("Retry-After"))
                print(f"  skill {skill_id}: HTTP {exc.code}, waiting {wait:.0f}s "
                      f"(attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            if exc.code == 404:
                raise PlayerUnavailable(f"HTTP 404 for {name}")
            raise Fatal(f"HTTP {exc.code} on skill {skill_id}; not retrying a client error")
        except (OSError, http.client.HTTPException) as exc:   # timeouts, resets, empty body
            if attempt == MAX_RETRIES:
                break
            wait = backoff(attempt)
            print(f"  skill {skill_id}: {exc}; waiting {wait:.0f}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
    return None


def check_error(data: dict, name: str) -> None:
    """RuneMetrics reports missing/private profiles as JSON with an 'error' field."""
    err = data.get("error")
    if err and "monthlyXpGain" not in data:
        raise PlayerUnavailable(f"{name}: RuneMetrics returned {err!r}")


def flatten(data: dict):
    for entry in data.get("monthlyXpGain", []):
        sid = entry.get("skillId")
        for m in entry.get("monthData", []):
            ts = m.get("timestamp")
            month = (datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")
                     if isinstance(ts, (int, float)) else "")
            yield {"skill_id": sid,
                   "hiscore_skill_id": sid + 1 if isinstance(sid, int) else "",
                   "month": month, "xp_gain": m.get("xpGain"),
                   "rank": m.get("rank"), "total_xp": entry.get("totalXp"),
                   "source": "runemetrics"}


def backfill(name: str) -> None:
    key = player_key(name)
    out = Path("rm_backfill") / key
    out.mkdir(parents=True, exist_ok=True)
    rows, failed, streak, calls = [], [], 0, 0

    for skill_id in SKILL_IDS:
        cached = out / f"skill_{skill_id}.json"
        if cached.exists():                      # cache hit: no request
            rows.extend(flatten(json.loads(cached.read_text())))
            continue

        if calls:                                # gap before every call after the first
            polite_sleep()
        print(f"{name}: skill {skill_id}")
        data = fetch(name, skill_id)
        calls += 1

        if data is None:                         # retries exhausted = API unhealthy
            raise Fatal(f"skill {skill_id} still failing after {MAX_RETRIES} tries; stopping. "
                        "Re-run later: saved skills won't be requested again.")

        check_error(data, name)                  # raises on missing/private profile
        if "monthlyXpGain" not in data:
            (out / f"skill_{skill_id}_unexpected.json").write_text(json.dumps(data, indent=2))
            failed.append(skill_id)
            streak += 1
            if streak >= CIRCUIT_BREAKER:
                raise Fatal(f"{streak} unexpected responses in a row; stopping.")
            continue

        streak = 0
        cached.write_text(json.dumps(data, indent=2))
        rows.extend(flatten(data))

    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["skill_id", "hiscore_skill_id", "month", "xp_gain",
                                          "rank", "total_xp", "source"])
        w.writeheader()
        w.writerows(rows)

    print(f"{name}: {calls} requests, {len(rows)} month rows saved to {out}"
          + (f"; failed skill ids {failed} (re-run to retry just those)" if failed else ""))


if __name__ == "__main__":
    names = sys.argv[1:]
    if not names:
        sys.exit('usage: python runemetrics_backfill.py "Display Name" ["Other Name" ...]')
    try:
        for i, n in enumerate(names):
            if i:
                polite_sleep(DELAY_S * 2)        # extra gap between players
            try:
                backfill(n)
            except PlayerUnavailable as exc:
                print(f"Skipping: {exc}")
    except Fatal as exc:
        print(f"Aborted: {exc}")
        sys.exit(1)
