"""End-to-end single- and multi-pass analysis pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from zig_recipe.config import GentooOutputConfig, PassConfig, RecipeConfig, build_zig_invocations
from zig_recipe.model import PassInfo, PassResult, RecipeModel
from zig_recipe.zig.cache import CacheDirs
from zig_recipe.zig.deps import (
    ResolvedDependencies,
    pack_git_dependencies_tarball,
    resolve_dependencies,
)
from zig_recipe.zig.inspector import ZigInspector
from zig_recipe.zig.manifest import parse_manifest, resolve_project_paths

if TYPE_CHECKING:
    from zig_recipe.ui.progress import ProgressReporter

logger = logging.getLogger(__name__)


def analyze_project(
    *,
    project_path: Path,
    zig_exe: str | Path = "zig",
    cache_dir: Path | None = None,
    fetch: bool = True,
    zig_build_args: tuple[str, ...] = (),
    pass_config: PassConfig | None = None,
    recipe_config: RecipeConfig | None = None,
    tarball_dir: Path | None = None,
    progress: ProgressReporter | None = None,
) -> RecipeModel:
    """Parse manifest, fetch transitive dependencies, and inspect one or more build passes."""
    paths = resolve_project_paths(project_path)
    manifest = parse_manifest(paths.build_zig_zon)
    logger.info("Analyzing %s %s (%s)", manifest.name, manifest.version_raw, paths.root_dir)

    cache = CacheDirs.open(cache_dir)
    inspector = ZigInspector(zig_exe=zig_exe, cache=cache, progress=progress)
    logger.info("Detected Zig %s", inspector.version.raw)

    if fetch and manifest.dependencies:
        if progress is not None:
            progress.set_stage(f"Fetching dependencies for {manifest.name} {manifest.version_raw}")
        inspector.fetch_all(project_dir=paths.root_dir, build_zig=paths.build_zig)

    if progress is not None:
        progress.set_stage("Resolving dependency URLs")
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

    effective_config = (
        pass_config
        if pass_config is not None
        else (
            recipe_config.build
            if recipe_config is not None
            else PassConfig(base_args=zig_build_args)
        )
    )
    invocations = build_zig_invocations(effective_config)
    if progress is not None:
        progress.set_stage(
            f"Inspecting {manifest.name} ({len(invocations)} pass"
            f"{'es' if len(invocations) != 1 else ''})",
            total=len(invocations),
        )

    pass_results: list[PassResult] = []
    default_step_names: tuple[str, ...] = ("install",)
    has_explicit_test = any(inv.name == "test" for inv in invocations)

    for invocation in invocations:
        if progress is not None:
            progress.set_stage(f"Inspecting pass '{invocation.name}'", total=len(invocations))
        if invocation.name == "default":
            cfg_path = inspector.inspect_configuration(
                project_dir=paths.root_dir,
                build_zig=paths.build_zig,
                extra_args=invocation.args,
            )
            default_insp = inspector.dump_configuration(cfg_path)
            found_defaults = tuple(s.name for s in default_insp.top_level_steps if s.is_default)
            default_step_names = found_defaults or ("install",)
            pass_results.append(
                PassResult(
                    info=PassInfo(
                        name="default",
                        args=invocation.args,
                        steps=default_step_names,
                    ),
                    inspection=default_insp,
                )
            )

            # Automatically trace the `test` step from the exact same Configuration binary
            # unless the user configured an explicit `test` variant.
            has_separate_test = any(
                s.name == "test" and not s.is_default for s in default_insp.top_level_steps
            )
            if has_separate_test and not has_explicit_test:
                test_insp = inspector.dump_configuration(cfg_path, step_names=("test",))
                pass_results.append(
                    PassResult(
                        info=PassInfo(
                            name="test",
                            args=invocation.args,
                            steps=("test",),
                        ),
                        inspection=test_insp,
                    )
                )
        elif invocation.name == "test":
            logger.info("Inspecting variant 'test'...")
            cfg_path = inspector.inspect_configuration(
                project_dir=paths.root_dir,
                build_zig=paths.build_zig,
                extra_args=invocation.args,
            )
            probe = inspector.dump_configuration(cfg_path)
            has_test_step = any(s.name == "test" for s in probe.top_level_steps)
            if has_test_step:
                test_insp = inspector.dump_configuration(cfg_path, step_names=("test",))
                traced_steps: tuple[str, ...] = ("test",)
            else:
                test_insp = probe
                traced_steps = default_step_names
            pass_results.append(
                PassResult(
                    info=PassInfo(
                        name="test",
                        args=invocation.args,
                        steps=traced_steps,
                    ),
                    inspection=test_insp,
                )
            )
        else:
            logger.info("Inspecting variant '%s'...", invocation.name)
            var_insp = inspector.inspect(
                project_dir=paths.root_dir,
                build_zig=paths.build_zig,
                extra_args=invocation.args,
            )
            pass_results.append(
                PassResult(
                    info=PassInfo(
                        name=invocation.name,
                        args=invocation.args,
                        steps=default_step_names,
                    ),
                    inspection=var_insp,
                )
            )
        if progress is not None:
            progress.advance_stage()

    pkg_meta = recipe_config.package if recipe_config is not None else None
    effective_summary = (
        pkg_meta.description
        if pkg_meta is not None and pkg_meta.description is not None
        else manifest.summary
    )
    return RecipeModel.from_passes(
        project_name=manifest.name,
        project_version=manifest.version_raw,
        summary=effective_summary,
        zig_version=inspector.version,
        dependencies=resolved_deps,
        pass_results=tuple(pass_results),
        pass_config=effective_config,
        homepage=pkg_meta.homepage if pkg_meta is not None else (),
        license=pkg_meta.license if pkg_meta is not None else None,
        gentoo_config=recipe_config.gentoo if recipe_config is not None else GentooOutputConfig(),
    )
