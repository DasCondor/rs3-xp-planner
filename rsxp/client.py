"""Polite client for the RuneScape Platform API.

Every request in the process goes through one lock, so calls are strictly sequential
with at least MIN_GAP_S (+ jitter) between them. Retries only 429 / 5xx / network errors,
honours Retry-After, never follows redirects, and a circuit breaker pauses all calls
after repeated failures.
"""
import http.client
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

# Jagex asks for a User-Agent that names the app and, ideally, how to contact you.
# Set RSXP_CONTACT (e.g. "@you on Discord" or a GitHub URL), or replace the whole
# string with RSXP_USER_AGENT.
USER_AGENT = os.environ.get("RSXP_USER_AGENT") or (
    f"RS3 XP Planner/{__version__} (self-hosted; contact: {os.environ.get('RSXP_CONTACT') or 'not set'})")
HISCORES = "https://secure.runescape.com/m=hiscore/index_lite.json"
EVENTS = "https://secure.runescape.com/m=adventurers-log/api/v1/playerEvents/"
QUESTS = "https://secure.runescape.com/m=adventurers-log/api/v1/playerQuests/"
# RuneMetrics closes 10 Nov 2026; until then it is the only source of past XP.
XP_MONTHLY = "https://apps.runescape.com/runemetrics/xp-monthly"
PROFILE = "https://apps.runescape.com/runemetrics/profile/profile"

MIN_GAP_S = 5.0
JITTER_S = 2.0
TIMEOUT_S = 30
MAX_ATTEMPTS = 3
MAX_BACKOFF_S = 300
BREAKER_FAILS = 3
BREAKER_PAUSE_S = 1800
MAX_BODY = 2_000_000      # bytes; real answers are a few KB, anything bigger is refused


class NotFound(Exception):
    """Player doesn't exist (or is hidden) on this endpoint."""


class NotMember(Exception):
    """Endpoint is members-only and the player isn't a member."""


class Unavailable(Exception):
    """API is failing or throttling; the call was not completed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)
_lock = threading.Lock()
_last_call = 0.0
_fails = 0
_paused_until = 0.0


def _send(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with _opener.open(req, timeout=TIMEOUT_S) as resp:
        body = resp.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError(f"response over {MAX_BODY} bytes")
    return body


def _error_code(exc: urllib.error.HTTPError) -> str:
    try:
        return json.loads(exc.read(MAX_BODY).decode("utf-8")).get("error", "")
    except Exception:
        return ""


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), MAX_BACKOFF_S)
    return min(MIN_GAP_S * 2 ** attempt, MAX_BACKOFF_S)


def paused_for() -> float:
    return max(0.0, _paused_until - time.time())


def get_json(url: str) -> dict:
    global _last_call, _fails, _paused_until
    with _lock:
        if time.time() < _paused_until:
            raise Unavailable(f"API calls paused for {paused_for():.0f}s after repeated failures")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            gap = _last_call + MIN_GAP_S + random.uniform(0, JITTER_S) - time.monotonic()
            if gap > 0 and _last_call:
                time.sleep(gap)
            _last_call = time.monotonic()
            wait = None
            try:
                body = _send(url)
                if not body.strip():
                    raise ConnectionError("empty body")
                data = json.loads(body)
                _fails = 0
                return data
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    raise NotFound(f"redirected to {exc.headers.get('Location')}")
                code = _error_code(exc)
                if code == "PLAYER_NOT_MEMBER":
                    raise NotMember(code)
                if exc.code == 404 or code == "PLAYER_NOT_FOUND":
                    raise NotFound(code or "HTTP 404")
                if exc.code == 429 or exc.code >= 500:
                    wait = _backoff(attempt, exc.headers.get("Retry-After"))
                else:
                    raise Unavailable(f"HTTP {exc.code}; not retrying a client error")
            except (json.JSONDecodeError, ValueError) as exc:
                raise Unavailable(f"unusable response: {exc}")
            except (OSError, http.client.HTTPException):
                wait = _backoff(attempt, None)
            if attempt < MAX_ATTEMPTS:
                time.sleep(wait)
        _fails += 1
        if _fails >= BREAKER_FAILS:
            _paused_until = time.time() + BREAKER_PAUSE_S
        raise Unavailable(f"gave up after {MAX_ATTEMPTS} attempts")


def hiscores(name: str) -> dict:
    return get_json(f"{HISCORES}?{urllib.parse.urlencode({'player': name})}")


def events(name: str) -> dict:
    return get_json(EVENTS + urllib.parse.quote(name))


def quests(name: str) -> dict:
    return get_json(QUESTS + urllib.parse.quote(name))


def xp_monthly(name: str, skill: int) -> dict:
    """Twelve months of XP gains for one skill (RuneMetrics numbering: Attack = 0)."""
    return get_json(f"{XP_MONTHLY}?{urllib.parse.urlencode({'searchName': name, 'skillid': skill})}")


def profile(name: str) -> dict:
    """RuneMetrics profile; {"error": "PROFILE_PRIVATE"} when the player has hidden it."""
    return get_json(f"{PROFILE}?{urllib.parse.urlencode({'user': name, 'activities': 0})}")
