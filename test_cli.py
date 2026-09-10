from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from token_trick_client import cli, quota


def test_no_local_usage_does_not_erase_remote_ledger(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    cli.atomic_private_json(config, {'api_key': 'ar_test'})
    monkeypatch.setattr(cli, 'build_payload', lambda *a: {'days': []})
    monkeypatch.setattr(cli.requests, 'post', lambda *a, **kw: pytest.fail('Empty collection must not upload'))
    args = SimpleNamespace(config=config, days=90, sessions='missing', no_claude=True)
    with pytest.raises(RuntimeError, match='no data was uploaded'):
        cli.collect(args)


def test_quota_failure_does_not_upload_local_paths(tmp_path):
    secret_path = tmp_path / 'private-project-name' / 'credentials.json'
    meters, _ = quota.claude_meters(secret_path, [], datetime.now(timezone.utc))
    assert 'unavailable' in meters[0]['error']
    assert str(tmp_path) not in json.dumps(meters)
    assert 'private-project-name' not in json.dumps(meters)


def test_custom_configuration_survives_scheduling(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.sys, 'platform', 'linux')
    monkeypatch.setattr(cli.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **kw: None)
    config = tmp_path / 'custom-config.json'
    cli.configure_schedule('/opt/token-trick', config)
    service = (tmp_path / '.config/systemd/user/token-trick-client.service').read_text()
    assert '--config ' + str(config) in service
    assert 'TimeoutStartSec=130' in service


def test_stuck_collection_process_is_killed_by_deadline(tmp_path, monkeypatch):
    package = tmp_path / 'token_trick_client'
    package.mkdir()
    (package / '__init__.py').write_text('')
    pidfile = tmp_path / 'pid'
    (package / 'cli.py').write_text('import os, time\ndef main():\n open(os.environ["TEST_PID_FILE"], "w").write(str(os.getpid()))\n time.sleep(30)\n')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setenv('TEST_PID_FILE', str(pidfile))
    with pytest.raises(subprocess.TimeoutExpired):
        cli.run_collection_worker(['collect'], timeout=.5)
    assert pidfile.exists()
    if os.name == 'posix':
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)


def test_collection_timeout_has_a_terminal_message(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, 'argv', ['token-trick', 'collect'])
    monkeypatch.delenv('TOKEN_TRICK_COLLECTION_WORKER', raising=False)
    monkeypatch.setattr(cli, 'run_collection_worker', lambda *a: (_ for _ in ()).throw(subprocess.TimeoutExpired('worker', 120)))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    assert 'worker was stopped' in capsys.readouterr().err
