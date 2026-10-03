# Security / 安全说明

This preview is designed for one user on their own Mac. It is not a public service or a multi-user application. Keep it bound to `127.0.0.1`; do not expose it through a proxy, tunnel, shared server, or LAN interface. Host and browser-origin checks reduce exposure to hostile websites, but they are not account authentication. Software with your user privileges can read the data directory. Other local users and processes may also call loopback endpoints; file permissions do not authenticate HTTP requests.

Treat all source conversations as untrusted input and all runtime files as private. Do not add features that execute commands found in conversations or render arbitrary conversation HTML. Source provenance validation reduces unsupported citations, but does not establish that a model's interpretation is true.

Dependencies and Codex CLI versions affect the security boundary. An unsupported CLI option must fail visibly rather than silently enabling tools or relaxing the sandbox. Review dependency changes and do not assume a passing test suite guarantees that no vulnerability exists.

## Report a problem without exposing private data

Use the repository's private vulnerability reporting channel if one is enabled. If it is unavailable, open only a minimal public issue asking for a private reporting route; do not include an exploit, credentials, private messages, real chat identifiers, database files, or logs. Share a reproduction using the fictional demo wherever possible.

The maintained branch for this preview is the current source version; there is no promised support window for older snapshots yet.

## 中文要点

本工具只用于你自己的 Mac 和账户，不具备团队服务的用户鉴权能力。请勿开放到局域网或公网。同账户软件可以读取数据文件，其他本机用户或进程也可能调用本地 HTTP 接口；文件权限不等于接口鉴权。

发现安全问题时，优先使用仓库的私密漏洞报告入口；没有入口时，仅在公开 issue 中请求私下联系渠道，不要贴出漏洞利用细节、真实对话、数据库、凭据或原始日志。可以用虚构演示构造复现。通过测试不等于绝对安全。
