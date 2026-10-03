#!/bin/zsh
set -e
umask 077
cd "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
RUNTIME="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
if [[ -d "$RUNTIME/node/bin" ]]; then
  export PATH="$RUNTIME/node/bin:$PATH"
fi
if ! command -v codex >/dev/null 2>&1; then
  for candidate in "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" "/Applications/Codex.app/Contents/Resources/codex"; do
    if [[ -x "$candidate" ]]; then
      export ATLAS_CODEX_BIN="$candidate"
      break
    fi
  done
fi
if [[ ! -x .venv/bin/python ]]; then
  if [[ -x "$RUNTIME/python/bin/python3" ]]; then
    "$RUNTIME/python/bin/python3" -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -f web/dist/index.html ]]; then
  (cd web && npm ci && npm run build)
fi
.venv/bin/python service_agent.py
open 'http://127.0.0.1:8787/'
