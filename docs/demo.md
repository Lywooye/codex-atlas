# Fictional demo

After installing dependencies and building the frontend as described in the [README](../README.md), run:

```sh
.venv/bin/python demo.py
```

Open `http://127.0.0.1:8788/`. The demo uses a different port from a normal Atlas installation. `--port 8790` selects another local port. `Ctrl+C` stops the server; its temporary files are removed on normal exit. A force-kill can leave a temporary directory behind, but it contains only the invented data.

The demo includes two unrelated projects, **Community Seed Library** and **Neighborhood Walking Guide**, across three fictional chats. Both share the topics **Community projects** and **Offline tools**. A general learning discussion is attached only to a topic. No personal transcript or real account identifier was used to build the sample.

Try these actions:

1. Open Community Seed Library and inspect its two source conversations.
2. Compare the earlier map release claim with the later retraction and narrower search verification record.
3. Search `seed`, `种子`, or `离线` and inspect matching original messages.
4. Open Neighborhood Walking Guide to see a reported draft completion and an unresolved review blocker.
5. Switch the interface language. The stored bilingual sample text stays unchanged.

“Verified” records here are explicitly fictional sample evidence. They demonstrate presentation, not independent project results. Native Codex chat links and analysis controls are intentionally unavailable because these chats do not exist in Codex.

To validate the fixture without opening a port:

```sh
.venv/bin/python demo.py --check
.venv/bin/python scripts/smoke_demo.py
```

## 中文

演示包含两个完全虚构的项目、三个聊天、共享主题、普通知识讨论以及一次撤回记录。搜索 `种子` 或 `离线` 可检查中文检索；点击来源可阅读虚构原文。演示不读取你的 Codex 历史，不启动 Codex，不消耗模型额度。切换界面语言不会翻译演示内容。退出后临时数据会被清理。
