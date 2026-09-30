---
sidebar_position: 99
title: "Nous Portal (Legacy Compatibility)"
description: "Migration note for inherited Hermes/Nous Portal configurations; not a Stardust account or setup path"
---

# Nous Portal (Legacy Compatibility)

Stardust does **not** use Nous Portal as its account system, subscription system, free tier, or recommended model setup path.

The repository still contains compatibility code for older Hermes installations that already have Nous-related state. That code exists so upgrades can identify or migrate legacy configuration safely; it is not the setup path for a new Stardust installation.

## Configure models directly

Use the model/provider you intend to call:

```bash
hermes model
```

Choose a direct provider when Stardust supports it natively. If you have an OpenAI-compatible API URL and key, choose **Custom endpoint** and configure the base URL, API key, and model explicitly.

Stardust does not require a Stardust-hosted account or subscription to use those paths.

## Commands retired from the Stardust product path

Do not use inherited instructions that tell you to run:

```text
hermes setup --portal
hermes portal
hermes auth add nous
hermes auth upgrade
```

Those commands belonged to the upstream Nous account/subscription experience and are not part of the current Stardust setup contract.

## Migrating an older install

If an existing `auth.json` or `config.yaml` still contains Nous Portal state, Stardust may retain compatibility data long enough to avoid destructive migration, but it does not automatically adopt that account as your active provider.

Configure the API/provider/model you actually want with `hermes model`, verify it works, and then remove obsolete credentials/configuration when you no longer need them.

See [AI Providers](/integrations/providers) for direct providers and custom endpoints.
