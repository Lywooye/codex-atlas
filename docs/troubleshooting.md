# Troubleshooting

| Symptom | What to check |
| --- | --- |
| Page cannot connect | Confirm the server is running and the correct port is open: normal installation 8787, fictional demo 8788. Read `data/logs/launcher.log` for managed startup errors. |
| Indexing works but no summaries appear | A fresh installation pauses AI by default. Review the privacy notice, model settings, and queue state before resuming. |
| A batch fails | The status panel distinguishes retrying and failed batches. Invalid outputs are retried a limited number of times; other batches can continue. Use Retry after resolving the cause. |
| Service errors or quota exhaustion | Read the displayed error and cooldown time. Automatic retries cannot restore login, add credits, or fix unsupported CLI options. Pause explicitly if you want all new attempts to stop. |
| Model settings unavailable | Open/update Codex so its local model catalog is present; check `ATLAS_CODEX_HOME`. Atlas does not invent models absent from that catalog. |
| SQLite reports an unknown tokenizer | Use a Python build whose bundled SQLite supports FTS5 and trigram. Do not delete the existing index to work around a missing SQLite capability. |
| Source chat will not open | Confirm the Codex app is installed and that the local conversation still exists. Demo chats deliberately cannot open in Codex. |
| A label changes language but a summary does not | Language selection changes interface text. Imported conversations and saved summaries remain in their stored language. |
| Startup fails after moving the folder | Remove and reinstall the launch agent from its new location. Check macOS folder permissions. |

Structured service events are in `data/logs/atlas.jsonl`, with rotated backups. `launcher.log` records launcher output and `fatal.log` can contain fatal Python diagnostics. Paths are relative to the configured data directory. A hard power-off or force-kill may prevent a final log entry; restart markers can still show that the previous run did not exit cleanly.

Logs, status JSON, and screenshots may reveal conversation identifiers, paths, model errors, or snippets. Do not attach them publicly without a careful review. Prefer a synthetic reproduction using `demo.py` and share only the relevant redacted error.

## 中文

页面无法连接时先检查服务和端口；能检索但没有摘要时，检查是否仍处于默认暂停状态。日志位于数据目录的 `logs/` 中。断电或强制结束进程不保证留有最后一条日志，重启记录可帮助判断异常退出。

额度不足、未登录、CLI 参数不兼容或文件授权被拒绝，仍可能需要人工处理；自动恢复不能替你补充额度或授权。日志、状态截图和错误文本都可能带有私人信息，公开反馈应优先使用虚构演示复现。
