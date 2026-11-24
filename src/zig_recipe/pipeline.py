"""End-to-end single-pass analysis pipeline."""

from __future__ import annotations

import logging
from pathlib import Path

from zig_recipe.cache import CacheDirs
from zig_recipe.deps import (
    ResolvedDependencies,
    pack_git_dependencies_tarball,
    resolve_dependencies,
)
from zig_recipe.manifest import parse_manifest, resolve_project_paths
from zig_recipe.model import RecipeModel
from zig_recipe.zig.inspector import ZigInspector

logger = logging.getLogger(__name__)


def analyze_project(
    *,
    project_path: Path,
    zig_exe: str | Path = "zig",
    cache_dir: Path | None = None,
    fetch: bool = True,
    zig_build_args: tuple[str, ...] = (),
    tarball_dir: Path | None = None,
) -> RecipeModel:
    """Parse manifest, fetch transitive dependencies, and inspect build configuration."""
    paths = resolve_project_paths(project_path)
    manifest = parse_manifest(paths.build_zig_zon)
    logger.info("Analyzing %s %s (%s)", manifest.name, manifest.version_raw, paths.root_dir)

    cache = CacheDirs.open(cache_dir)
    inspector = ZigInspector(zig_exe=zig_exe, cache=cache)
    logger.info("Detected Zig %s", inspector.version.raw)

    if fetch and manifest.dependencies:
        inspector.fetch_all(project_dir=paths.root_dir, build_zig=paths.build_zig)

    resolved_deps = resolve_dependencies(
        root_dir=paths.root_dir,
        root_manifest=manifest,
        packages_dir=cache.packages_dir,
    )

    if resolved_deps.git_commits:
        tarball_path = pack_git_dependencies_tarball(
            project_name=manifest.name,
            project_version=manifest.version_raw,
            git_packages=resolved_deps.git_commits,
            packages_dir=cache.packages_dir,
            output_dir=tarball_dir or cache.git_tarballs_dir,
        )
        resolved_deps = ResolvedDependencies(
            packages=resolved_deps.packages,
            tarball_tarball=tarball_path,
        )

    inspection = inspector.inspect(
        project_dir=paths.root_dir,
        build_zig=paths.build_zig,
        extra_args=zig_build_args,
    )

    return RecipeModel(
        project_name=manifest.name,
        project_version=manifest.version_raw,
        summary=manifest.summary,
        zig_version=inspector.version,
        dependencies=resolved_deps,
        inspection=inspection,
    )
