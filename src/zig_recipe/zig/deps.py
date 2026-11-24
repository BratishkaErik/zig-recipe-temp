"""Transitive Zig dependency discovery, URL normalization, and git tarball packing."""

from __future__ import annotations

import logging
import tarfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yarl import URL

from zig_recipe.errors import DependencyError, ManifestError
from zig_recipe.zig.manifest import (
    LocalDependency,
    RemoteDependency,
    ZonManifest,
    parse_manifest,
)

logger = logging.getLogger(__name__)

PackageKind = Literal["tarball", "git_commit"]
UrlOrigin = Literal["native_tarball", "converted_git", "git_commit"]

_ORIGIN_PRIORITY: dict[UrlOrigin, int] = {
    "native_tarball": 2,
    "converted_git": 1,
    "git_commit": 0,
}

ARCHIVE_EXTENSIONS: tuple[str, ...] = (
    ".tar.gz",
    ".tar.xz",
    ".tar.zst",
    ".tar",
    ".tgz",
    ".txz",
    ".tzst",
    ".zip",
    ".jar",
)

# Hosts whose git+https://<host>/<path>[.git]#<commit> URLs map cleanly to archive tarballs.
_GIT_ARCHIVE_TEMPLATES: dict[str, str] = {
    "github.com": "https://{host}{path}/archive/{commit}.tar.gz",
    "codeberg.org": "https://{host}{path}/archive/{commit}.tar.gz",
    "git.sr.ht": "https://{host}{path}/archive/{commit}.tar.gz",
    "gitlab.com": "https://{host}{path}/-/archive/{commit}.tar.gz",
}


@dataclass(frozen=True, slots=True)
class ResolvedPackage:
    """A deduplicated remote package discovered via transitive build.zig.zon traversal."""

    hash: str
    name: str | None  # None for pristine packages (without their own build.zig.zon)
    url: str
    kind: PackageKind
    origin: UrlOrigin
    extension: str  # e.g. ".tar.gz", ".zip"

    @property
    def filename(self) -> str:
        """Archive filename `<hash><ext>` used by offline package caches."""
        return f"{self.hash}{self.extension}"


@dataclass(frozen=True, slots=True)
class ResolvedDependencies:
    """All remote dependencies discovered for a project."""

    packages: tuple[ResolvedPackage, ...]
    tarball_tarball: Path | None = None

    @property
    def tarballs(self) -> tuple[ResolvedPackage, ...]:
        return tuple(p for p in self.packages if p.kind == "tarball")

    @property
    def git_commits(self) -> tuple[ResolvedPackage, ...]:
        return tuple(p for p in self.packages if p.kind == "git_commit")


def classify_and_convert_url(raw_url: str) -> tuple[str, PackageKind, UrlOrigin, str]:
    """Normalize a dependency URL, converting known git hosts to archive tarballs.

    Returns `(final_url, kind, origin, extension)`.
    """
    parsed = URL(raw_url)
    scheme = parsed.scheme.lower()

    if scheme.startswith("git+"):
        converted = _convert_git_url(parsed)
        if converted is not None:
            return converted, "tarball", "converted_git", ".tar.gz"
        return raw_url, "git_commit", "git_commit", ".tar.gz"

    canonical = _canonicalize_http(parsed)
    ext = detect_archive_extension(canonical.path)
    if ext is None:
        logger.warning("Could not detect archive extension for %s; defaulting to .tar.gz", raw_url)
        ext = ".tar.gz"
    return str(canonical), "tarball", "native_tarball", ext


def detect_archive_extension(path: str) -> str | None:
    lower = path.lower()
    for ext in ARCHIVE_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return None


def _canonicalize_http(url: URL) -> URL:
    scheme = "https" if url.scheme.lower() == "http" else url.scheme.lower()
    host = (url.host or "").lower().removeprefix("www.")
    return url.with_scheme(scheme).with_host(host)


def _convert_git_url(url: URL) -> str | None:
    commit = url.fragment
    if not commit:
        return None

    inner_scheme = url.scheme.lower().removeprefix("git+")
    if inner_scheme not in ("http", "https"):
        return None

    host = (url.host or "").lower().removeprefix("www.")
    template = _GIT_ARCHIVE_TEMPLATES.get(host)
    if template is None:
        return None

    path = url.path.rstrip("/").removesuffix(".git")
    return template.format(host=host, path=path, commit=commit)


def choose_best_package(existing: ResolvedPackage, candidate: ResolvedPackage) -> ResolvedPackage:
    """Prefer native tarballs > converted git tarballs > git commits; ties keep BFS order."""
    if _ORIGIN_PRIORITY[candidate.origin] > _ORIGIN_PRIORITY[existing.origin]:
        return candidate
    return existing


