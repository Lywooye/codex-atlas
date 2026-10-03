# Installation, stopping, and removal

## Requirements

- macOS for the managed launchd service and native Codex chat links.
- Python 3.11+ with SQLite FTS5 and the trigram tokenizer. Some system Python builds may lack this feature.
- Node.js 20+ and npm to build the frontend.
- A compatible, signed-in Codex CLI for real-history metadata and AI analysis. Demo mode does not need it.

Install dependencies inside the checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
```

Keep the checkout in a private, stable folder; moving it later invalidates the managed service's paths. Do not keep its runtime data in a public repository or shared sync folder. If macOS requests folder access for Python, grant access only if you intend to read history and run this installation.

## Start in the foreground

```sh
.venv/bin/python -m atlas.server
```

Open `http://127.0.0.1:8787/`. Stop with `Ctrl+C`. This mode ends when the terminal process ends. AI starts paused on a new data directory. Indexing can run while AI is paused.

## Start as a macOS background service

```sh
./start.command
```

The launcher installs a per-user launchd agent, opens the page, and keeps the service running after the terminal or page is closed. launchd can restart a terminated process while the user is logged in. Sleep, logout, shutdown, missing files, denied permissions, and exhausted model allowance can still interrupt progress; this is not an uptime guarantee. Manual analysis pause persists across restarts.

Stop the service for the current login:

```sh
.venv/bin/python service_agent.py stop
```

The launch agent remains installed and can start at a future login. To remove automatic startup and stop the service:

```sh
.venv/bin/python service_agent.py uninstall
```

Uninstalling the agent preserves the data. After stopping the service, you may remove the checkout and its default `data/` folder if you no longer want the local index, summaries, edits, and logs. If you configured another data directory, remove that separately after checking the path. Original Codex conversations are unaffected.

## Optional paths

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `ATLAS_CODEX_HOME` | Codex history and configuration location | `~/.codex` |
| `ATLAS_CODEX_BIN` | Explicit compatible Codex executable | Discovered CLI |
| `ATLAS_DATA_DIR` | Atlas's private runtime data directory | `data/` in this checkout |

Set overrides before starting or installing the service. Existing launchd installations retain their installed environment; uninstall and reinstall after changing paths. Model selection is saved only in Atlas's own settings, not in the global Codex configuration.

## 中文

先在项目内安装 Python 虚拟环境和前端依赖。前台用 `.venv/bin/python -m atlas.server` 启动，按 `Ctrl+C` 停止；后台用 `./start.command` 安装当前用户的 launchd 服务。

`service_agent.py stop` 仅停止当前登录期间的服务，保留以后登录时自动启动的配置；`service_agent.py uninstall` 停止服务并移除自动启动配置，保留数据。确认服务停止后，可自行删除项目及其 `data/`；如果指定了其他数据目录，需要另行检查并删除。原 Codex 对话不会被删除。
