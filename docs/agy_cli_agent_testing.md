# Antigravity (agy) CLI Evaluation Guide

This guide covers how to use EvalBench for evaluating Antigravity CLI (`agy`)
agent workflows using **MCP Servers** and **Skills**. It mirrors the structure
of [`gemini_cli_agent_testing.md`](gemini_cli_agent_testing.md) and only calls
out where the two harnesses differ.

> **Status:** the agy CLI surface targeted here was verified against the
> v1.0.5 binary (the self-updating installer pulls the latest). Model
> selection is passed via agy's `--model` flag, and the value must be the
> exact agy UI label (see the model-selection note below and the comment in
> `datasets/model_configs/agy_cli_model.yaml`).

> [!IMPORTANT]
> **First-run auth:** agy uses an OAuth consumer flow backed by the system
> keyring (file-backed under SSH). Before evals can run, complete `agy`
> login interactively at least once on the host so a refreshable token
> exists. After that, the harness can run non-interactively.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
  - [Run Configuration](#1-run-configuration)
  - [Model Configuration](#2-model-configuration)
  - [Evaluation Dataset (Evalset)](#3-evaluation-dataset-evalset)
- [Tool Paradigms](#tool-paradigms)
  - [MCP Servers](#mcp-servers)
  - [Skills](#skills)
  - [Fake MCP Servers (Testing)](#fake-mcp-servers-testing)
- [Differences vs. Gemini CLI](#differences-vs-gemini-cli)
- [Scorers](#scorers)
- [Reporting](#reporting)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's agy CLI integration enables automated, multi-turn evaluation of
agentic workflows that run on the Antigravity CLI binary. Same evaluator,
same scorers, same evalset format as the Gemini CLI guide -- the generator
just shells out to the `agy` binary instead of `npm exec @google/gemini-cli`.

### Key Capabilities

- **Multi-turn evaluation** with LLM-powered simulated users
- **Two tool paradigms** today: MCP servers and skills (agy does not expose a
  Gemini-CLI-style extension manager)
- **Fake MCP server support** for deterministic, offline testing
- **Same 8 built-in scorers** as Gemini CLI
- **CSV and BigQuery reporting**

---

## Architecture

Identical to the Gemini CLI flow; only the generator class changes:

```
Run Config -> AgentOrchestrator -> AgentEvaluator -> AgyCliGenerator -> agy
                                                       |
                                                       v
                                              MCP servers / skills
```

The dispatch keyword in `evaluator/__init__.py` is still `geminicli`; that
string covers all agent-style CLI generators (gemini_cli, claude_code,
codex_cli, agy_cli). The concrete CLI is chosen via `model_config`.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed using `uv`:
   ```bash
   cd evalbench
   uv sync
   ```

2. **Antigravity CLI installed and on `PATH`**:
   ```bash
   curl -fsSL https://antigravity.google/cli/install.sh | sh -s -- --dir ~/.local/bin
   export PATH="$HOME/.local/bin:$PATH"
   agy --version  # sanity check
   ```

   The installer writes a SHA-512-verified native binary; it self-updates in
   the background and does not expose a pinning flag. The Docker image at
   `evalbench_service/Dockerfile` does the equivalent install into
   `/usr/local/bin`.

3. **GCP Authentication** (for Vertex AI models and MCP servers):
   ```bash
   gcloud auth application-default login
   ```

4. **Environment Variables**:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=us-central1
   ```

---

## Quick Start

### 1. Set the run configuration

```bash
# For MCP Server evaluation:
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_config.yaml

# For Skills evaluation:
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_skills_config.yaml

# For Fake MCP (offline testing):
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_fake_config.yaml
```

### 2. Run the evaluation

```bash
./evalbench/run.sh
```

---

## Configuration Reference

### 1. Run Configuration

For agy CLI, set `orchestrator: agent` (the modern agent-CLI keyword,
shared with `claude_code` and `codex_cli`) and
`dataset_format: agent-format`. The legacy `geminicli` /
`gemini-cli-format` values still work -- both route to
`AgentOrchestrator` -- but the `agent*` names are the right ones for
non-gemini CLIs.

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | `agent-format` (recommended) or the legacy `gemini-cli-format` |
| `orchestrator` | Yes | `agent` (recommended) or the legacy `geminicli` |
| `model_config` | Yes | Path to the agy CLI model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations |
| `reporting` | Optional | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](/datasets/agy-cli-tools/example_run_config.yaml)):

```yaml
dataset_config: datasets/agy-cli-tools/agy-cli.evalset.json
dataset_format: agent-format
orchestrator: agent
model_config: datasets/model_configs/agy_cli_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml

scorers:
  trajectory_matcher: {}
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  turn_count: {}

reporting:
  csv:
    output_directory: 'results'
```

---

### 2. Model Configuration

| Key | Required | Description |
|-----|----------|-------------|
| `generator` | Yes | Must be `agy_cli` |
| `model` | Optional | agy UI model label, e.g. `"Gemini 3.1 Pro (High)"`. Passed via the `--model` flag. Must be a valid label, not an API id. |
| `env` | Optional | Environment variables passed to the CLI process |
| `setup` | Optional | Tool setup block containing `mcp_servers`, `skills`, or `fake_mcp_servers` |

> [!NOTE]
> **Model selection uses the `--model` flag; use a UI label.** The harness
> passes the configured `model` via agy's `--model` flag (agy >=1.0.5). The
> value must be the **exact agy UI label** (e.g. `"Gemini 3.1 Pro (High)"`,
> `"Gemini 3.5 Flash (Medium)"`), not an API id like `gemini-2.5-pro` -- an
> unrecognized value is silently ignored and agy falls back to its default
> model. List the valid labels with `agy models`. A non-interactive `agy -p`
> run honors the label (the backend log shows `Propagating selected model
> override to backend: label="..."`), even though the unrelated
> `FetchAvailableModels` poll may fail on project-auth accounts. Omit the key
> to leave the flag off, so agy uses its own default model. Note: the
> transcript never echoes the real model, so the EvalBench stats bucket is
> keyed by the configured label (or `agy` when unset).

---

### 3. Evaluation Dataset (Evalset)

Same format as Gemini CLI. See the [Gemini guide's evalset section](gemini_cli_agent_testing.md#3-evaluation-dataset-evalset)
for full field reference; the same `expected_trajectory` canonical form
(`<server>__<tool>`) applies. The `agy-cli-tools/` directory ships copies of
the Gemini Cloud SQL evalsets so the two harnesses score against an
identical baseline.

---

## Tool Paradigms

### MCP Servers

Configured under `setup.mcp_servers` in the model config. EvalBench writes
the block under the `mcpServers` key of a sandboxed
`<fake_home>/.gemini/config/mcp_config.json` (a separate file from
`settings.json`; both path and key are confirmed from the v1.0.5 binary's
load-error string and `json:"mcpServers"` struct tag) and lets agy pick it
up at startup.

> [!IMPORTANT]
> **agy's HTTP transport field is `serverUrl`, not `httpUrl`.** This is the
> Windsurf/cortex lineage; the binary has no `httpUrl` field, so a
> Gemini-style `httpUrl` is parsed to a nil URL and the server attaches with
> **no transport and zero tools** -- a silent failure that makes the agent
> fall back to `gcloud` shell-outs. EvalBench auto-translates a `httpUrl`
> alias to `serverUrl` (`_translate_mcp_config`), but prefer writing
> `serverUrl` directly. `authProviderType`, `oauth.scopes`, and `headers`
> are native agy fields, so Google auth works without Bearer-header
> injection (unlike `claude_code`).

Unlike older notes, the harness **does** pre-verify attach: at setup it runs
a short `agy -p` probe, then confirms each configured server discovered
tools by checking that agy wrote per-tool schema files to
`<appDataDir>/mcp/<server>/*.json` (the lazy-load schema cache that
`call_mcp_tool` reads). A server that attaches no tools raises a `RuntimeError`
with the offending server name rather than silently degrading. See
`_verify_mcp_runtime` in `agy_cli.py`.

### Skills

> [!NOTE]
> The field is named `setup.skills` for parity with the `claude_code` and
> `codex_cli` harnesses, which use the same key. For agy each entry is
> installed as a **plugin** (`agy plugin install`), and a plugin bundle may
> carry skills *and* its own MCP servers. The separate top-level
> `setup.mcp_servers` block is for attaching a **standalone** MCP server (by
> URL/stdio) that is not packaged in a plugin -- the two are distinct attach
> paths and are configured independently.

Configured under `setup.skills`. Skills are delivered via **plugins**:
verified against agy v1.0.5, `agy plugin install <target>` reads a plugin
manifest (Claude/Gemini/Codex formats), processes any bundled skills,
materializes them under `<HOME>/.gemini/config/plugins/<name>/`, and
records the install in `<HOME>/.gemini/config/import_manifest.json`. There
is no `agy skills` subcommand, and dropping `SKILL.md` folders on disk
registers nothing (`agy plugin list` stays empty). The harness therefore
shells out to `agy plugin install` for every entry. Two input shapes are
supported:

```yaml
setup:
  skills:
    # String form: an install target passed straight to
    # `agy plugin install`. May be a local plugin directory, a
    # `plugin@marketplace` spec, or a git URL (cloned first).
    - "cloud-sql-postgresql@gemini-cli-extensions"

    # Dict form: same, via an explicit target. Git URLs (scheme:// or
    # trailing .git) are cloned first, then the clone dir is installed;
    # local paths and marketplace specs are installed in place. `url:`
    # is conventional; `path:` is accepted as a synonym. Append
    # `#<branch-or-tag>` to a git URL to pin a version -- the clone uses
    # `git clone --branch`, which resolves branch and tag names only, not
    # raw commit SHAs.
    - action: install_from_repo
      url: "https://github.com/gemini-cli-extensions/cloud-sql-postgresql.git#v1.2.3"
```

> [!NOTE]
> Legacy dict actions (`link`, `install`, `enable`, `disable`,
> `uninstall`) that the gemini-cli generator supports are **not**
> supported here. Use a string target or `install_from_repo`.
> Unsupported entries are logged and skipped.

### Fake MCP Servers (Testing)

Identical setup to Gemini CLI -- a stdio MCP server defined in
`setup.fake_mcp_servers` with tool definitions in the top-level
`fake_mcp_tools` block. See
[`datasets/model_configs/agy_cli_fake_model.yaml`](../datasets/model_configs/agy_cli_fake_model.yaml)
for a working example.

---

## Differences vs. Gemini CLI

| Area | Gemini CLI | Antigravity (agy) |
|------|-----------|--------------------|
| Install | `npm install -g @google/gemini-cli@<ver>` | `curl install.sh \| sh -- --dir <bin>` |
| Version pinning | NPM specifier in `gemini_cli_version` | None exposed; binary self-updates |
| Invocation | `npm exec --yes @google/gemini-cli@<ver> -- ...` | `agy ...` (bare binary) |
| Non-interactive flag | `--yolo` / `--prompt` | `--dangerously-skip-permissions` and `-p` (alias `--print`) |
| Output format | `--output-format stream-json` (NDJSON on stdout) | Plain text on stdout; structured tool-call data lives in the per-conversation transcript JSONL (see below) |
| Session resume | `--resume <id>` | `--continue` (most recent in cwd) or `--conversation <uuid>` |
| Settings dir (`appDataDir`) | `~/.gemini/` | `~/.gemini/antigravity-cli/` |
| Settings file | `~/.gemini/settings.json` | `~/.gemini/antigravity-cli/settings.json` |
| Skills dir | `~/.gemini/skills/<name>/SKILL.md` | `~/.gemini/config/plugins/<name>/` (materialized by `agy plugin install`) |
| Skill management | `gemini skills <link\|install\|enable\|...>` subcommands | `agy plugin install <target>` (plugin manifests carry skills); no `agy skills` subcommand |
| Extensions | Supported via `setup.extensions` | Not modeled; drop the block |
| MCP config location | `mcpServers` in `settings.json` | `mcpServers` in a separate `~/.gemini/config/mcp_config.json` |
| MCP HTTP transport field | `httpUrl` | `serverUrl` (no `httpUrl` field; `httpUrl` is auto-translated by the harness) |
| MCP tool name format | `mcp_<server>_<tool>` (single underscore) | No per-tool functions -- every MCP call goes through a single native `call_mcp_tool` wrapper whose args carry `ServerName`/`ToolName`/`Arguments`; the harness unwraps it to the canonical `<server>__<tool>` (see `canonicalize_agy_tool_name` in `tool_naming.py`) |
| Model selection | `GEMINI_API_MODEL` / `GEMINI_MODEL` env var | `--model` flag (agy >=1.0.5); value is a UI label (e.g. `"Gemini 3.1 Pro (High)"`), not an API id |
| Auth | NPM auth token via `gcloud auth print-access-token` plus ADC | OAuth (keyring-backed); ADC not required by agy itself |
| Token-usage stats | Reported per request | Not exposed; transcript carries no token counts (verified through agy v1.0.5). `token_consumption` is omitted from the agy example configs since it would only ever report zero |

### Tool-call extraction (transcript JSONL)

Since agy has no `--output-format stream-json`, the harness reads
structured tool-call data out of the per-conversation transcript that
agy writes to:

```
~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl
```

The conversation UUID for a given working directory is looked up in:

```
~/.gemini/antigravity-cli/cache/last_conversations.json
```

Each transcript line is a step with a `type` field. Tool invocations
appear on `PLANNER_RESPONSE` steps as a `tool_calls` array; the
immediately following MODEL step holds the result. Native tools get a
result step typed after the tool (e.g. `VIEW_FILE`, `RUN_COMMAND`); an MCP
call -- always the `call_mcp_tool` wrapper -- gets a dedicated `MCP_TOOL`
result step. The final user-visible reply is the last `PLANNER_RESPONSE`
with `content` (no `tool_calls`). When `--continue` is used the transcript
accumulates across turns; the parser slices from the most-recent
`USER_INPUT` step onward to report only the current turn.

The parser binds each call only to the result step that immediately follows
the planner step that emitted it (strict adjacency); the pending window resets
at every planner step and any intervening step, so a call that never produced
an adjacent result step is not credited with a later call's result. As a guard against forged transcript lines, a `call_mcp_tool`
wrapper is only counted as a successful MCP execution when it is paired with
a genuine `MCP_TOOL` result step; a wrapper with no result, or one paired
with a non-MCP result, is marked failed. This bounds but cannot fully
prevent crediting forged lines -- the authoritative guarantee that MCP is
wired up is the setup-time schema-cache check (`_verify_mcp_runtime`).

---

## Scorers

Identical to Gemini CLI -- see the
[scorers section of the Gemini guide](gemini_cli_agent_testing.md#scorers).
The `trajectory_matcher` default of dropping native/harness-internal tools
also applies; the canonical-name rule it uses (`<server>__<tool>`) lives in
`evalbench/generators/models/tool_naming.py`.

---

## Reporting

Identical to Gemini CLI. CSV under `reporting.csv.output_directory`,
BigQuery under `reporting.bigquery.gcp_project_id`.

---

## Troubleshooting

### `agy: command not found`

The CLI is not on `PATH`. Re-run the installer with `--dir` pointing at a
directory that's on your `PATH`, or symlink the binary into one.

### MCP Server Doesn't Attach

The harness pre-verifies attach at setup (`_verify_mcp_runtime`): it runs a
short `agy -p` probe and fails fast with a `RuntimeError` if a configured
server discovered no tools. If you hit that error:

- **Check the URL field first:** agy uses `serverUrl`, **not** `httpUrl`. A
  wrong field is accepted silently and exposes zero tools.
- Confirm the block lives under `mcpServers` in
  `<fake_home>/.gemini/config/mcp_config.json` (not `settings.json`) after
  setup runs.
- Confirm agy wrote per-tool schemas to
  `<fake_home>/.gemini/antigravity-cli/mcp/<server>/*.json` -- an empty
  directory means the server failed to attach.
- For Google-auth servers, run `gcloud auth application-default login`
  (used for outbound credentials to the MCP server -- agy's own auth is
  separate OAuth).
- Verify OAuth scopes and project ID in the headers.
- If you suspect the path or key is wrong, search the agy binary with
  `strings <agy-binary> | grep -i mcp_config` to confirm what it reads.

### Skill Not Picked Up

- Confirm the plugin registered: check that the name appears in
  `<fake_home>/.gemini/config/import_manifest.json` and that
  `<fake_home>/.gemini/config/plugins/<name>/` was materialized after
  setup runs. The setup log line `agy registered plugins: [...]` reports
  what `agy plugin install` recorded.
- If `agy plugin install` failed, check the logged `rc=` and stderr --
  a bad target (wrong path, missing `plugin.json` manifest, unreachable
  git URL) is the usual cause.
- If you have an `action: link` / `enable` / `install` entry in your
  config, drop it -- those gemini-cli-style actions are not supported
  here and are logged-and-skipped. Use a string target or
  `install_from_repo`.

### Empty or Missing Results

- Confirm `dataset_format` is `agent-format` (or the legacy `gemini-cli-format`).
- Verify the `model_config` path is correct relative to the repo root.
- Check that `agy --version` works from the same shell.