def sort_packages(packages: list[ResolvedPackage]) -> tuple[ResolvedPackage, ...]:
    """Sort named packages alphabetically by (name, hash), pristine packages at the end by hash."""
    named = sorted(
        (p for p in packages if p.name is not None),
        key=lambda p: (p.name or "", p.hash),
    )
    pristine = sorted((p for p in packages if p.name is None), key=lambda p: p.hash)
    return tuple((*named, *pristine))


def resolve_dependencies(
    *,
    root_dir: Path,
    root_manifest: ZonManifest,
    packages_dir: Path,
) -> ResolvedDependencies:
    """BFS-traverse all transitive build.zig.zon dependencies cached in packages_dir."""
    visited_dirs: set[Path] = {root_dir.resolve()}
    queue: deque[tuple[Path, ZonManifest]] = deque([(root_dir.resolve(), root_manifest)])
    by_hash: dict[str, ResolvedPackage] = {}

    while queue:
        pkg_dir, manifest = queue.popleft()
        for dep in manifest.dependencies:
            if isinstance(dep, LocalDependency):
                local_dir = (pkg_dir / dep.path).resolve()
                if local_dir in visited_dirs:
                    continue
                visited_dirs.add(local_dir)
                zon_path = local_dir / "build.zig.zon"
                if zon_path.is_file():
                    queue.append((local_dir, parse_manifest(zon_path)))
                continue

            assert isinstance(dep, RemoteDependency)
            pkg_cache_path = packages_dir / dep.hash
            if not pkg_cache_path.is_dir():
                raise DependencyError(
                    f"Package {dep.hash} ({dep.url}) not found in cache {packages_dir}. "
                    "Run without --no-fetch to populate the cache."
                )

            child_name: str | None = None
            child_zon = pkg_cache_path / "build.zig.zon"
            if child_zon.is_file():
                try:
                    child_manifest = parse_manifest(child_zon)
                    child_name = child_manifest.name
                    if pkg_cache_path.resolve() not in visited_dirs:
                        visited_dirs.add(pkg_cache_path.resolve())
                        queue.append((pkg_cache_path.resolve(), child_manifest))
                except ManifestError as exc:
                    logger.warning("Ignoring malformed dependency manifest %s: %s", child_zon, exc)

            final_url, kind, origin, ext = classify_and_convert_url(dep.url)
            candidate = ResolvedPackage(
                hash=dep.hash,
                name=child_name,
                url=final_url,
                kind=kind,
                origin=origin,
                extension=ext,
            )
            if dep.hash in by_hash:
                by_hash[dep.hash] = choose_best_package(by_hash[dep.hash], candidate)
            else:
                by_hash[dep.hash] = candidate

    return ResolvedDependencies(packages=sort_packages(list(by_hash.values())))


def pack_git_dependencies_tarball(
    *,
    project_name: str,
    project_version: str,
    git_packages: tuple[ResolvedPackage, ...],
    packages_dir: Path,
    output_dir: Path,
) -> Path | None:
    """Create reproducible <name>-<version>-git_dependencies.tar.gz for git deps."""
    if not git_packages:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = project_name.replace("_", "-")
    tarball_path = output_dir / f"{safe_name}-{project_version}-git_dependencies.tar.gz"

    def _normalize(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.uid = 0
        ti.gid = 0
        ti.uname = ""
        ti.gname = ""
        ti.mtime = 1
        return ti

    inner_archives: list[Path] = []
    try:
        for pkg in git_packages:
            src_dir = packages_dir / pkg.hash
            if not src_dir.is_dir():
                raise DependencyError(f"Cached git dependency directory missing: {src_dir}")
            inner_path = output_dir / pkg.filename
            with tarfile.open(inner_path, mode="w:gz", compresslevel=6) as inner_tar:
                for entry in sorted(src_dir.rglob("*")):
                    rel = entry.relative_to(src_dir).as_posix()
                    inner_tar.add(entry, arcname=rel, recursive=False, filter=_normalize)
            inner_archives.append(inner_path)

        with tarfile.open(tarball_path, mode="w:gz", compresslevel=6) as outer_tar:
            for inner_path in sorted(inner_archives, key=lambda p: p.name):
                outer_tar.add(inner_path, arcname=inner_path.name, filter=_normalize)
    finally:
        for inner_path in inner_archives:
            inner_path.unlink(missing_ok=True)

    return tarball_path
