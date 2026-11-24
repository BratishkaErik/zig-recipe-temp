"""Tests for dependency URL conversion, BFS traversal, and git tarball packing."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from zig_recipe.errors import DependencyError
from zig_recipe.zig.deps import (
    PackageKind,
    ResolvedPackage,
    UrlOrigin,
    choose_best_package,
    classify_and_convert_url,
    pack_git_dependencies_tarball,
    resolve_dependencies,
)
from zig_recipe.zig.manifest import parse_manifest_text


def _pkg(
    *,
    url: str,
    origin: UrlOrigin,
    kind: PackageKind = "tarball",
) -> ResolvedPackage:
    return ResolvedPackage(
        hash="dep-0.1.0-aaaa",
        name="dep",
        url=url,
        kind=kind,
        origin=origin,
        extension=".tar.gz",
    )


@pytest.mark.parametrize(
    ("raw", "expected_url", "expected_kind", "expected_ext"),
    [
        (
            "git+https://github.com/ziglibs/known-folders.git#abcdef1234",
            "https://github.com/ziglibs/known-folders/archive/abcdef1234.tar.gz",
            "tarball",
            ".tar.gz",
        ),
        (
            "git+https://codeberg.org/ifreund/zig-wayland#deadbeef",
            "https://codeberg.org/ifreund/zig-wayland/archive/deadbeef.tar.gz",
            "tarball",
            ".tar.gz",
        ),
        (
            "git+https://gitlab.com/group/proj.git#123456",
            "https://gitlab.com/group/proj/-/archive/123456.tar.gz",
            "tarball",
            ".tar.gz",
        ),
        (
            "git+https://git.sr.ht/~user/repo#9999",
            "https://git.sr.ht/~user/repo/archive/9999.tar.gz",
            "tarball",
            ".tar.gz",
        ),
        (
            "git+https://selfhosted.example.org/foo/bar#1111",
            "git+https://selfhosted.example.org/foo/bar#1111",
            "git_commit",
            ".tar.gz",
        ),
        (
            "http://www.example.com/pkg/foo.tar.zst",
            "https://example.com/pkg/foo.tar.zst",
            "tarball",
            ".tar.zst",
        ),
    ],
)
def test_url_conversion(raw: str, expected_url: str, expected_kind: str, expected_ext: str) -> None:
    url, kind, _origin, ext = classify_and_convert_url(raw)
    assert url == expected_url
    assert kind == expected_kind
    assert ext == expected_ext


def test_package_url_priority() -> None:
    git_only = _pkg(url="git+https://x.test/r#1", origin="git_commit", kind="git_commit")
    converted = _pkg(url="https://codeberg.org/o/a/archive/1.tar.gz", origin="converted_git")
    native_first = _pkg(url="https://codeberg.org/o/a/releases/v1.tar.gz", origin="native_tarball")
    native_second = _pkg(url="https://github.com/o/a/releases/v1.tar.gz", origin="native_tarball")

    assert choose_best_package(git_only, converted) == converted
    assert choose_best_package(converted, native_first) == native_first
    # Equal priority keeps BFS order (first seen wins).
    assert choose_best_package(native_first, native_second) == native_first


def test_transitive_and_pristine_resolution(tmp_path: Path) -> None:
    packages_dir = tmp_path / "p"
    packages_dir.mkdir()

    # Child A depends on pristine package B (no build.zig.zon).
    dep_a_dir = packages_dir / "dep_a-0.1.0-aaaa"
    dep_a_dir.mkdir()
    (dep_a_dir / "build.zig.zon").write_text(
        """
        .{
            .name = .dep_a,
            .version = "0.1.0",
            .dependencies = .{
                .pristine_b = .{
                    .url = "https://example.com/b.zip",
                    .hash = "pristine_b-0.0.0-bbbb",
                },
            },
        }
        """,
        encoding="utf-8",
    )
    (packages_dir / "pristine_b-0.0.0-bbbb").mkdir()

    root_manifest = parse_manifest_text(
        """
        .{
            .name = .root_app,
            .version = "1.0.0",
            .dependencies = .{
                .dep_a = .{
                    .url = "git+https://github.com/org/dep_a#1234",
                    .hash = "dep_a-0.1.0-aaaa",
                },
            },
        }
        """
    )

    resolved = resolve_dependencies(
        root_dir=tmp_path,
        root_manifest=root_manifest,
        packages_dir=packages_dir,
    )
    assert [p.hash for p in resolved.packages] == ["dep_a-0.1.0-aaaa", "pristine_b-0.0.0-bbbb"]
    assert resolved.packages[0].name == "dep_a"
    assert resolved.packages[0].url == "https://github.com/org/dep_a/archive/1234.tar.gz"
    assert resolved.packages[1].name is None
    assert resolved.packages[1].filename == "pristine_b-0.0.0-bbbb.zip"


def test_missing_cached_package_raises(tmp_path: Path) -> None:
    root_manifest = parse_manifest_text(
        """
        .{
            .name = .root_app,
            .version = "1.0.0",
            .dependencies = .{
                .missing = .{
                    .url = "https://example.com/missing.tar.gz",
                    .hash = "missing-0.1.0-xxxx",
                },
            },
        }
        """
    )
    with pytest.raises(DependencyError, match="--no-fetch"):
        resolve_dependencies(
            root_dir=tmp_path,
            root_manifest=root_manifest,
            packages_dir=tmp_path / "p",
        )


def test_git_bundle_is_reproducible(tmp_path: Path) -> None:
    packages_dir = tmp_path / "p"
    pkg_dir = packages_dir / "dep-0.1.0-aaaa"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "hello.txt").write_text("hi", encoding="utf-8")

    pkg = _pkg(url="git+https://selfhosted.test/custom#abc", origin="git_commit", kind="git_commit")
    out = pack_git_dependencies_tarball(
        project_name="my_app",
        project_version="0.2.0",
        git_packages=(pkg,),
        packages_dir=packages_dir,
        output_dir=tmp_path / "out",
    )
    assert out is not None
    assert out.name == "my-app-0.2.0-git_dependencies.tar.gz"
    with tarfile.open(out, "r:gz") as tf:
        members = tf.getmembers()
        assert [m.name for m in members] == ["dep-0.1.0-aaaa.tar.gz"]
        assert members[0].mtime == 1
        assert members[0].uid == 0
