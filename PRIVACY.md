# Privacy / 隐私说明

## What stays on your computer

Atlas reads Codex rollout files from `~/.codex/sessions` and `~/.codex/archived_sessions` by default. It requests current conversation metadata through the installed Codex CLI. It stores indexed text, source locations, classifications, queue state, settings, and diagnostic logs in its own data directory (`data/` by default). It does not modify your original conversations.

Imported text can contain personal details, credentials pasted into a chat, account identifiers, filesystem paths, tool results, and information about other people. Indexing is not anonymization. Derived summaries may repeat that information. The database and logs are not encrypted by Atlas; use your operating system's file permissions and disk encryption as appropriate.

Atlas has no application analytics or telemetry endpoint of its own. Codex CLI, your model provider, and package managers are separate programs with their own network behavior and policies.

## What model analysis sends

AI analysis is paused on a new installation. Resuming it authorizes Atlas to submit batches through your signed-in Codex CLI. A request can contain:

- The source conversation title, selected dialogue messages, and limited earlier context.
- Sampled tool outputs and their source references.
- Existing topic and project names, summaries, goals, decisions, and remaining work used for matching.

These requests consume your Codex usage allowance or credits. Provider retention and account controls still apply; an ephemeral CLI session is not a promise of zero provider retention. Changing the interface language does not alter those data flows. Pausing stops new batches; an already-running batch may finish.

Atlas asks the analyzer to treat history as data and runs it with a read-only sandbox and tools disabled. This reduces the opportunity for instructions inside old chats to be executed. It does not guarantee correct classification or remove sensitive material from a prompt.

## Demo mode

`demo.py` creates invented messages and prewritten summaries in a new temporary directory. It does not read actual Codex history or authentication files, start Codex, or call a model. Analysis and native chat opening are disabled. Dependency installation may use the internet. The local demo server only listens on loopback and its data is removed after normal shutdown.

## Deletion and sharing

Stop the service before deleting its data directory. Deleting that directory removes Atlas's local index, summaries, corrections, settings, and logs. It does not delete original Codex conversations or information already sent to the model service. Service removal alone preserves data; see [installation](docs/installation.md).

Do not publish the data directory, raw logs, source sessions, credentials, quota reports, or screenshots of real conversations. Review the exact staged files or release archive before sharing. Ignore rules are a guardrail, not proof that an archive is safe.

## 中文要点

- 默认读取本机 Codex 历史并建立本地索引，原始对话不被修改；索引、摘要和日志都可能包含私人信息，Atlas 本身不加密这些文件。
- 新安装默认暂停 AI 分析。手动继续后，对话批次、抽样工具输出及已有项目上下文会通过已登录的 Codex CLI 发送给模型服务，消耗额度或 Credits。
- “临时会话”不代表服务商不保留数据；服务商和账户的数据设置仍然适用。暂停阻止领取新批次，已开始的批次可能继续完成。
- 演示数据全部虚构，不读取真实对话或认证文件，不调用模型。依赖安装仍可能需要联网。
- 删除 Atlas 数据不等于删除原 Codex 对话，也不能撤回已发送给模型服务的信息。请勿上传真实数据库、日志、凭据、额度报告或私人截图。
