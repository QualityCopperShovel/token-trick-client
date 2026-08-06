"""Privacy-preserving Codex token aggregation."""

from collections import defaultdict
from datetime import datetime
import glob
import json

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")


def collect_codex(session_glob, cutoff):
    daily = defaultdict(lambda: defaultdict(int))
    for path in glob.iglob(str(session_glob), recursive=True):
        previous = None
        with open(path, encoding="utf-8") as session_file:
            for line in session_file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload", {})
                if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                usage = (payload.get("info") or {}).get("total_token_usage")
                if not isinstance(usage, dict) or "total_tokens" not in usage:
                    continue
                timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                delta = {field: max(0, int(usage.get(field, 0)) - int((previous or {}).get(field, 0))) for field in FIELDS}
                previous = usage
                if timestamp >= cutoff:
                    for field, value in delta.items():
                        daily[timestamp.date().isoformat()][field] += value
    return daily


def daily_rows(daily):
    return [{
        "date": date,
        "codex_total": daily[date]["total_tokens"],
        "codex_cached": daily[date]["cached_input_tokens"],
        "codex_fresh_input": daily[date]["input_tokens"] - daily[date]["cached_input_tokens"],
        "codex_output": daily[date]["output_tokens"],
        "api_total": 0,
    } for date in sorted(daily)]
