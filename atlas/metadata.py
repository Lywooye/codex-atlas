"""Bounded metadata reads via the official Codex app-server protocol."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time

from .analysis import codex_binary, stop_process


SOURCE_KINDS = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview",
                "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]


def load_metadata(codex_home: Path, timeout: float = 35) -> tuple[dict, str | None]:
    """Return id -> metadata and an explicit error if pagination was incomplete."""
    mapping: dict = {}
    process = None
    incoming: queue.Queue = queue.Queue()
    deadline = time.monotonic() + timeout
    try:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen([codex_binary(), "app-server", "--stdio"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                       text=True, encoding="utf-8", env=env,
                                       start_new_session=(os.name == "posix"))

            def read_stdout() -> None:
                try:
                    assert process is not None and process.stdout is not None
                    for line in process.stdout:
                        try:
                            incoming.put(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                finally:
                    incoming.put(None)

            reader = threading.Thread(target=read_stdout, name="atlas-metadata-reader", daemon=True)
            reader.start()
            next_id = 0

            def send(message: dict) -> None:
                assert process is not None and process.stdin is not None
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()

            def request(method: str, params: dict) -> dict:
                nonlocal next_id
                next_id += 1
                request_id = next_id
                send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Codex 元数据读取超时，返回的列表可能不完整。")
                    try:
                        response = incoming.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise TimeoutError("Codex 元数据读取超时，返回的列表可能不完整。") from exc
                    if response is None:
                        raise RuntimeError("Codex app-server 在分页完成前退出。")
                    if not isinstance(response, dict) or response.get("id") != request_id:
                        continue
                    if "error" in response:
                        error = response["error"]
                        raise RuntimeError("Codex 元数据接口错误：" + str(error.get("message", error))[:1000])
                    result = response.get("result")
                    if not isinstance(result, dict):
                        raise RuntimeError("Codex 元数据接口返回了无效结构。")
                    return result

            request("initialize", {"clientInfo": {"name": "codex_project_atlas", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            for archived in (False, True):
                cursor = None
                seen: set[str] = set()
                while True:
                    result = request("thread/list", {"limit": 100, "cursor": cursor,
                        "archived": archived, "useStateDbOnly": True,
                        "sourceKinds": SOURCE_KINDS, "modelProviders": [],
                        "sortKey": "updated_at", "sortDirection": "desc"})
                    rows = result.get("data")
                    if not isinstance(rows, list):
                        raise RuntimeError("Codex thread/list 缺少 data 列表。")
                    for row in rows:
                        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                            raise RuntimeError("Codex thread/list 包含无效记录。")
                        mapping[row["id"]] = {**row, "title": row.get("name") or "",
                                               "archived": archived,
                                               "updated_at": row.get("updatedAt"),
                                               "source_kind": row.get("threadSource") or row.get("source")}
                    cursor = result.get("nextCursor")
                    if cursor is None:
                        break
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise RuntimeError("Codex 元数据分页游标无效或重复，列表可能不完整。")
                    seen.add(cursor)
            return mapping, None
    except Exception as exc:
        return mapping, str(exc)[:2000]
    finally:
        if process is not None:
            stop_process(process)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
