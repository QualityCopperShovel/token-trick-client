from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from token_trick_client.codex import collect_codex, daily_rows


def test_codex_collection_uses_deltas_and_excludes_content(tmp_path):
    session = tmp_path / "session.jsonl"
    events = [
        {"type": "turn_context", "payload": {"model": "gpt-astra", "cwd": "/private/path"}},
        {"timestamp": "2026-08-06T00:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 10, "total_tokens": 110}}}},
        {"type": "turn_context", "payload": {"model": "gpt-sol", "cwd": "/private/path"}},
        {"timestamp": "2026-08-06T00:01:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 150, "cached_input_tokens": 120, "output_tokens": 20, "total_tokens": 170}}}},
    ]
    session.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    rows = daily_rows(collect_codex(str(tmp_path / "**" / "*.jsonl"), datetime(2026, 8, 1, tzinfo=timezone.utc)))
    assert rows == [{
        "date": "2026-08-06", "codex_total": 170, "codex_cached": 120, "codex_fresh_input": 30, "codex_output": 20, "api_total": 0,
        "models": [
            {"model": "gpt-astra", "total": 110, "cached": 80, "fresh_input": 20, "output": 10},
            {"model": "gpt-sol", "total": 60, "cached": 40, "fresh_input": 10, "output": 10},
        ],
    }]
    assert "content" not in json.dumps(rows)
    assert "/private/path" not in json.dumps(rows)


def test_build_payload_merges_claude_rows_and_quota(tmp_path, monkeypatch):
    from token_trick_client import codex as codex_module
    codex_dir = tmp_path / "codex"; codex_dir.mkdir()
    events = [
        {"type": "turn_context", "payload": {"model": "gpt-6-astra"}},
        {"timestamp": "2026-09-06T00:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 10, "total_tokens": 110}}, "rate_limits": {"primary": {"used_percent": 42.0, "window_minutes": 10080, "resets_at": 1789185558}, "plan_type": "pro"}}},
    ]
    (codex_dir / "s.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    claude_dir = tmp_path / "claude"; (claude_dir / "projects" / "p").mkdir(parents=True)
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 500, "output_tokens": 7}
    (claude_dir / "projects" / "p" / "t.jsonl").write_text(json.dumps({"timestamp": "2026-09-05T12:00:00Z", "requestId": "r", "message": {"id": "m", "model": "claude-fable-5-1", "usage": usage}}), encoding="utf-8")
    (claude_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": 0}}), encoding="utf-8")
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    payload = codex_module.build_payload(now, 30, str(codex_dir / "**" / "*.jsonl"), claude_dir,
                                         previous_history=[{"at": "2026-09-01T00:00:00+00:00", "provider": "claude", "window": "weekly_all", "used_percent": 10.0, "resets_at": None}])
    dates = {row["date"]: row for row in payload["days"]}
    assert dates["2026-09-05"]["claude_models"] == [{"model": "claude-fable-5-1", "input": 5, "cache_write": 50, "cache_read": 500, "output": 7, "tiers": {"unknown": {"input": 5, "cache_write": 50, "cache_read": 500, "output": 7}}}]
    assert dates["2026-09-05"]["codex_total"] == 0 and dates["2026-09-06"]["models"][0]["model"] == "gpt-6-astra"
    meters = {(m["provider"], m["window"]): m for m in payload["quota"]["meters"]}
    assert meters[("codex", "weekly")]["used_percent"] == 42.0
    assert meters[("codex", "weekly")]["window_cost_tokens"]["gpt-6-astra"]["cached"] == 80
    # An expired Claude credential is reported, never guessed around.
    assert "expired" in meters[("claude", "weekly_all")]["error"]
    assert meters[("claude", "weekly_all")]["used_percent"] is None
    assert [h["window"] for h in payload["quota"]["history"]] == ["weekly_all", "weekly"]
    assert "accessToken" not in json.dumps(payload) and "x" != json.dumps(payload)


def test_source_history_is_full_window_and_preserves_unknown(monkeypatch):
    from token_trick_client import codex as module
    usage = {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 10, 'total_tokens': 110}
    turns = [
        {'at': datetime(2026, 9, 1, tzinfo=timezone.utc), 'model': 'model-a', 'usage': usage, 'rate_limits': None, 'call': {'source': 'CLI'}},
        {'at': datetime(2026, 9, 1, tzinfo=timezone.utc), 'model': 'model-b', 'usage': usage, 'rate_limits': None, 'call': {'source': 'FairyStack'}},
        {'at': datetime(2026, 9, 1, tzinfo=timezone.utc), 'model': 'model-a', 'usage': usage, 'rate_limits': None, 'call': None},
    ]
    monkeypatch.setattr(module, 'codex_turn_usage', lambda *_, **kwargs: iter(turns))
    value = module.build_payload(datetime(2026, 9, 8, tzinfo=timezone.utc), 30, 'unused', None)
    assert value['calls'] == []
    day = value['days'][0]
    assert {g['source'] for g in day['codex_sources']} == {'CLI', 'FairyStack', 'Unknown'}
    assert sum(m['total'] for g in day['codex_sources'] for m in g['models']) == day['codex_total'] == 330


def test_tier_projection_preserves_all_numeric_totals():
    from datetime import datetime, timezone
    from token_trick_client.codex import attach_tiers, daily_rows, model_rows
    from coding_agent_sessions import daily_by_model, TOKEN_FIELDS
    events=[{'at':datetime(2026,9,8,tzinfo=timezone.utc),'model':'m','service_tier':tier,'usage':{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2,'total_tokens':12}} for tier in ['fast','standard','unknown']]
    rows=daily_rows(daily_by_model(events,TOKEN_FIELDS))
    attach_tiers(rows,events,TOKEN_FIELDS,model_rows)
    model=rows[0]['models'][0]
    assert model['total']==36
    assert model['tiers']['fast']=={'total':12,'cached':4,'fresh_input':6,'output':2}
    for field in ('total','cached','fresh_input','output'):
        assert sum(part[field] for part in model['tiers'].values())==model[field]


def test_sessions_reconcile_tokens_tiers_and_runtime():
    from token_trick_client.codex import attach_sessions,empty_day
    now=datetime(2026,9,8,tzinfo=timezone.utc)
    events=[{'at':now,'session':session,'model':'m','source':'CLI','service_tier':'fast','usage':{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2,'total_tokens':12}} for session in ['one','two']]
    runtime=[{'at':now,'session':'one','model':'m','source':'CLI','duration_ms':100}]
    rows={'2026-09-08':empty_day('2026-09-08')}
    attach_sessions(rows,events,[],runtime)
    sessions=rows['2026-09-08']['sessions']
    assert sum(s['models'][0]['total'] for s in sessions)==24
    assert sessions[0]['models'][0]['tiers']['fast']['total']==12
    assert sessions[0]['runtime']==[{'model':'m','duration_ms':100,'turns':1}]


def test_session_efforts_cover_history_models_and_switches(monkeypatch):
    from token_trick_client import codex as module
    at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    events = [{'at': at, 'session': 'same', 'model': model, 'source': 'CLI',
               'effort': effort, 'usage': {'input_tokens': 10, 'cached_input_tokens': 4,
               'output_tokens': 2, 'total_tokens': 12}, 'rate_limits': None}
              for model, effort in [('astra','medium'),('astra','xhigh'),('sol','low'),('sol','unknown')]]
    monkeypatch.setattr(module, 'codex_turn_usage', lambda *_, **kwargs: iter(events))
    data = module.build_payload(datetime(2026, 9, 8, tzinfo=timezone.utc), 90, 'unused', None)
    assert data['calls'] == [], 'Session effort must survive the two-day response cutoff'
    session = data['days'][0]['sessions'][0]
    assert session['efforts'] == {'astra': ['medium','xhigh'], 'sol': ['low','unknown']}
    assert sum(m['total'] for m in session['models']) == 48



def test_request_distribution_never_sums_calls_within_a_turn():
    from token_trick_client.codex import session_request_summaries
    def event(turn, tokens, native=True):
        return {'session': 's', 'call': {'turn': turn} if native else None, 'usage': {'total_tokens': tokens}}
    events = [event('one', 1), event('two', 100000), event('two', 172000),
              event('three', 272000), event('three', 272001), event('four', 0), event(None, 99, False)]
    runtime = [{'provider': 'codex', 'session': 's', 'turn_id': 'two'},
               {'provider': 'codex', 'session': 's', 'turn_id': 'no-usage'}]
    result = session_request_summaries(events, [], runtime, [{'session':'s'}]*6)[('codex','s')]
    bins = [0]*32
    for index in [0,11,20,31]: bins[index] += 1
    assert result == {'turns':5,'requests':6,'bins':bins,'overflow':1,'zero_tokens':1,'unattributed_tokens':99,'compactions':6,'median_tokens':136000,'timeline':None}
    claude = session_request_summaries([], [{'session':'c','usage':{'input':10,'cache_write':20,'cache_read':30,'output':40}}], [], [])[('claude','c')]
    assert claude['requests'] == 1 and claude['bins'][0] == 1 and claude['compactions'] is None


def test_request_histogram_survives_detail_cutoff_and_spans_days(monkeypatch):
    from token_trick_client import codex as module
    events=[]
    for day in [1,2]:
        for index in range(3):
            at=datetime(2026,8,day,tzinfo=timezone.utc)
            usage={'input_tokens':100000,'cached_input_tokens':90000,'output_tokens':1000,'total_tokens':101000}
            events.append({'at':at,'session':'same','source':'CLI','model':'m','usage':usage,'rate_limits':None,
                           'call':{'id':f'{day}-{index}','turn':str(day),'at':at.isoformat(),'usage':usage}})
    def parse(*_, **kwargs):
        kwargs['compaction']({'session':'same','at':datetime(2026,8,2,tzinfo=timezone.utc)})
        return iter(events)
    monkeypatch.setattr(module,'codex_turn_usage',parse)
    payload=module.build_payload(datetime(2026,9,8,tzinfo=timezone.utc),90,'unused',None)
    assert payload['calls']==[]
    summaries=[day['sessions'][0]['request_summary'] for day in payload['days']]
    assert summaries[0]==summaries[1]
    assert summaries[0]['turns']==2 and summaries[0]['requests']==6 and summaries[0]['compactions']==1
    assert summaries[0]['bins'][11]==6 and summaries[0]['overflow']==0
    assert summaries[0]['median_tokens']==101000 and len(summaries[0]['timeline']['points'])==6
    assert all('turn_summary' not in day['sessions'][0] for day in payload['days'])


def test_exact_median_includes_zero_and_overflow_without_bin_estimation():
    from token_trick_client.codex import session_request_summaries
    def summarize(values):
        events=[{'session':'s','call':{'turn':'t'},'usage':{'total_tokens':n}} for n in values]
        return session_request_summaries(events,[],[{'session':'s','turn_id':'t'}],[])[('codex','s')]
    assert summarize([1,2])['median_tokens']==1.5
    assert summarize([0,5,900000])['median_tokens']==5
    assert summarize([300001,400000])['median_tokens']==350000.5
    assert summarize([])['median_tokens'] is None


def test_request_timeline_preserves_time_endpoints_and_extremes():
    from token_trick_client.codex import request_timeline
    from datetime import timedelta
    start=datetime(2026,9,8,tzinfo=timezone.utc)
    points=[(start+timedelta(seconds=i*i),i+1000) for i in range(1000)]
    points[111]=(points[111][0],999999)
    points[112]=(points[112][0],0)
    trace=request_timeline(points[::-1])
    assert len(trace['points'])<=64
    assert trace['points'][0]==[0,1000]
    assert trace['points'][-1]==[999*999*1000,1999]
    assert [111*111*1000,999999] in trace['points']
    assert [112*112*1000,0] in trace['points']
    assert trace['points']==sorted(trace['points'],key=lambda p:p[0])
    assert request_timeline([(None,12)]) is None
    short=request_timeline([(start+timedelta(microseconds=999),2),(start+timedelta(microseconds=1001),3)])
    assert short['points']==[[0,2],[1,3]]
