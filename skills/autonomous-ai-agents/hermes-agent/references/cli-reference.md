# Hermes CLI Reference

Live sources when anything looks stale: `stardust --help`, `hermes <command> --help`,
and this checkout's `website/docs/reference/cli-commands.md`.

### Global Flags

```
hermes [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
stardust chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
stardust setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
stardust model                Interactive model/provider picker
stardust fallback [add|remove|list]  Fallback provider chain
stardust config [show|edit|get|set|unset|path|env-path|check|migrate]
stardust login / logout       OAuth sign-in / clear stored auth
stardust doctor [--fix]       Check dependencies and config
stardust status [--all]       Component status
```

### Tools & Skills

```
stardust tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

stardust skills list|browse|search QUERY|inspect ID
stardust skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
stardust skills config        Enable/disable skills per platform
stardust skills check|update|uninstall|publish PATH
stardust skills tap add REPO  Add a GitHub repo as a skill source
stardust bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
stardust mcp add NAME (--url or --command) | remove | list | test NAME
stardust mcp catalog | install NAME     Curated catalog install
stardust mcp configure NAME             Toggle tool selection
stardust mcp serve                      Run Hermes as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
stardust gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `stardust photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Repository docs: `website/docs/user-guide/messaging/`

### Sessions

```
stardust sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
stardust cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
stardust webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
stardust profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
stardust profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
stardust auth                 Interactive credential manager
stardust auth add [PROVIDER]  Add a supported OAuth/API credential (openai-codex, qwen-oauth, …)
stardust auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
stardust desktop / gui        Native desktop app
stardust dashboard            Web admin panel + embedded chat (--stop / --status)
stardust proxy                OpenAI-compatible local proxy backed by an OAuth provider
stardust project              Named multi-folder workspaces
stardust skin list|use|set    Switch/tweak skins (see references/themes.md)
stardust pets <verb>          Pet mascots (see references/petdex.md)
stardust memory setup|status|off|reset   Memory provider
stardust secrets bitwarden|onepassword   External secret stores
stardust moa                  Mixture-of-Agents slots
stardust hooks / security / backup / import / checkpoints / console
stardust logs [-f] [errors]   View agent/error logs
stardust send                 One-off message through a gateway platform
stardust pairing / plugins / insights / journey / computer-use
stardust acp                  ACP server (IDE integration)
stardust completion bash|zsh|fish
stardust update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `stardust photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `stardust config edit` · `website/docs/user-guide/configuration.md` |
| Tools / toolsets | `stardust tools list` · `website/docs/reference/tools-reference.md` |
| Skills catalog | `stardust skills browse` · `website/docs/reference/skills-catalog.md` |
| Provider setup | `stardust model` · `website/docs/integrations/providers.md` |
| Env variables | `stardust config env-path` · `website/docs/reference/environment-variables.md` |
| Gateway logs | `~/.stardust/logs/gateway.log` (or `stardust logs`) |
| Sessions | `stardust sessions browse` (reads state.db) |
