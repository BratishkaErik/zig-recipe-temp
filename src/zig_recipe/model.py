"""Format-agnostic intermediate representation (RecipeModel) for zig-recipe."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from zig_recipe.config import GentooOutputConfig, PassConfig
from zig_recipe.zig.deps import ResolvedDependencies, ResolvedPackage
from zig_recipe.zig.inspector import (
    BuildInspection,
    LibraryUser,
    SystemIntegration,
    SystemLibrary,
    TopLevelStep,
    UserOption,
    ZigVersion,
)


class UsageKind(StrEnum):
    """Classification of whether an item is always needed, conditional, or unused."""

    ALWAYS = "always"
    CONDITIONAL = "conditional"
    UNUSED = "unused"


def classify_usage(used_in: tuple[str, ...]) -> UsageKind:
    """Classify an item based on which passes used it."""
    if not used_in:
        return UsageKind.UNUSED
    if "default" in used_in:
        return UsageKind.ALWAYS
    return UsageKind.CONDITIONAL


@dataclass(frozen=True, slots=True)
class PassInfo:
    """Metadata describing a single inspection pass."""

    name: str
    args: tuple[str, ...]
    steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PassResult:
    """A completed inspection pass paired with its PassInfo."""

    info: PassInfo
    inspection: BuildInspection


@dataclass(frozen=True, slots=True)
class AggregatedPackage:
    """A resolved package annotated with multi-pass usage classification."""

    package: ResolvedPackage
    usage: UsageKind
    used_in: tuple[str, ...]

    @property
    def hash(self) -> str:
        return self.package.hash

    @property
    def name(self) -> str | None:
        return self.package.name

    @property
    def url(self) -> str:
        return self.package.url

    @property
    def kind(self) -> str:
        return self.package.kind

    @property
    def origin(self) -> str:
        return self.package.origin

    @property
    def filename(self) -> str:
        return self.package.filename


@dataclass(frozen=True, slots=True)
class AggregatedLibrary:
    """A system library annotated with multi-pass usage classification."""

    library: SystemLibrary
    usage: UsageKind
    used_in: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.library.name

    @property
    def used_by(self) -> tuple[LibraryUser, ...]:
        return self.library.used_by

    @property
    def use_pkg_config(self) -> str:
        return self.library.use_pkg_config

    @property
    def pkg_config_hint(self) -> str | None:
        return self.library.pkg_config_hint


@dataclass(frozen=True, slots=True)
class AggregatedProgram:
    """A `findProgramLazy` candidate list annotated with multi-pass usage classification."""

    names: tuple[str, ...]
    usage: UsageKind
    used_in: tuple[str, ...]


def _aggregate_packages(
    dependencies: ResolvedDependencies,
    pass_results: tuple[PassResult, ...],
) -> tuple[AggregatedPackage, ...]:
    """Annotate resolved packages with multi-pass usage classification."""
    pkg_passes: dict[str, list[str]] = defaultdict(list)
    for pr in pass_results:
        for pkg_hash in pr.inspection.used_package_hashes:
            pkg_passes[pkg_hash].append(pr.info.name)

    return tuple(
        AggregatedPackage(
            package=pkg,
            usage=classify_usage(tuple(pkg_passes.get(pkg.hash, ()))),
            used_in=tuple(pkg_passes.get(pkg.hash, ())),
        )
        for pkg in dependencies.packages
    )


def _aggregate_libraries(
    pass_results: tuple[PassResult, ...],
) -> tuple[AggregatedLibrary, ...]:
    """Merge system libraries across all passes, preserving strongest pkg-config mode."""
    lib_users: dict[str, set[LibraryUser]] = defaultdict(set)
    lib_pkgcfg: dict[str, str] = {}
    lib_passes: dict[str, list[str]] = defaultdict(list)
    pkgcfg_rank = {"no": 0, "yes": 1, "force": 2}

    for pr in pass_results:
        for sl in pr.inspection.system_libraries:
            lib_users[sl.name].update(sl.used_by)
            prev = lib_pkgcfg.get(sl.name)
            if prev is None or pkgcfg_rank.get(sl.use_pkg_config, 1) > pkgcfg_rank.get(prev, 1):
                lib_pkgcfg[sl.name] = sl.use_pkg_config
            if pr.info.name not in lib_passes[sl.name]:
                lib_passes[sl.name].append(pr.info.name)

    return tuple(
        AggregatedLibrary(
            library=SystemLibrary(
                name=name,
                used_by=tuple(sorted(lib_users[name], key=lambda u: (u.kind, u.name))),
                use_pkg_config=lib_pkgcfg[name],
            ),
            usage=classify_usage(tuple(lib_passes[name])),
            used_in=tuple(lib_passes[name]),
        )
        for name in sorted(lib_users)
    )


def _aggregate_programs(
    pass_results: tuple[PassResult, ...],
) -> tuple[AggregatedProgram, ...]:
    """Merge `findProgramLazy` candidate lists across passes in first-seen order."""
    prog_order: list[tuple[str, ...]] = []
    prog_passes: dict[tuple[str, ...], list[str]] = {}
    for pr in pass_results:
        for names in pr.inspection.find_programs:
            if names not in prog_passes:
                prog_order.append(names)
                prog_passes[names] = []
            if pr.info.name not in prog_passes[names]:
                prog_passes[names].append(pr.info.name)

    return tuple(
        AggregatedProgram(
            names=names,
            usage=classify_usage(tuple(prog_passes[names])),
            used_in=tuple(prog_passes[names]),
        )
        for names in prog_order
    )


@dataclass(frozen=True, slots=True)
class RecipeModel:
    """Format-agnostic model of an analyzed Zig project (single- or multi-pass)."""

    project_name: str
    project_version: str
    summary: str | None
    zig_version: ZigVersion
    dependencies: ResolvedDependencies
    passes: tuple[PassInfo, ...]
    packages: tuple[AggregatedPackage, ...]
    system_libraries: tuple[AggregatedLibrary, ...]
    find_programs: tuple[AggregatedProgram, ...]
    user_options: tuple[UserOption, ...]
    system_integrations: tuple[SystemIntegration, ...]
    top_level_steps: tuple[TopLevelStep, ...]
    has_c_sources: bool
    pass_config: PassConfig = PassConfig()
    homepage: tuple[str, ...] = ()
    license: str | None = None
    gentoo_config: GentooOutputConfig = GentooOutputConfig()

    @classmethod
    def from_passes(
        cls,
        *,
        project_name: str,
        project_version: str,
        summary: str | None,
        zig_version: ZigVersion,
        dependencies: ResolvedDependencies,
        pass_results: tuple[PassResult, ...],
        pass_config: PassConfig | None = None,
        homepage: tuple[str, ...] = (),
        license: str | None = None,
        gentoo_config: GentooOutputConfig | None = None,
    ) -> RecipeModel:
        """Aggregate one or more inspection passes into a unified RecipeModel."""
        if not pass_results:
            raise ValueError("RecipeModel.from_passes requires at least one PassResult.")

        passes = tuple(pr.info for pr in pass_results)
        primary = pass_results[0].inspection

        agg_packages = _aggregate_packages(dependencies, pass_results)
        agg_libraries = _aggregate_libraries(pass_results)
        agg_programs = _aggregate_programs(pass_results)

        # Union user options and system integrations (preserving primary order).
        opt_by_name: dict[str, UserOption] = {o.name: o for o in primary.user_options}
        for pr in pass_results[1:]:
            for opt in pr.inspection.user_options:
                opt_by_name.setdefault(opt.name, opt)

        si_enabled: dict[str, bool] = {si.name: si.enabled for si in primary.system_integrations}
        for pr in pass_results[1:]:
            for si in pr.inspection.system_integrations:
                si_enabled[si.name] = si_enabled.get(si.name, False) or si.enabled
        merged_integrations = tuple(
            sorted(
                (SystemIntegration(name=n, enabled=e) for n, e in si_enabled.items()),
                key=lambda si: (not si.enabled, si.name),
            )
        )

        has_c = any(pr.inspection.has_c_sources for pr in pass_results)

        return cls(
            project_name=project_name,
            project_version=project_version,
            summary=summary,
            zig_version=zig_version,
            dependencies=dependencies,
            passes=passes,
            packages=agg_packages,
            system_libraries=agg_libraries,
            find_programs=agg_programs,
            user_options=tuple(opt_by_name.values()),
            system_integrations=merged_integrations,
            top_level_steps=primary.top_level_steps,
            has_c_sources=has_c,
            pass_config=pass_config if pass_config is not None else PassConfig(),
            homepage=homepage,
            license=license,
            gentoo_config=gentoo_config if gentoo_config is not None else GentooOutputConfig(),
        )

    @property
    def used_tarballs(self) -> tuple[AggregatedPackage, ...]:
        return tuple(
            p for p in self.packages if p.kind == "tarball" and p.usage is not UsageKind.UNUSED
        )

    @property
    def always_tarballs(self) -> tuple[AggregatedPackage, ...]:
        return tuple(
            p for p in self.packages if p.kind == "tarball" and p.usage is UsageKind.ALWAYS
        )

    @property
    def conditional_tarballs(self) -> tuple[AggregatedPackage, ...]:
        return tuple(
            p for p in self.packages if p.kind == "tarball" and p.usage is UsageKind.CONDITIONAL
        )

    @property
    def unused_tarballs(self) -> tuple[AggregatedPackage, ...]:
        return tuple(
            p for p in self.packages if p.kind == "tarball" and p.usage is UsageKind.UNUSED
        )

    @property
    def git_commits(self) -> tuple[AggregatedPackage, ...]:
        return tuple(p for p in self.packages if p.kind == "git_commit")

    @property
    def tarball_tarball(self) -> Path | None:
        return self.dependencies.tarball_tarball

    @property
    def always_libraries(self) -> tuple[AggregatedLibrary, ...]:
        return tuple(lib for lib in self.system_libraries if lib.usage is UsageKind.ALWAYS)

    @property
    def conditional_libraries(self) -> tuple[AggregatedLibrary, ...]:
        return tuple(lib for lib in self.system_libraries if lib.usage is UsageKind.CONDITIONAL)

    @property
    def always_programs(self) -> tuple[AggregatedProgram, ...]:
        return tuple(prog for prog in self.find_programs if prog.usage is UsageKind.ALWAYS)

    @property
    def conditional_programs(self) -> tuple[AggregatedProgram, ...]:
        return tuple(prog for prog in self.find_programs if prog.usage is UsageKind.CONDITIONAL)

    def to_dict(self) -> dict[str, Any]:
        """Serialize RecipeModel to a JSON-compatible dict."""
        return {
            "project": {
                "name": self.project_name,
                "version": self.project_version,
                "summary": self.summary,
                "homepage": list(self.homepage),
                "license": self.license,
            },
            "zig": {
                "version": self.zig_version.raw,
                "major_minor": self.zig_version.major_minor,
                "is_live": self.zig_version.is_live,
            },
            "passes": [
                {
                    "name": p.name,
                    "args": list(p.args),
                    "steps": list(p.steps),
                }
                for p in self.passes
            ],
            "dependencies": {
                "packages": [
                    {
                        "hash": p.hash,
                        "name": p.name,
                        "url": p.url,
                        "kind": p.kind,
                        "origin": p.origin,
                        "filename": p.filename,
                        "usage": p.usage.value,
                        "used_in": list(p.used_in),
                    }
                    for p in self.packages
                ],
                "tarball_tarball": str(self.tarball_tarball) if self.tarball_tarball else None,
            },
            "build": {
                "has_c_sources": self.has_c_sources,
                "user_options": [
                    {
                        "name": o.name,
                        "description": o.description,
                        "type": o.type_id,
                        "values": list(o.values) if o.values is not None else None,
                    }
                    for o in self.user_options
                ],
                "system_integrations": [
                    {"name": si.name, "enabled": si.enabled} for si in self.system_integrations
                ],
                "system_libraries": [
                    {
                        "name": sl.name,
                        "used_by": [{"kind": u.kind.value, "name": u.name} for u in sl.used_by],
                        "use_pkg_config": sl.use_pkg_config,
                        "usage": sl.usage.value,
                        "used_in": list(sl.used_in),
                    }
                    for sl in self.system_libraries
                ],
                "top_level_steps": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "is_default": s.is_default,
                    }
                    for s in self.top_level_steps
                ],
                "find_programs": [
                    {
                        "names": list(prog.names),
                        "usage": prog.usage.value,
                        "used_in": list(prog.used_in),
                    }
                    for prog in self.find_programs
                ],
            },
        }
