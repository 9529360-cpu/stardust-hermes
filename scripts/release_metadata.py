"""Read and validate the version fields used by release.py."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from datetime import date
from pathlib import Path
from typing import cast


def parse_calver_date(value: str) -> str:
    """Validate ASCII YYYY.M.D and return its unpadded canonical spelling."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}", value, re.ASCII):
        raise ValueError(f"Invalid release date {value!r}; expected ASCII YYYY.M.D")
    year, month, day = (int(part) for part in value.split("."))
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"Invalid release date {value!r}: {exc}") from exc
    return f"{year:04d}.{month}.{day}"


def date_from_tag(tag: str) -> str:
    """Return a validated date from vYYYY.M.D or its numeric release suffix."""
    match = re.fullmatch(r"v([0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2})(?:\.[0-9]+)?", tag, re.ASCII)
    if not match:
        raise ValueError(f"Release tag {tag!r} does not contain a CalVer date")
    return parse_calver_date(match.group(1))


def _required_string(data: object, key: str, path: Path) -> str:
    value = cast(dict[str, object], data).get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}: required string field {key!r} is missing")
    return value


def _python_metadata(text: str, path: Path) -> tuple[str, str]:
    fields: dict[str, str] = {}
    required = {"__version__", "__release_date__"}
    module = ast.parse(text, filename=str(path))
    allowed_targets: set[int] = set()
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
            names = [statement.target.id] if isinstance(statement.target, ast.Name) else []
        else:
            continue
        for name in required.intersection(names):
            if name in fields:
                raise ValueError(f"duplicate Python metadata field {name!r}")
            if (
                not isinstance(statement, ast.Assign)
                or len(statement.targets) != 1
                or not isinstance(statement.value, ast.Constant)
                or not isinstance(statement.value.value, str)
                or not statement.value.value
            ):
                raise ValueError(f"field {name!r} must be a direct string assignment")
            fields[name] = statement.value.value
            allowed_targets.add(id(statement.targets[0]))
    for node in ast.walk(module):
        binding = None
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if id(node) not in allowed_targets:
                binding = node.id
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            binding = node.name
        elif isinstance(node, ast.alias):
            binding = node.asname or node.name.split(".")[0]
        elif isinstance(node, ast.MatchMapping):
            binding = node.rest
        if binding in required:
            raise ValueError(f"metadata field {binding!r} may only be declared once at module level")
    for name in sorted(required):
        if name not in fields:
            raise ValueError(f"required string field {name!r} is missing")
    return fields["__version__"], fields["__release_date__"]


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def read_metadata(paths: dict[str, Path]) -> dict[str, tuple[str, str | None]]:
    """Read existing metadata paths, mapping names to (version, release_date)."""
    result: dict[str, tuple[str, str | None]] = {}
    for kind, path in paths.items():
        try:
            result[kind] = parse_metadata(kind, path.read_text(encoding="utf-8-sig"), path)
        except (OSError, ValueError, SyntaxError) as exc:
            raise ValueError(f"{path}: cannot read release metadata: {exc}") from exc
    return result


def parse_metadata(kind: str, text: str, path: Path) -> tuple[str, str | None]:
    """Parse one metadata document's (version, release_date) without writing it."""
    if kind == "python":
        return _python_metadata(text, path)
    if kind == "pyproject":
        return (_required_string(tomllib.loads(text).get("project"), "version", path), None)
    if kind == "cargo" or path.name == "Cargo.toml":
        return (_required_string(tomllib.loads(text).get("package"), "version", path), None)
    return (
        _required_string(json.loads(text, object_pairs_hook=_unique_json_object), "version", path),
        None,
    )


def check_metadata_values(
    metadata: dict[str, tuple[str, str | None]], paths: dict[str, Path], *,
    expected_version: str | None = None, expected_date: str | None = None,
    require_consistent: bool = False,
) -> dict[str, tuple[str, str | None]]:
    """Check already-parsed values against the candidate release."""
    versions = {version for version, _ in metadata.values()}
    if expected_version is not None:
        for kind, (version, _) in metadata.items():
            if version != expected_version:
                raise ValueError(f"{paths[kind]}: version {version!r} does not match candidate {expected_version!r}")
    elif require_consistent and len(versions) > 1:
        details = ", ".join(f"{paths[kind]}={version}" for kind, (version, _) in metadata.items())
        raise ValueError(f"release version metadata is inconsistent: {details}; use --bump or prepare consistent metadata")
    if expected_date is not None:
        cli_date = metadata["python"][1]
        if cli_date != expected_date:
            raise ValueError(f"{paths['python']}: __release_date__ {cli_date!r} does not match candidate {expected_date!r}")
    return metadata


def validate_metadata(
    paths: dict[str, Path], *, expected_version: str | None = None,
    expected_date: str | None = None, require_consistent: bool = False,
) -> dict[str, tuple[str, str | None]]:
    """Validate structure and optionally candidate values across metadata files."""
    return check_metadata_values(
        read_metadata(paths), paths,
        expected_version=expected_version,
        expected_date=expected_date,
        require_consistent=require_consistent,
    )
