"""Format-agnostic intermediate representation (RecipeModel) for zig-recipe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zig_recipe.deps import ResolvedDependencies, ResolvedPackage
from zig_recipe.zig.inspector import (
    BuildInspection,
    SystemIntegration,
    SystemLibrary,
    TopLevelStep,
    UserOption,
    ZigVersion,
)


@dataclass(frozen=True, slots=True)
class RecipeModel:
    """Format-agnostic model of an analyzed Zig project."""

    project_name: str
    project_version: str
    summary: str | None
    zig_version: ZigVersion
    dependencies: ResolvedDependencies
    inspection: BuildInspection

    @property
    def used_tarballs(self) -> tuple[ResolvedPackage, ...]:
        used = self.inspection.used_package_hashes
        return tuple(p for p in self.dependencies.tarballs if p.hash in used)

    @property
    def unused_tarballs(self) -> tuple[ResolvedPackage, ...]:
        used = self.inspection.used_package_hashes
        return tuple(p for p in self.dependencies.tarballs if p.hash not in used)

    @property
    def git_commits(self) -> tuple[ResolvedPackage, ...]:
        return self.dependencies.git_commits

    @property
    def tarball_tarball(self) -> Path | None:
        return self.dependencies.tarball_tarball

    @property
    def user_options(self) -> tuple[UserOption, ...]:
        return self.inspection.user_options

    @property
    def system_integrations(self) -> tuple[SystemIntegration, ...]:
        return self.inspection.system_integrations

    @property
    def system_libraries(self) -> tuple[SystemLibrary, ...]:
        return self.inspection.system_libraries

    @property
    def top_level_steps(self) -> tuple[TopLevelStep, ...]:
        return self.inspection.top_level_steps

    @property
    def find_programs(self) -> tuple[tuple[str, ...], ...]:
        return self.inspection.find_programs

    @property
    def has_c_sources(self) -> bool:
        return self.inspection.has_c_sources

    def to_dict(self) -> dict[str, Any]:
        """Serialize RecipeModel to a JSON-compatible dict."""
        used = self.inspection.used_package_hashes
        return {
            "project": {
                "name": self.project_name,
                "version": self.project_version,
                "summary": self.summary,
            },
            "zig": {
                "version": self.zig_version.raw,
                "major_minor": self.zig_version.major_minor,
                "is_live": self.zig_version.is_live,
            },
            "dependencies": {
                "packages": [
                    {
                        "hash": p.hash,
                        "name": p.name,
                        "url": p.url,
                        "kind": p.kind,
                        "origin": p.origin,
                        "filename": p.filename,
                        "used": p.hash in used,
                    }
                    for p in self.dependencies.packages
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
                "find_programs": [list(names) for names in self.find_programs],
            },
        }
