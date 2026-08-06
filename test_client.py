from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from token_trick_client.codex import collect_codex, daily_rows


def test_codex_collection_uses_deltas_and_excludes_content(tmp_path):
    session = tmp_path / "session.jsonl"
    events = [
        {"timestamp": "2026-08-06T00:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 10, "total_tokens": 110}}}},
        {"timestamp": "2026-08-06T00:01:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 150, "cached_input_tokens": 120, "output_tokens": 20, "total_tokens": 170}}}},
    ]
    session.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    rows = daily_rows(collect_codex(str(tmp_path / "**" / "*.jsonl"), datetime(2026, 8, 1, tzinfo=timezone.utc)))
    assert rows == [{"date": "2026-08-06", "codex_total": 170, "codex_cached": 120, "codex_fresh_input": 30, "codex_output": 20, "api_total": 0}]
    assert "content" not in json.dumps(rows)
