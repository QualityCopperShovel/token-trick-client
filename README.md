# Token Trick Client

Local Codex and Claude Code usage collector for [Token Trick](https://tokentrick.com).
See tokens, cached input, estimated cost, recorded runtime and reasoning effort in
your own private account. Public beta; Python 3.10+ required.

## Connect

1. [Create an account and client key](https://tokentrick.com/#connect).
2. Install with [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html):

```bash
pipx install https://tokentrick.com/downloads/token_trick_client-0.15.0.tar.gz
token-trick setup --no-schedule
token-trick collect
```

Paste the key into the hidden setup prompt. Each collection has a 120-second
limit. Missing local usage fails without replacing your existing ledger.
Return to Token Trick and refresh after upload. Repeat `token-trick collect`
when you want a fresh snapshot. To update an existing pipx installation:

```bash
pipx install --force https://tokentrick.com/downloads/token_trick_client-0.15.0.tar.gz
```

One computer per Token Trick account: **each upload replaces the previous
snapshot; uploads from multiple computers are not merged**. This collector reads
local logs, not your provider account's complete history across devices.

On Linux with systemd, `token-trick setup` also enables daily collection. Inspect
it with `systemctl --user status token-trick-client.timer` and
`journalctl --user -u token-trick-client.service`. Stop automatic collection with
`systemctl --user disable --now token-trick-client.timer`.
Use `--no-schedule` on macOS and Windows; manual collection on those platforms has
not yet been verified end to end. Codex and Claude Code must run locally or have
local logs available. Web-only provider usage is not collected.

## Data sent and kept private

The open-source, MIT-licensed `coding-agent-sessions` parser runs locally.
The upload contains daily/model token counts, cache counts, explicit runtime,
quota readings, client and service tier, and opaque session/turn/response IDs.
Recent response metadata includes timestamps, reasoning effort, compaction counts
and character counts. **Prompts, response text, tool contents, local paths,
repository names and raw session files are not uploaded.**

A Token Trick client key is saved to the user's configuration file with mode
0600 on POSIX systems. Claude's existing OAuth credential is read locally only to
request its own quota meter, and is never uploaded to Token Trick. Missing quota
access is reported as unavailable. To omit Claude logs and its quota request:

```bash
token-trick collect --no-claude
```

The client uploads over HTTPS to Agent Telemetry's Token Trick-scoped API. The
verified key owner determines the destination account. Revoke or replace your
key through [Connect](https://tokentrick.com/#connect); replacement requires
running setup again. Revoking stops future uploads but does not delete the ledger.

## Coverage

- Default local log window: 90 days; the chart displays up to 30 days.
- Recent response diagnostics: at most two days and 5,000 responses, depending on log format.
- Runtime: explicit recorded timings, including tools; overlapping turns add together.
- Estimated dollars use model list prices; they are not subscription bills.
- Raw source-file inspection and organization-wide OpenAI API collection are server-collector features.
- Effort comparisons do not measure completed-task quality or allocate subscription quota by effort.

`CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `--sessions`, `--claude-dir`, `--days` and
`--config` select local inputs. Inspect options with `token-trick collect --help`.
Provider credentials, prompts and raw logs should never be attached to an issue.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Daily Codex session metadata includes recorded effort levels per model, including
sessions that changed effort. Unknown effort stays explicit. This covers the full
collection window independently of the two-day response sample.
