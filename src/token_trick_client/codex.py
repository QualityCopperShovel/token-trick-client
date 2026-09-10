"""Token Trick projection over shared local coding-agent session events."""

from datetime import timedelta

from coding_agent_sessions import CLAUDE_TOKEN_FIELDS, TOKEN_FIELDS, claude_request_usage, codex_turn_usage, daily_by_model

from .quota import claude_meters, claude_model_rows, codex_meters, merge_history


def collect_codex(session_glob, cutoff):
    return daily_by_model(codex_turn_usage(session_glob, cutoff), TOKEN_FIELDS)


def model_rows(models):
    rows = [{
        "model": model,
        "total": fields["total_tokens"],
        "cached": fields["cached_input_tokens"],
        "fresh_input": fields["input_tokens"] - fields["cached_input_tokens"],
        "output": fields["output_tokens"],
    } for model, fields in models.items()]
    return sorted(rows, key=lambda row: (-row["total"], row["model"]))


def daily_rows(daily):
    rows = []
    for date in sorted(daily):
        models = model_rows(daily[date])
        rows.append({
            "date": date,
            "codex_total": sum(row["total"] for row in models),
            "codex_cached": sum(row["cached"] for row in models),
            "codex_fresh_input": sum(row["fresh_input"] for row in models),
            "codex_output": sum(row["output"] for row in models),
            "api_total": 0,
            "models": models,
        })
    return rows


def empty_day(date):
    return {"date": date, "codex_total": 0, "codex_cached": 0, "codex_fresh_input": 0, "codex_output": 0, "api_total": 0, "models": [], "claude_models": []}


def attach_tiers(rows, events, fields, project):
    """Project the same counted events into reconciled per-model pricing tiers."""
    grouped = {}
    for event in events:
        tier = event.get("service_tier", "unknown")
        grouped.setdefault(tier, []).append(event)
    index = {r["date"]: {m["model"]: m for m in r["models"]} for r in rows}
    for tier, items in grouped.items():
        for date, models in daily_by_model(items, fields).items():
            for model in project(models):
                index[date][model["model"]].setdefault("tiers", {})[tier] = {k:v for k,v in model.items() if k != "model"}


def attach_sessions(by_date, turns, requests, runtime):
    groups = {}
    def group(date, session, provider, source):
        source = source if source in {"FairyStack", "CLI", "Other"} else "Unknown"
        key = (date, session, provider, source)
        return groups.setdefault(key, {"session": session, "provider": provider, "source": source, "models": [], "runtime": [], "events": []})
    for provider, events, fields, project in [("codex", turns, TOKEN_FIELDS, model_rows), ("claude", requests, CLAUDE_TOKEN_FIELDS, claude_model_rows)]:
        for event in events:
            session = event.get("session")
            if not session or not any(event["usage"].values()):
                continue
            group(event["at"].date().isoformat(), session, provider, event.get("source"))["events"].append(event)
        for (date, _, kind, _), item in list(groups.items()):
            if kind != provider:
                continue
            events = item["events"]
            models = daily_by_model(events, fields)[date]
            row = {"date": date, "models": project(models)}
            attach_tiers([row], events, fields, project)
            item["models"] = row["models"]
    for event in runtime:
        if not event.get("session"):
            continue
        item = group(event["at"].date().isoformat(), event["session"], event.get("provider", "codex"), event["source"])
        timing = next((t for t in item["runtime"] if t["model"] == event["model"]), None)
        if timing is None:
            timing = {"model": event["model"], "duration_ms": 0, "turns": 0}
            item["runtime"].append(timing)
        timing["duration_ms"] += event["duration_ms"]
        timing["turns"] += 1
    for (date, _, _, _), item in groups.items():
        item.pop("events")
        by_date[date].setdefault("sessions", []).append(item)


def build_payload(now, days, sessions_glob, claude_dir, previous_history=None, claude_timeout=15, evidence=None, claude_evidence=None):
    """Assemble the token-usage payload: Codex and Claude daily rows plus quota meters."""
    cutoff = now - timedelta(days=days)
    runtime = []
    turns = list(codex_turn_usage(sessions_glob, cutoff, evidence=evidence, runtime=runtime.append))  # one scan feeds rows, meters and window costs
    by_date = {row["date"]: {**row, "claude_models": []} for row in daily_rows(daily_by_model(turns, TOKEN_FIELDS))}
    attach_tiers(list(by_date.values()), turns, TOKEN_FIELDS, model_rows)
    # Aggregate before truncating response detail: source history spans the full ledger.
    source_turns = {}
    for turn in turns:
        if not any(turn["usage"].values()):
            continue
        source = turn.get("source") or (turn.get("call") or {}).get("source", "Unknown")
        if source not in {"FairyStack", "CLI", "Other"}:
            source = "Unknown"
        source_turns.setdefault(source, []).append(turn)
    for row in by_date.values():
        row["codex_sources"] = []
    for source, items in sorted(source_turns.items()):
        source_rows = daily_rows(daily_by_model(items, TOKEN_FIELDS))
        attach_tiers(source_rows, items, TOKEN_FIELDS, model_rows)
        for row in source_rows:
            by_date[row["date"]]["codex_sources"].append({"source": source, "models": row["models"]})
    claude_dir = None if claude_dir is None else str(claude_dir)
    requests = list(claude_request_usage(f"{claude_dir}/projects", cutoff, evidence=claude_evidence, runtime=runtime.append)) if claude_dir else []
    for date, models in daily_by_model(requests, CLAUDE_TOKEN_FIELDS).items():
        by_date.setdefault(date, empty_day(date))["claude_models"] = claude_model_rows(models)
    attach_tiers([{"date": d["date"], "models": d["claude_models"]} for d in by_date.values()], requests, CLAUDE_TOKEN_FIELDS, claude_model_rows)
    durations = {}
    for event in runtime:
        date = event["at"].date().isoformat()
        source = event["source"] if event["source"] in {"FairyStack", "CLI", "Other"} else "Unknown"
        key = (date, event["model"], source, event.get("provider", "codex"))
        item = durations.setdefault(key, {"model": event["model"], "source": source, "duration_ms": 0, "turns": 0})
        item["duration_ms"] += event["duration_ms"]
        item["turns"] += 1
    for (date, _, _, provider), item in durations.items():
        by_date.setdefault(date, empty_day(date)).setdefault("claude_runtime" if provider == "claude" else "runtime", []).append(item)
    attach_sessions(by_date, turns, requests, runtime)
    codex, codex_history = codex_meters(turns, now)
    if claude_dir:
        claude, claude_history = claude_meters(f"{claude_dir}/.credentials.json", requests, now, timeout=claude_timeout)
    else:
        claude, claude_history = [], []
    return {
        "collected_at": now.isoformat(), "window_days": days,
        "days": [by_date[date] for date in sorted(by_date)],
        "calls": sorted({t["call"]["id"]: t["call"] for t in turns if t.get("call") and t["at"] >= now - timedelta(days=2)}.values(), key=lambda c: c["at"])[-5000:],
        "quota": {
            "sampled_at": now.isoformat(), "meters": codex + claude,
            "history": merge_history(previous_history, codex_history + claude_history, now),
        },
    }
