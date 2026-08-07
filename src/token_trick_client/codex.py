"""Token Trick projection over shared local coding-agent session events."""

from coding_agent_sessions import codex_token_totals


def collect_codex(session_glob, cutoff):
    return codex_token_totals(session_glob, cutoff)


def daily_rows(daily):
    return [{
        "date": date,
        "codex_total": daily[date]["total_tokens"],
        "codex_cached": daily[date]["cached_input_tokens"],
        "codex_fresh_input": daily[date]["input_tokens"] - daily[date]["cached_input_tokens"],
        "codex_output": daily[date]["output_tokens"],
        "api_total": 0,
    } for date in sorted(daily)]
