# Token Trick Client

Collects daily token totals from local Codex JSONL telemetry and uploads only aggregate counts to [Token Trick](https://tokentrick.com). It never uploads prompts, responses, filenames, repository names, usernames, hostnames, or raw session records.

```bash
pipx install git+https://github.com/QualityCopperShovel/token-trick-client.git@v0.3.0
token-trick setup
```

Create a client key from the signed-in Token Trick dashboard, then paste it into setup. Linux setup installs a bounded daily user-level systemd timer. Run `token-trick collect` manually at any time.

The client is MIT licensed. Its parser is product-specific; scheduling and credential storage follow Kaomojo's contract but have not been extracted into a shared dependency while only two consumers exist.

The client authenticates with a Token Trick key while sending its typed aggregate payload to the independent Agent Telemetry service. Token Trick accounts and browser sessions remain separate from the infrastructure service. Local Codex parsing comes from the network-free, MIT-licensed `coding-agent-sessions` package shared with Kaomojo.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```
