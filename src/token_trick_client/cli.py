"""Token Trick CLI."""

import argparse
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import NamedTemporaryFile

import requests

from .codex import collect_codex, daily_rows

API_URL = os.environ.get("TOKEN_TRICK_API_URL", "https://tokentrick.com/api/v1/ledger")
VERIFY_URL = "https://authreturn.com/api/keys/verify"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "token-trick" / "config.json"
SESSIONS_GLOB = str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions" / "**" / "*.jsonl")


def atomic_private_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        json.dump(payload, output)
        output.flush()
        os.fsync(output.fileno())
        temp = output.name
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def load_key(path):
    with path.open(encoding="utf-8") as config_file:
        key = json.load(config_file).get("api_key")
    if not isinstance(key, str) or not key.startswith("ar_"):
        raise ValueError("Token Trick API key is missing; run `token-trick setup`")
    return key


def setup(args):
    key = getpass.getpass("Token Trick API key: ").strip()
    response = requests.post(VERIFY_URL, json={"key": key, "app_slug": "token-trick"}, timeout=10)
    body = response.json()
    if response.status_code != 200 or body.get("valid") is not True:
        raise RuntimeError(body.get("error") or f"Key verification failed ({response.status_code})")
    if not body.get("user_email"):
        raise RuntimeError("Key verification omitted user_email")
    atomic_private_json(args.config, {"api_key": key})
    if not args.no_schedule:
        configure_schedule(sys.argv[0])
    print(f"Token Trick configured for {body['user_email']}")


def collect(args):
    now = datetime.now(timezone.utc)
    rows = daily_rows(collect_codex(args.sessions, now - timedelta(days=args.days)))
    response = requests.post(
        API_URL,
        json={"collected_at": now.isoformat(), "window_days": args.days, "days": rows},
        headers={"Authorization": f"Bearer {load_key(args.config)}"},
        timeout=20,
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("Token Trick returned malformed JSON") from exc
    if response.status_code != 200 or body.get("status") != "completed":
        raise RuntimeError(body.get("error") or f"Upload failed ({response.status_code})")
    print(f"Uploaded {body['day_count']} daily rows")


def configure_schedule(executable):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Automatic scheduling currently supports Linux systemd; run `token-trick collect` daily on this platform")
    user_dir = Path.home() / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True, exist_ok=True)
    command = shlex.join([str(Path(executable).resolve()), "collect"])
    (user_dir / "token-trick-client.service").write_text(
        "[Unit]\nDescription=Collect Token Trick usage\n\n[Service]\nType=oneshot\n" f"ExecStart={command}\nTimeoutStartSec=120\n",
        encoding="utf-8",
    )
    (user_dir / "token-trick-client.timer").write_text(
        "[Unit]\nDescription=Daily Token Trick collection\n\n[Timer]\nOnCalendar=daily\nPersistent=true\nRandomizedDelaySec=300\nUnit=token-trick-client.service\n\n[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
    subprocess.run(["systemctl", "--user", "enable", "--now", "token-trick-client.timer"], check=True, timeout=15)


def parser():
    result = argparse.ArgumentParser(prog="token-trick")
    commands = result.add_subparsers(dest="command", required=True)
    setup_command = commands.add_parser("setup")
    setup_command.add_argument("--config", type=Path, default=CONFIG_PATH)
    setup_command.add_argument("--no-schedule", action="store_true")
    setup_command.set_defaults(func=setup)
    collect_command = commands.add_parser("collect")
    collect_command.add_argument("--config", type=Path, default=CONFIG_PATH)
    collect_command.add_argument("--sessions", default=SESSIONS_GLOB)
    collect_command.add_argument("--days", type=int, choices=range(1, 366), default=90)
    collect_command.set_defaults(func=collect)
    return result


def main():
    args = parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, RuntimeError, requests.RequestException, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
