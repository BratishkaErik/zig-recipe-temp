"""Tests for Gentoo ebuild and generic Jinja2 renderers."""

from __future__ import annotations

from pathlib import Path

from zig_recipe.deps import ResolvedDependencies, ResolvedPackage
from zig_recipe.model import RecipeModel
from zig_recipe.renderers.gentoo import (
    CommentStyle,
    default_gentoo_filename,
    gentoo_zig_slot,
    render_gentoo_deps_only,
    render_gentoo_ebuild,
)
from zig_recipe.renderers.jinja import render_template
from zig_recipe.zig.inspector import (
    BuildInspection,
    LibraryUser,
    LibraryUserKind,
    SystemIntegration,
    SystemLibrary,
    TopLevelStep,
    UsedPackage,
    UserOption,
    ZigVersion,
)


def _make_sample_model() -> RecipeModel:
    used_pkg = ResolvedPackage(
        hash="known_folders-0.0.0-1111",
        name="known_folders",
        url="https://github.com/ziglibs/known-folders/archive/abc.tar.gz",
        kind="tarball",
        origin="converted_git",
        extension=".tar.gz",
    )
    lazy_pkg = ResolvedPackage(
        hash="wayland-0.2.0-2222",
        name="wayland",
        url="https://codeberg.org/ifreund/zig-wayland/archive/def.tar.gz",
        kind="tarball",
        origin="converted_git",
        extension=".tar.gz",
    )
    git_pkg = ResolvedPackage(
        hash="custom_git-0.1.0-3333",
        name="custom_git",
        url="git+https://selfhosted.test/custom#999",
        kind="git_commit",
        origin="git_commit",
        extension=".tar.gz",
    )
    deps = ResolvedDependencies(
        packages=(used_pkg, lazy_pkg, git_pkg),
        tarball_tarball=Path("/tmp/demo-app-1.0.0-git_dependencies.tar.gz"),
    )
    inspection = BuildInspection(
        user_options=(
            UserOption("backend", "Windowing backend", "enum", ("wayland", "x11")),
            UserOption("foo", "Enable foo", "bool", None),
        ),
        system_integrations=(
            SystemIntegration("zlib", True),
            SystemIntegration("rarelib", False),
        ),
        system_libraries=(
            SystemLibrary(
                "wayland-client",
                (LibraryUser(LibraryUserKind.ARTIFACT, "demo_app"),),
                "yes",
            ),
        ),
        top_level_steps=(
            TopLevelStep("install", "Copy build artifacts", True),
            TopLevelStep("test", "Run tests", False),
        ),
        used_packages=(UsedPackage("known_folders-0.0.0-1111", "", ""),),
        find_programs=(("wayland-scanner",),),
        has_c_sources=True,
    )
    return RecipeModel(
        project_name="demo_app",
        project_version="1.0.0",
        summary="A demo app",
        zig_version=ZigVersion.parse("0.17.1"),
        dependencies=deps,
        inspection=inspection,
    )


def test_gentoo_slot_mapping() -> None:
    assert gentoo_zig_slot(ZigVersion.parse("0.17.1")) == "0.17"
    assert gentoo_zig_slot(ZigVersion.parse("0.18.0-dev.9+abc")) == "9999"


def test_gentoo_comment_styles() -> None:
    model = _make_sample_model()
    assert default_gentoo_filename(model) == "demo-app-1.0.0.ebuild"

    full = render_gentoo_ebuild(model, comments=CommentStyle.FULL, year=2026)
    assert "EAPI=8" in full
    assert 'DESCRIPTION="A demo app"' in full
    assert "ZIG_NEEDS_LLVM=1" in full
    assert 'ZIG_SLOT="0.17"' in full
    assert "known_folders-0.0.0-1111.tar.gz" in full
    assert "wayland-0.2.0-2222.tar.gz" in full
    assert "[custom_git-0.1.0-3333.tar.gz]=''" in full
    assert "wayland-client" in full
    assert "wayland-scanner" in full
    assert "-Dbackend=[enum: wayland | x11]" in full
    assert "-fsys=zlib" in full
    assert "-fsys=rarelib" in full

    compact = render_gentoo_ebuild(model, comments=CommentStyle.COMPACT, year=2026)
    assert "Windowing backend" not in compact
    assert "-Dbackend=[enum: wayland | x11]" in compact
    assert len(compact.splitlines()) < len(full.splitlines())

    none = render_gentoo_ebuild(model, comments=CommentStyle.NONE, year=2026)
    assert "wayland-client" not in none
    assert "-Dbackend=" not in none
    assert "-fsys=zlib" in none
    assert len(none.splitlines()) < len(compact.splitlines())


def test_gentoo_deps_only() -> None:
    deps_only = render_gentoo_deps_only(_make_sample_model())
    assert deps_only.startswith("declare -g -r -A ZBS_DEPENDENCIES=(\n")
    assert "EAPI=8" not in deps_only
    assert "known_folders-0.0.0-1111.tar.gz" in deps_only
    assert "wayland-0.2.0-2222.tar.gz" in deps_only


def test_generic_jinja_template(tmp_path: Path) -> None:
    tpl = tmp_path / "recipe.j2"
    tpl.write_text(
        "{{ project.name }} {{ project.version }} {{ zig.major_minor }}\n", encoding="utf-8"
    )
    out = render_template(_make_sample_model(), template_path=tpl)
    assert out == "demo_app 1.0.0 0.17\n"
