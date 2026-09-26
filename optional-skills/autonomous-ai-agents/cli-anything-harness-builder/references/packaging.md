# Packaging: PEP 420 namespace packages

Read this for phase 7, once the harness is built and tested.

## Why namespace packages

Every `cli-anything-<software>` CLI installs into the **same** `cli_anything`
namespace, so a machine can have `cli-anything-gimp`, `cli-anything-blender`,
and a dozen others installed side by side, each independently
installable/uninstallable, with clean imports (`from cli_anything.gimp.core...`)
and no conflicts. This works because `cli_anything/` itself carries **no**
`__init__.py` — only its sub-packages (`gimp/`, `blender/`, ...) do.

## `setup.py` template

```python
from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-<software>",
    version="1.0.0",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        # + this harness's own Python dependencies
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-<software>=cli_anything.<software>.<software>_cli:main",
        ],
    },
    python_requires=">=3.10",
    package_data={
        "cli_anything.<software>": ["skills/*.md"],  # ship the harness's own SKILL.md
    },
)
```

- Use `find_namespace_packages`, **not** `find_packages` — the latter won't
  discover a namespace package correctly.
- `include=["cli_anything.*"]` scopes discovery to this namespace.
- The real software (LibreOffice, Blender, ...) can't be expressed in
  `install_requires` — it isn't a PyPI package. Document it in the harness's
  own `README.md` and have the backend module's `find_<software>()` raise a
  clear, actionable error when it's missing (see
  `references/architecture-and-pitfalls.md`).

## Verifying the install, not just the source

```bash
cd <software>/agent-harness
pip install -e .
which cli-anything-<software>
cli-anything-<software> --help
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest cli_anything/<software>/tests/ -v -s
```

The last command's output must show `[_resolve_cli] Using installed command:
/path/to/cli-anything-<software>` — that's the proof the subprocess tests
exercised the real installed binary, not a `python -m` fallback that could
mask a packaging mistake the installed command would actually hit.
