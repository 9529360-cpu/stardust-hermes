---
title: "Imessage — Send and receive iMessages/SMS via the imsg CLI on macOS"
sidebar_label: "Imessage"
description: "Send and receive iMessages/SMS via the imsg CLI on macOS"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Imessage

Send and receive iMessages/SMS via the imsg CLI on macOS.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/apple\imessage` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | macos |
| Tags | `iMessage`, `SMS`, `messaging`, `macOS`, `Apple` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# iMessage

Use `imsg` to read and send iMessage/SMS via macOS Messages.app.

## Prerequisites

- **macOS** with Messages.app signed in
- Install: `brew install steipete/tap/imsg`
- Grant Full Disk Access for terminal (System Settings → Privacy → Full Disk Access)
- Grant Automation permission for Messages.app when prompted

## When to Use

- User asks to send an iMessage or text message
- Reading iMessage conversation history
- Checking recent Messages.app chats
- Sending to phone numbers or Apple IDs

## When NOT to Use

- Telegram/Discord/Slack/WhatsApp messages → use the appropriate gateway channel
- Group chat management (adding/removing members) → not supported
- Bulk/mass messaging → always confirm with user first

## Quick Reference

### List Chats

```bash
imsg chats --limit 10 --json
```

### View History

```bash
# By chat ID
imsg history --chat-id 1 --limit 20 --json

# With attachments info
imsg history --chat-id 1 --limit 20 --attachments --json
```

### Send Messages

```bash
# Text only
imsg send --to "+14155551212" --text "Hello!"

# With attachment
imsg send --to "+14155551212" --text "Check this out" --file /path/to/image.jpg

# Force iMessage or SMS
imsg send --to "+14155551212" --text "Hi" --service imessage
imsg send --to "+14155551212" --text "Hi" --service sms
```

### Watch for New Messages

```bash
imsg watch --chat-id 1 --attachments
```

## Service Options

- `--service imessage` — Force iMessage (requires recipient has iMessage)
- `--service sms` — Force SMS (green bubble)
- `--service auto` — Let Messages.app decide (default)

## Rules

1. **A clear current-turn send request is authorization.** If the user identifies the recipient and the intended message, send it without asking them to repeat or reconfirm the same details.
2. **Clarify only real ambiguity.** Ask when the named contact resolves to multiple plausible recipients, the recipient is missing, or the requested message content is materially unclear. A raw number explicitly supplied by the user is not "unknown" for this rule.
3. **Verify file paths** exist before attaching.
4. **Don't spam** — bulk or repeated outreach still needs clear user intent and sensible rate limiting.

## Example Workflow

User: "Text mom that I'll be late"

```bash
# 1. Find mom's chat
imsg chats --limit 20 --json | jq '.[] | select(.displayName | contains("Mom"))'

# 2. If the match is unambiguous, send directly — the user's request already authorized it.
imsg send --to "+1555123456" --text "I'll be late"
```

If multiple "Mom" contacts are plausible, ask which one before sending.
