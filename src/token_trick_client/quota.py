"""Provider quota meters and window costs for Token Trick.

Both providers publish their own percent-of-plan meter; this module reads them
and pairs each with the tokens spent inside that window so the dashboard can
put a list-price value on "one full window". Nothing here guesses: a meter that
cannot be read is reported with an ``error`` rather than a fabricated value.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

from coding_agent_sessions import rate_limit_samples, totals_by_model, CLAUDE_TOKEN_FIELDS, TOKEN_FIELDS

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
HISTORY_DAYS = 14
CODEX_WINDOW_NAMES = {10080: "weekly", 300: "session"}


def codex_window_cost(turns, start):
    return {
        model: {
            "total": f["total_tokens"], "cached": f["cached_input_tokens"],
            "fresh_input": f["input_tokens"] - f["cached_input_tokens"], "output": f["output_tokens"],
        } for model, f in totals_by_model(turns, TOKEN_FIELDS, start).items()
    }


def claude_window_cost(requests, start):
    return {
        model: {
            "input": f["input_tokens"], "cache_write": f["cache_creation_input_tokens"],
            "cache_read": f["cache_read_input_tokens"], "output": f["output_tokens"],
        } for model, f in totals_by_model(requests, CLAUDE_TOKEN_FIELDS, start).items()
    }


def claude_model_rows(models):
    rows = [{
        "model": model, "input": f["input_tokens"], "cache_write": f["cache_creation_input_tokens"],
        "cache_read": f["cache_read_input_tokens"], "output": f["output_tokens"],
    } for model, f in models.items()]
    return sorted(rows, key=lambda row: (-(row["cache_read"] + row["input"] + row["output"]), row["model"]))


def _hourly(samples):
    """Keep the highest reading per hour so history stays bounded."""
    keep = {}
    for sample in samples:
        key = (sample["provider"], sample["window"], sample["at"][:13])
        if key not in keep or sample["used_percent"] > keep[key]["used_percent"]:
            keep[key] = sample
    return sorted(keep.values(), key=lambda sample: sample["at"])


def codex_meters(turns, now):
    """Meters from an in-memory list of ``codex_turn_usage`` turns (one log scan)."""
    floor = now - timedelta(days=HISTORY_DAYS)
    samples = rate_limit_samples(turn for turn in turns if turn["at"] >= floor)
    history, latest = [], {}
    for sample in samples:
        window = CODEX_WINDOW_NAMES.get(sample["window_minutes"], f"{sample['window_minutes']}m")
        entry = {"at": sample["at"], "provider": "codex", "window": window, "used_percent": sample["used_percent"], "resets_at": sample["resets_at"]}
        history.append(entry)
        latest[window] = (entry, sample["window_minutes"])
    meters = []
    for window, (entry, minutes) in latest.items():
        resets = datetime.fromisoformat(entry["resets_at"]) if entry["resets_at"] else None
        stale = resets is not None and resets <= now
        start = resets - timedelta(minutes=minutes) if resets and not stale else None
        meters.append({
            "provider": "codex", "window": window,
            "used_percent": None if stale else entry["used_percent"],
            "resets_at": entry["resets_at"], "scope": None,
            "window_cost_tokens": codex_window_cost(turns, start) if start else None,
            "error": f"last reading {entry['at']} is from a window that already reset" if stale else None,
        })
    if not meters:
        meters.append({"provider": "codex", "window": "weekly", "used_percent": None, "resets_at": None, "scope": None, "window_cost_tokens": None, "error": "no rate_limits readings in Codex session logs"})
    return meters, _hourly(history)


def _claude_token(credentials_path, now):
    data = json.loads(Path(credentials_path).read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    token, expires = data.get("accessToken"), data.get("expiresAt")
    if not token:
        raise ValueError("no claudeAiOauth.accessToken in credentials")
    if expires is not None and datetime.fromtimestamp(float(expires) / 1000, tz=timezone.utc) <= now:
        raise ValueError("Claude OAuth token expired; run any Claude Code command to refresh it")
    return token


def claude_meters(credentials_path, requests, now, timeout=15):
    """Meters from Claude's usage endpoint paired with in-memory ``claude_request_usage`` items."""
    def failed(reason):
        return [{"provider": "claude", "window": "weekly_all", "used_percent": None, "resets_at": None, "scope": None, "window_cost_tokens": None, "error": reason[:300]}], []
    try:
        token = _claude_token(credentials_path, now)
        request = urllib.request.Request(CLAUDE_USAGE_URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except FileNotFoundError:
        return failed("Claude quota credential is unavailable on this computer")
    except PermissionError:
        return failed("Claude quota credential cannot be read with current permissions")
    except ValueError as error:
        # Parser messages can contain local content. Only our fixed credential
        # diagnostics may enter the uploaded quota record.
        message = str(error)
        known = ("no claudeAiOauth.accessToken in credentials", "Claude OAuth token expired; run any Claude Code command to refresh it")
        return failed(message if message in known else "Claude quota response or credential is invalid")
    except urllib.error.HTTPError as error:
        return failed(f"Claude quota lookup returned HTTP {error.code}")
    except OSError:
        return failed("Claude quota lookup failed or timed out")
    limits = body.get("limits")
    if not isinstance(limits, list):
        return failed("usage endpoint returned no limits list")
    meters, history = [], []
    for limit in limits:
        kind = limit.get("kind")
        resets = limit.get("resets_at")
        scope = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
        span = timedelta(hours=5) if kind == "session" else timedelta(days=7)
        start = datetime.fromisoformat(resets) - span if resets else None
        cost = claude_window_cost(requests, start) if start and kind != "weekly_scoped" else None
        meters.append({
            "provider": "claude", "window": kind, "used_percent": float(limit.get("percent", 0)),
            "resets_at": resets, "scope": scope, "window_cost_tokens": cost, "error": None,
        })
        history.append({"at": now.isoformat(), "provider": "claude", "window": kind + (f":{scope}" if scope else ""), "used_percent": float(limit.get("percent", 0)), "resets_at": resets})
    return meters, history


def merge_history(previous, fresh, now):
    """Union stored and fresh samples, dropping anything older than HISTORY_DAYS."""
    floor = (now - timedelta(days=HISTORY_DAYS)).isoformat()
    merged = {}
    for sample in list(previous or []) + list(fresh):
        if sample["at"] < floor:
            continue
        merged[(sample["provider"], sample["window"], sample["at"][:13])] = sample
    return sorted(merged.values(), key=lambda sample: sample["at"])
