"""ZON manifest parsing and project path discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import natizon
from semver import Version

from zig_recipe.errors import ManifestError


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Resolved paths to a Zig project's root, build.zig, and build.zig.zon."""

    root_dir: Path
    build_zig: Path
    build_zig_zon: Path


@dataclass(frozen=True, slots=True)
class LocalDependency:
    """A local path dependency declared in build.zig.zon."""

    key: str
    path: str
    lazy: bool = False


@dataclass(frozen=True, slots=True)
class RemoteDependency:
    """A remote URL+hash dependency declared in build.zig.zon."""

    key: str
    url: str
    hash: str
    lazy: bool = False


ZonDependency = LocalDependency | RemoteDependency


@dataclass(frozen=True, slots=True)
class ZonManifest:
    """Parsed and validated build.zig.zon manifest."""

    name: str
    version: Version
    version_raw: str
    minimum_zig_version: Version | None
    fingerprint: int | None
    summary: str | None
    paths: tuple[str, ...]
    dependencies: tuple[ZonDependency, ...]


def resolve_project_paths(target: Path) -> ProjectPaths:
    """Resolve a project directory or build.zig path to its build files."""
    resolved = target.expanduser().resolve()
    if resolved.is_dir():
        root_dir = resolved
        build_zig = root_dir / "build.zig"
    elif resolved.is_file():
        if resolved.name == "build.zig":
            build_zig = resolved
            root_dir = resolved.parent
        elif resolved.name == "build.zig.zon":
            root_dir = resolved.parent
            build_zig = root_dir / "build.zig"
        else:
            raise ManifestError(
                f"Expected a directory, build.zig, or build.zig.zon, got: {resolved}"
            )
    else:
        raise ManifestError(f"Project path does not exist: {resolved}")

    if not build_zig.is_file():
        raise ManifestError(f"build.zig not found at: {build_zig}")

    build_zig_zon = root_dir / "build.zig.zon"
    if not build_zig_zon.is_file():
        raise ManifestError(f"build.zig.zon not found at: {build_zig_zon}")

    return ProjectPaths(root_dir=root_dir, build_zig=build_zig, build_zig_zon=build_zig_zon)


def parse_manifest(path: Path) -> ZonManifest:
    """Read and parse a build.zig.zon file via natizon."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Failed to read {path}: {exc}") from exc
    return parse_manifest_text(text, source=str(path))


def parse_manifest_text(text: str, *, source: str = "<memory>") -> ZonManifest:
    """Parse build.zig.zon text via natizon into a validated ZonManifest."""
    try:
        data = natizon.loads(text, use_tuples=True)
    except Exception as exc:
        raise ManifestError(f"Failed to parse ZON in {source}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError(f"Top-level ZON value in {source} must be a struct")

    name = _require_str(data, "name", source)
    version_raw = _require_str(data, "version", source)
    try:
        version = Version.parse(version_raw)
    except ValueError as exc:
        raise ManifestError(f"Invalid semantic version {version_raw!r} in {source}: {exc}") from exc

    min_zig_raw = data.get("minimum_zig_version")
    minimum_zig_version: Version | None = None
    if min_zig_raw is not None:
        if not isinstance(min_zig_raw, str):
            raise ManifestError(f"'minimum_zig_version' in {source} must be a string")
        try:
            minimum_zig_version = Version.parse(min_zig_raw)
        except ValueError as exc:
            raise ManifestError(
                f"Invalid minimum_zig_version {min_zig_raw!r} in {source}: {exc}"
            ) from exc

    fingerprint_raw = data.get("fingerprint")
    fingerprint: int | None = None
    if fingerprint_raw is not None:
        if not isinstance(fingerprint_raw, int) or isinstance(fingerprint_raw, bool):
            raise ManifestError(f"'fingerprint' in {source} must be an integer")
        if not (0 <= fingerprint_raw < (1 << 64)):
            raise ManifestError(f"'fingerprint' in {source} must be a 64-bit unsigned integer")
        fingerprint = fingerprint_raw

    summary_raw = data.get("summary")
    summary: str | None = None
    if summary_raw is not None:
        if not isinstance(summary_raw, str):
            raise ManifestError(f"'summary' in {source} must be a string")
        summary = summary_raw.strip() or None

    paths_raw = data.get("paths", ())
    if not isinstance(paths_raw, tuple) or not all(isinstance(p, str) for p in paths_raw):
        raise ManifestError(f"'paths' in {source} must be a tuple of strings")
    paths: tuple[str, ...] = tuple(p for p in paths_raw if isinstance(p, str))

    deps_raw = data.get("dependencies", {})
    if not isinstance(deps_raw, dict):
        raise ManifestError(f"'dependencies' in {source} must be a struct")

    local_deps: list[LocalDependency] = []
    remote_deps: list[RemoteDependency] = []
    for dep_key, dep_val in deps_raw.items():
        if not isinstance(dep_key, str) or not isinstance(dep_val, dict):
            raise ManifestError(f"Invalid dependency entry {dep_key!r} in {source}")
        parsed = _parse_dependency(dep_key, dep_val, source)
        if isinstance(parsed, LocalDependency):
            local_deps.append(parsed)
        else:
            remote_deps.append(parsed)

    # Local dependencies first, then remote (matches Zig / zig-ebuilder convention).
    dependencies: tuple[ZonDependency, ...] = (*local_deps, *remote_deps)

    return ZonManifest(
        name=name,
        version=version,
        version_raw=version_raw,
        minimum_zig_version=minimum_zig_version,
        fingerprint=fingerprint,
        summary=summary,
        paths=paths,
        dependencies=dependencies,
    )


def _require_str(mapping: dict[str, Any], key: str, source: str) -> str:
    val = mapping.get(key)
    if not isinstance(val, str) or not val:
        raise ManifestError(f"Missing or invalid string field {key!r} in {source}")
    return val


def _parse_dependency(key: str, data: dict[str, Any], source: str) -> ZonDependency:
    lazy_raw = data.get("lazy", False)
    if not isinstance(lazy_raw, bool):
        raise ManifestError(f"Dependency {key!r} 'lazy' field in {source} must be a bool")

    path_val = data.get("path")
    url_val = data.get("url")
    hash_val = data.get("hash")

    if path_val is not None:
        if not isinstance(path_val, str):
            raise ManifestError(f"Dependency {key!r} 'path' in {source} must be a string")
        if url_val is not None or hash_val is not None:
            raise ManifestError(
                f"Dependency {key!r} in {source} cannot mix 'path' with 'url'/'hash'"
            )
        return LocalDependency(key=key, path=path_val, lazy=lazy_raw)

    if isinstance(url_val, str) and url_val.startswith("file://"):
        return LocalDependency(key=key, path=url_val.removeprefix("file://"), lazy=lazy_raw)

    if not isinstance(url_val, str) or not url_val:
        raise ManifestError(f"Dependency {key!r} in {source} is missing 'url' or 'path'")
    if not isinstance(hash_val, str) or not hash_val:
        raise ManifestError(f"Dependency {key!r} in {source} is missing 'hash'")

    return RemoteDependency(key=key, url=url_val, hash=hash_val, lazy=lazy_raw)
