# Codex Atlas

[简体中文](README.zh-CN.md)

A local dashboard that turns Codex conversations into projects, topics, and a traceable history of work.

Atlas groups work by conversation content, not by the folder a chat happened to use. One conversation can contribute to several projects; one project can span several conversations. Search the original text, inspect the source of a summary, and return to the corresponding Codex chat.

**Early preview for macOS.** This is an independent tool, not an official OpenAI product. Conversation formats and Codex CLI capabilities can change.

## Try it with fictional data

Prerequisites: Python 3.11+, Node.js 20+, npm, and Python's SQLite build with FTS5 and the trigram tokenizer. The demo needs no Codex installation or login.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
.venv/bin/python demo.py
```

Open [the demo](http://127.0.0.1:8788/). It contains only invented conversations and prewritten analysis; it does not read your Codex history, start Codex, or call a model. Press `Ctrl+C` to stop it and remove its temporary data. Installing dependencies requires network access; running this demo does not require an internet connection. See [demo details](docs/demo.md).

## Use your own conversations

Read [Privacy](PRIVACY.md) before importing private conversations. Install and sign in to a compatible Codex CLI, then run:

```sh
.venv/bin/python -m atlas.server
```

Open [Atlas](http://127.0.0.1:8787/). Local indexing begins, but **AI analysis starts paused on a new installation**. Choose a model and reasoning level under **Model settings**, then explicitly resume analysis when ready.

**AI analysis sends conversation batches, selected tool evidence, and existing project context to Codex's model service and consumes your Codex allowance or credits.** Keeping the database on your Mac does not make model analysis offline. The interface language does not translate stored conversations or summaries; new summaries follow their source conversation's language.

For a macOS service that survives closing the terminal, run `./start.command` after the prerequisite setup. It installs a per-user launchd agent. See [installation, stopping, and removal](docs/installation.md).

## What you can do

- Browse projects and overlapping topics, including work items within a broader project.
- Inspect source-backed summaries, decisions, remaining work, blockers, and historical changes.
- Distinguish discussion, decisions, reported completion, verification recorded in a conversation, and retractions.
- Search original messages and derived summaries, including short Chinese and mixed-language queries.
- Rename, merge, split, and reassign classifications; saved manual edits take precedence over later analysis.
- Change the analysis model, pause or resume the queue, inspect coverage, and retry failed batches.
- Switch between English and Simplified Chinese interface text.

There are no inferred completion percentages. “Verified” means a source conversation contains a verification record; Atlas has not independently rerun the work.

## Reliability and boundaries

Indexing is incremental. Interrupted work can be reclaimed after restart. Invalid analysis is rejected and retried a limited number of times before that batch is left failed; transient service failures use a cooldown. Manual pause persists across restarts. Logs help diagnose failures without needing a screenshot of private conversations.

The server binds to loopback. It is a single-user desktop tool, not an authenticated team server: do not expose it through a proxy, tunnel, LAN address, or public hosting service. Other software running as your user may still access its files and local endpoints. Read [Security](SECURITY.md) for the threat model and reporting guidance.

The first version supports local Codex history, not ChatGPT cloud history. The Codex app is needed for chat deep links. Opening a chat does not guarantee scrolling to a particular message. Summaries can be wrong, incomplete, or stale; follow their sources before relying on them.

## Development

```sh
.venv/bin/python -m pytest tests
.venv/bin/python demo.py --check
.venv/bin/python scripts/smoke_demo.py
npm --prefix web run build
```

The backend uses FastAPI and SQLite; the frontend uses React and TypeScript. See [troubleshooting](docs/troubleshooting.md). Runtime databases, logs, source conversations, model settings, credentials, and personal screenshots must stay out of contributions.

## License

[MIT](LICENSE).
