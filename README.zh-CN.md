# Codex Atlas

[English](README.md)

在本机把 Codex 对话整理成项目、主题和可追溯的工作历史。

分类依据是对话内容，而不是聊天所在目录。一条混合对话可以关联多个项目，一个项目也可以跨聊天、跨目录。你可以搜索原文、展开摘要的来源，再返回对应的 Codex 聊天继续工作。

**macOS 早期预览版。** 本项目是独立工具，并非 OpenAI 官方产品。Codex 的历史格式与 CLI 能力可能变化。

## 先体验虚构演示

需要 Python 3.11+、Node.js 20+、npm，以及支持 FTS5 和 trigram 分词器的 Python SQLite。演示不需要安装或登录 Codex。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
.venv/bin/python demo.py
```

打开[演示页面](http://127.0.0.1:8788/)。其中的对话和分析结果全部虚构，不读取你的 Codex 历史，不启动 Codex，也不调用模型。按 `Ctrl+C` 退出并清理临时演示数据。安装依赖需要联网，运行演示无需互联网连接。详见[演示说明](docs/demo.md)。

## 整理自己的对话

导入前请阅读[隐私说明](PRIVACY.md)。安装并登录兼容的 Codex CLI，然后运行：

```sh
.venv/bin/python -m atlas.server
```

打开[总览页面](http://127.0.0.1:8787/)。本地索引会开始建立，但**新安装默认暂停 AI 分析**。在“模型设置”中选择模型和推理强度，确认后手动继续整理。

**AI 分析会把对话批次、抽样工具证据和已有项目上下文发送给 Codex 模型服务，并消耗 Codex 额度或 Credits。** 数据库保存在本机，不代表模型分析也在离线进行。页面支持中英文切换；切换界面语言不会翻译已有原文或摘要，新摘要会沿用来源对话的语言。

如需关闭终端后仍继续运行，可在完成依赖安装后执行 `./start.command`，安装当前用户的 launchd 后台服务。[安装、停止与卸载说明](docs/installation.md)列出了具体命令。

## 主要功能

- 项目与主题双视图，支持主项目内的工作分项。
- 展示有来源的摘要、决定、待办、卡点和历史变化。
- 区分讨论、决定、对话声称完成、附验证记录和撤回。
- 搜索原文与派生内容，支持短中文及中英文混合查询。
- 重命名、合并、拆分、调整归属；后续分析保留人工修正。
- 修改整理模型、暂停和恢复队列、查看覆盖范围、重试失败批次。

不会根据消息数量猜测完成百分比。“附验证记录”只表示历史对话中存在相应证据，不代表 Atlas 重新执行并独立验证了工作。

## 运行与使用边界

索引采用增量读取。服务重启后可恢复被中断的批次；无效分析有限次重试后标为失败，并继续其他批次；网络或服务错误采用延迟重试。手动暂停会跨重启保留。日志用于排查故障，也必须按私人数据保管。

服务只监听本机地址。这是单用户桌面工具，不能作为带登录鉴权的团队服务器。请勿通过反向代理、隧道或公网托管暴露服务。同一用户下运行的其他软件仍可能访问其文件与本地接口。详见[安全说明](SECURITY.md)。

首版读取本机 Codex 历史，不读取 ChatGPT 云端聊天。原聊天跳转需要安装 Codex 应用，不保证定位到某条消息。摘要可能有误、遗漏或过时，重要结论应核对来源。

## 开发验证

```sh
.venv/bin/python -m pytest tests
.venv/bin/python demo.py --check
.venv/bin/python scripts/smoke_demo.py
npm --prefix web run build
```

后端为 FastAPI 与 SQLite，前端为 React 与 TypeScript。遇到问题请查看[排错说明](docs/troubleshooting.md)。请勿在贡献中提交运行数据库、日志、真实对话、个人模型设置、凭据或私人截图。

## 许可证

[MIT](LICENSE)。
