"""Tests for ZON manifest parsing and project path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from zig_recipe.errors import ManifestError
from zig_recipe.manifest import (
    LocalDependency,
    RemoteDependency,
    parse_manifest_text,
    resolve_project_paths,
)


def test_parse_valid_manifest() -> None:
    zon = """
    .{
        .name = .demo_app,
        .version = "1.2.3",
        .fingerprint = 0xe244585b3cb2d4f9,
        .minimum_zig_version = "0.17.0",
        .summary = "Demo Zig application",
        .paths = .{ "build.zig", "src" },
        .dependencies = .{
            .remote_dep = .{
                .url = "https://example.com/dep.tar.gz",
                .hash = "dep-0.1.0-abc123",
                .lazy = true,
            },
            .local_dep = .{
                .path = "../local",
            },
            .file_dep = .{
                .url = "file://../other",
            },
        },
    }
    """
    m = parse_manifest_text(zon)
    assert m.name == "demo_app"
    assert m.version_raw == "1.2.3"
    assert m.fingerprint == 0xE244585B3CB2D4F9
    assert str(m.minimum_zig_version) == "0.17.0"
    assert m.summary == "Demo Zig application"
    assert m.paths == ("build.zig", "src")
    # Local deps sorted before remote deps
    assert isinstance(m.dependencies[0], LocalDependency)
    assert isinstance(m.dependencies[1], LocalDependency)
    assert m.dependencies[1].path == "../other"
    assert isinstance(m.dependencies[2], RemoteDependency)
    assert m.dependencies[2].lazy is True


def test_parse_invalid_semver_raises() -> None:
    with pytest.raises(ManifestError, match="Invalid semantic version"):
        parse_manifest_text('.{ .name = .foo, .version = "not-a-semver" }')


def test_resolve_project_paths(tmp_path: Path) -> None:
    (tmp_path / "build.zig").write_text("", encoding="utf-8")
    (tmp_path / "build.zig.zon").write_text(
        '.{ .name = .foo, .version = "0.1.0" }', encoding="utf-8"
    )

    by_dir = resolve_project_paths(tmp_path)
    by_build = resolve_project_paths(tmp_path / "build.zig")
    by_zon = resolve_project_paths(tmp_path / "build.zig.zon")

    assert by_dir == by_build == by_zon
    assert by_dir.root_dir == tmp_path.resolve()
