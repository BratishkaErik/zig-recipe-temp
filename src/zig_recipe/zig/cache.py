"""Cross-platform cache directory management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir


@dataclass(frozen=True, slots=True)
class CacheDirs:
    """Managed cache directories for zig-recipe."""

    root: Path
    packages_dir: Path
    tools_dir: Path
    git_tarballs_dir: Path
    zig_cache_dir: Path

    @classmethod
    def open(cls, root: Path | None = None) -> CacheDirs:
        base = root.resolve() if root is not None else Path(user_cache_dir("zig-recipe"))
        packages_dir = base / "deps" / "p"
        tools_dir = base / "tools"
        git_tarballs_dir = base / "git_commit_tarballs"
        zig_cache_dir = base / "zig-cache"

        for d in (packages_dir, tools_dir, git_tarballs_dir, zig_cache_dir):
            d.mkdir(parents=True, exist_ok=True)

        return cls(
            root=base,
            packages_dir=packages_dir,
            tools_dir=tools_dir,
            git_tarballs_dir=git_tarballs_dir,
            zig_cache_dir=zig_cache_dir,
        )
