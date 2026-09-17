---
sidebar_position: 3
title: "Android / Termux"
description: "Run Stardust's Hermes-compatible runtime directly on Android with Termux"
---

# Stardust on Android with Termux

:::warning Tier 2 platform
Termux (Android) is a [Tier 2 platform](./platform-support.md#tier-2). The installer path and documentation are maintained on a best-effort basis. Commits to `main` may temporarily break Android-specific dependencies.
:::

Stardust can run directly on an Android phone through [Termux](https://termux.dev/) using the inherited `hermes` CLI/runtime compatibility layer.

## Tested runtime bundle

The tested Termux bundle installs the compatible CLI plus core features known to work on Android:

- the `hermes` CLI runtime used by Stardust;
- cron support;
- PTY/background terminal support;
- Telegram gateway support (manual / best-effort background runs);
- MCP support;
- Honcho memory support;
- ACP support.

The Python bundle maps to:

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

## Current limitations

Some desktop/server features rely on dependencies that are unavailable or unvalidated on Android:

- `.[all]` is not supported on Android today;
- the `voice` extra is blocked by `faster-whisper -> ctranslate2`, which does not publish Android wheels;
- automatic browser / Playwright bootstrap is skipped;
- Docker-based terminal isolation is unavailable inside Termux;
- Android may suspend Termux background jobs, so gateway persistence is best-effort.

---

## Option 1: Stardust one-line installer

Use the Stardust-owned bootstrap, not the former Hermes website installer:

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

On Termux, the inherited installer logic automatically:

- uses `pkg` for system packages;
- creates the venv with `python -m venv`;
- attempts `.[termux-all]`, then falls back to `.[termux]`, then a base install;
- links `hermes` into `$PREFIX/bin` so the compatibility command stays on PATH;
- skips untested browser / WhatsApp bootstrap.

The bootstrap obtains product source from `9529360-cpu/stardust-hermes` and refuses to execute if an upstream Hermes product-source URL survives its source-authority rewrite.

---

## Option 2: Manual Stardust source install

### 1. Update Termux and install system packages

```bash
pkg update
pkg install -y git python clang rust make pkg-config libffi openssl nodejs ripgrep ffmpeg
```

:::warning Supported Python range
The inherited runtime currently requires **Python >=3.11,&lt;3.14**. If Termux ships a newer unsupported Python, install a supported interpreter from the [Termux User Repository (TUR)](https://github.com/termux-user-repository/tur):

```bash
pkg install tur-repo
pkg install python3.13
```

Then use `python3.13` in place of `python` below.
:::

### 2. Clone Stardust

```bash
git clone https://github.com/9529360-cpu/stardust-hermes.git
cd stardust-hermes
```

### 3. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install --upgrade pip setuptools wheel
```

`ANDROID_API_LEVEL` is important for Rust / maturin-based packages such as `jiter`.

### 4. Install the tested Termux bundle

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

For the minimal core runtime:

```bash
python -m pip install -e '.' -c constraints-termux.txt
```

### 5. Put `hermes` on your Termux PATH

```bash
ln -sf "$PWD/venv/bin/hermes" "$PREFIX/bin/hermes"
```

`hermes` remains the inherited command name for compatibility; the product/source maintained by this repository is Stardust.

### 6. Verify and start

```bash
hermes --version
hermes doctor
hermes
```

---

## Community `pkg` option

:::caution External distribution
A community-maintained APT repository exists for the upstream Hermes-compatible package. It is operated outside Stardust and is **not a Stardust release channel**. Installing it means trusting that external repository and may install a package that does not match this repository's Stardust modifications.
:::

The external packaging sources are published in [`adybag14-cyber/termux-python`](https://github.com/adybag14-cyber/termux-python) and [`adybag14-cyber/termux-hermes`](https://github.com/adybag14-cyber/termux-hermes).

```bash
curl -fsSL https://raw.githubusercontent.com/adybag14-cyber/termux-python/main/scripts/setup_apt_repo.sh | bash
pkg install hermes-agent
```

That package is managed through APT rather than the Stardust Git checkout:

```bash
pkg update
pkg upgrade hermes-agent
```

Use the Stardust installer/manual clone above when you specifically want the code maintained in `9529360-cpu/stardust-hermes`.

---

## Follow-up setup

Configure a model:

```bash
hermes model
```

Re-run the setup wizard later:

```bash
hermes setup
```

### Optional browser tooling

The tested Termux path skips local browser bootstrap. Cloud browser providers host their own browser. For local experiments you can install Node and `agent-browser` manually:

```bash
pkg install nodejs-lts
npm install -g agent-browser && agent-browser install
```

Treat local browser and WhatsApp tooling on Android as experimental until documented otherwise.

---

## Troubleshooting

### `No solution found` when installing `.[all]`

Use the tested Termux bundle:

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

### `uv pip install` fails on Android

Use the stdlib venv + pip path:

```bash
python -m venv venv
source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

### `jiter` / `maturin` complains about `ANDROID_API_LEVEL`

```bash
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

### `hermes doctor` says ripgrep or Node is missing

```bash
pkg install ripgrep nodejs
```

### Build failures while installing Python packages

```bash
pkg install clang rust make pkg-config libffi openssl
```

Then retry the `.[termux]` install.

---

## Known phone limitations

- Docker backend is unavailable;
- local voice transcription via `faster-whisper` is unavailable in the tested path;
- browser automation setup is intentionally skipped by the installer;
- only `.[termux]` and `.[termux-all]` are documented as tested Android bundles.

If you hit an Android-specific issue, open an issue in the Stardust repository and include:

- Android version;
- `termux-info`;
- `python --version`;
- `hermes doctor`;
- the exact install command and full error output.
