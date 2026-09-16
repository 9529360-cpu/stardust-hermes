# Stardust Security Policy

Stardust is an independently maintained personal AI assistant built on the Hermes Agent codebase. The original Hermes project remains an important code and design source, but security ownership for this repository belongs to `9529360-cpu/stardust-hermes`.

## Reporting a vulnerability

Please report vulnerabilities privately through this repository's GitHub Security Advisories:

https://github.com/9529360-cpu/stardust-hermes/security/advisories/new

Do not send Stardust vulnerability reports to Nous Research and do not open a public issue for an unpatched vulnerability.

A useful report includes the affected commit, file/component, environment, reproduction steps, expected security boundary, and the observed impact. If possible, include a minimal proof of concept that does not contain real credentials or private user data.

Stardust does not currently operate a bug bounty program.

## Security model

Stardust is a powerful local agent. It can intentionally be granted access to terminals, files, browsers, MCP servers, plugins, messaging adapters, credentials, and remote systems. Those capabilities are not safe merely because an LLM is driving them.

The main security principles are:

- OS-level isolation is the meaningful containment boundary for adversarial model output. Prompt rules, approval regexes, redaction, allowlists inside the same process, and content scanners are useful safeguards but are not substitutes for process/container isolation.
- Network-exposed surfaces must authenticate or explicitly allowlist callers. Session identifiers are routing handles, not authorization credentials.
- Plugins and skills execute with the privileges granted to the agent and must be treated as code, not as harmless prompt files.
- Secrets must stay outside the Git checkout. Do not commit API keys, tokens, private keys, conversation databases, memory/profile data, logs, local configuration, or packaged builds containing operator state.
- Desktop renderer content is lower trust than the Electron main process. Privileged operations must cross narrow, validated IPC boundaries; arbitrary renderer navigation or content must not gain Node/main-process authority.
- Install, repair, update, and release paths are supply-chain boundaries. Stardust product source must resolve to `9529360-cpu/stardust-hermes`, not silently fall back to the original Hermes repository.

## In scope

Examples of security issues that should be reported privately include:

- escaping a documented OS/container isolation boundary;
- bypassing authentication or caller allowlists on a network-facing surface;
- renderer or untrusted web content reaching privileged desktop APIs outside the intended IPC contract;
- credential/session-token leakage outside the operator's trust envelope;
- install/update/recovery paths that can unexpectedly replace Stardust with code from another repository;
- path traversal, command injection, unsafe archive extraction, or arbitrary code execution across a boundary that was expected to constrain it;
- a documented security control failing open in a way that materially expands attacker authority.

## Usually not a security boundary by itself

The following can still be bugs worth fixing, but are not automatically security vulnerabilities without a chained boundary crossing:

- prompt injection by itself;
- bypasses of heuristic output redaction or destructive-command pattern matching;
- behavior explicitly permitted by the operator's selected terminal/process isolation mode;
- malicious behavior from third-party code the operator intentionally installed and granted full agent privileges;
- public exposure that the operator explicitly configured without authentication or network controls.

## Deployment hardening

For normal personal use:

- run Stardust as a non-admin/non-root user;
- keep secrets in operator-controlled credential storage with restrictive file permissions;
- use process/container isolation when the assistant consumes untrusted web, email, messaging, MCP, or file content;
- keep local dashboards and IPC services loopback/local-user only unless an authentication layer is intentionally added;
- review plugins and skills before enabling them;
- keep the repository source-only and inspect staged changes before every public push;
- prefer the Stardust-owned bootstrap installers documented in `README.md`.

## Upstream attribution

Stardust retains the original Hermes Agent copyright notices, Git history, and MIT license. Security issues specific to the original upstream project may also exist upstream, but vulnerabilities affecting this repository should be reported here first so the Stardust-maintained code and release path can be assessed independently.
