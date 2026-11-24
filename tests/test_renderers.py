"""Tests for Gentoo ebuild and generic Jinja2 renderers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from zig_recipe.config import GentooOutputConfig, PassConfig, VariantSpec
from zig_recipe.errors import ConfigError
from zig_recipe.gentoo.renderer import (
    CommentStyle,
    UnusedDepsMode,
    default_gentoo_filename,
    gentoo_zig_slot,
    render_gentoo_deps_only,
    render_gentoo_ebuild,
    resolve_unused_deps_mode,
)
from zig_recipe.jinja import render_template
from zig_recipe.model import PassInfo, PassResult, RecipeModel, UsageKind
from zig_recipe.zig.deps import ResolvedDependencies, ResolvedPackage
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
    cond_pkg = ResolvedPackage(
        hash="test_helper-0.1.0-4444",
        name="test_helper",
        url="https://github.com/example/test-helper/archive/444.tar.gz",
        kind="tarball",
        origin="native_tarball",
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
        packages=(used_pkg, cond_pkg, lazy_pkg, git_pkg),
        tarball_tarball=Path("/tmp/demo-app-1.0.0-git_dependencies.tar.gz"),
    )
    default_insp = BuildInspection(
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
    test_insp = BuildInspection(
        user_options=default_insp.user_options,
        system_integrations=default_insp.system_integrations,
        system_libraries=(
            SystemLibrary(
                "check",
                (LibraryUser(LibraryUserKind.ARTIFACT, "test"),),
                "yes",
            ),
        ),
        top_level_steps=default_insp.top_level_steps,
        used_packages=(
            UsedPackage("known_folders-0.0.0-1111", "", ""),
            UsedPackage("test_helper-0.1.0-4444", "", ""),
        ),
        find_programs=(("scdoc",),),
        has_c_sources=False,
    )
    return RecipeModel.from_passes(
        project_name="demo_app",
        project_version="1.0.0",
        summary="A demo app",
        zig_version=ZigVersion.parse("0.17.1"),
        dependencies=deps,
        pass_results=(
            PassResult(PassInfo("default", (), ("install",)), default_insp),
            PassResult(PassInfo("test", (), ("test",)), test_insp),
        ),
    )


def test_gentoo_slot_mapping() -> None:
    assert gentoo_zig_slot(ZigVersion.parse("0.17.1")) == "0.17"
    assert gentoo_zig_slot(ZigVersion.parse("0.18.0-dev.9+abc")) == "9999"


def test_unused_deps_mode_resolution() -> None:
    assert resolve_unused_deps_mode(CommentStyle.FULL, None) is UnusedDepsMode.COMMENT
    assert resolve_unused_deps_mode(CommentStyle.COMPACT, None) is UnusedDepsMode.COMMENT
    assert resolve_unused_deps_mode(CommentStyle.NONE, None) is UnusedDepsMode.DROP
    assert resolve_unused_deps_mode(CommentStyle.NONE, UnusedDepsMode.KEEP) is UnusedDepsMode.KEEP
    assert resolve_unused_deps_mode(CommentStyle.NONE, UnusedDepsMode.DROP) is UnusedDepsMode.DROP
    with pytest.raises(ConfigError):
        resolve_unused_deps_mode(CommentStyle.NONE, UnusedDepsMode.COMMENT)


def test_gentoo_comment_styles_and_unused_modes() -> None:
    model = _make_sample_model()
    assert default_gentoo_filename(model) == "demo-app-1.0.0.ebuild"

    # Full + default unused_deps=comment
    full = render_gentoo_ebuild(model, comments=CommentStyle.FULL, year=2026)
    assert "EAPI=8" in full
    assert 'DESCRIPTION="A demo app"' in full
    assert "ZIG_NEEDS_LLVM=1" in full
    assert 'ZIG_SLOT="0.17"' in full
    assert "\t[known_folders-0.0.0-1111.tar.gz]=" in full
    assert "\t[test_helper-0.1.0-4444.tar.gz]=" in full
    assert "\t# [wayland-0.2.0-2222.tar.gz]=" in full
    assert "[custom_git-0.1.0-3333.tar.gz]=''" in full
    assert "wayland-client" in full
    assert "check (test?)" in full
    assert "wayland-scanner" in full
    assert "scdoc (test?)" in full
    assert "-Dbackend=[enum: wayland | x11]" in full
    assert "-fsys=zlib" in full
    assert "-fsys=rarelib" in full

    # Full + unused_deps=drop omits unused tarball
    full_drop = render_gentoo_ebuild(
        model, comments=CommentStyle.FULL, unused_deps=UnusedDepsMode.DROP, year=2026
    )
    assert "wayland-0.2.0-2222.tar.gz" not in full_drop

    # Full + unused_deps=keep includes unused tarball uncommented
    full_keep = render_gentoo_ebuild(
        model, comments=CommentStyle.FULL, unused_deps=UnusedDepsMode.KEEP, year=2026
    )
    assert "\t[wayland-0.2.0-2222.tar.gz]=" in full_keep

    # Compact comments
    compact = render_gentoo_ebuild(model, comments=CommentStyle.COMPACT, year=2026)
    assert "Windowing backend" not in compact
    assert "check (test?)" in compact
    assert "scdoc (test?)" in compact
    assert "-Dbackend=[enum: wayland | x11]" in compact
    assert len(compact.splitlines()) < len(full.splitlines())

    # None comments defaults unused_deps to drop
    none = render_gentoo_ebuild(model, comments=CommentStyle.NONE, year=2026)
    assert "wayland-client" not in none
    assert "-Dbackend=" not in none
    assert "-fsys=zlib" in none
    assert "wayland-0.2.0-2222.tar.gz" not in none
    assert len(none.splitlines()) < len(compact.splitlines())


def test_gentoo_deps_only() -> None:
    model = _make_sample_model()
    deps_only = render_gentoo_deps_only(model)
    assert deps_only.startswith("declare -g -r -A ZBS_DEPENDENCIES=(\n")
    assert "EAPI=8" not in deps_only
    assert "\t[known_folders-0.0.0-1111.tar.gz]=" in deps_only
    assert "\t[test_helper-0.1.0-4444.tar.gz]=" in deps_only
    assert "\t# [wayland-0.2.0-2222.tar.gz]=" in deps_only

    deps_drop = render_gentoo_deps_only(model, unused_deps=UnusedDepsMode.DROP)
    assert "wayland-0.2.0-2222.tar.gz" not in deps_drop


def test_model_json_schema_and_generic_jinja(tmp_path: Path) -> None:
    model = _make_sample_model()
    data = model.to_dict()
    assert [p["name"] for p in data["passes"]] == ["default", "test"]
    pkg_usage = {p["hash"]: (p["usage"], p["used_in"]) for p in data["dependencies"]["packages"]}
    assert pkg_usage["known_folders-0.0.0-1111"] == (UsageKind.ALWAYS.value, ["default", "test"])
    assert pkg_usage["test_helper-0.1.0-4444"] == (UsageKind.CONDITIONAL.value, ["test"])
    assert pkg_usage["wayland-0.2.0-2222"] == (UsageKind.UNUSED.value, [])

    tpl = tmp_path / "recipe.j2"
    tpl.write_text(
        "{{ project.name }} {{ project.version }} {{ passes | length }}\n", encoding="utf-8"
    )
    out = render_template(model, template_path=tpl)
    assert out == "demo_app 1.0.0 2\n"


def test_gentoo_iuse_and_smart_src_configure() -> None:
    base_model = _make_sample_model()
    pass_cfg = PassConfig(
        base_args=("-Dpie=true", "-fsys=zlib"),
        variants=(
            VariantSpec("X", ("-Dbackend=x11",), ("-Dbackend=wayland",)),
            VariantSpec("foo", ("-Dfoo=true",)),
            VariantSpec("extra", ("-Da=1", "-Db=2"), ("-Da=0",)),
        ),
    )
    model = replace(
        base_model,
        pass_config=pass_cfg,
        homepage=("https://example.org/demo", "https://codeberg.org/example/demo"),
        license="GPL-3.0-only",
        gentoo_config=GentooOutputConfig(
            src_uri=("https://example.org/demo/v${PV}.tar.gz",),
            keywords="~amd64 ~arm64",
            license="GPL-3",
        ),
    )

    out = render_gentoo_ebuild(model, comments=CommentStyle.COMPACT, year=2026)
    assert 'HOMEPAGE="https://example.org/demo https://codeberg.org/example/demo"' in out
    assert out.index("inherit zig") < out.index('DESCRIPTION="')
    assert 'LICENSE="GPL-3"' in out
    assert 'KEYWORDS="~amd64 ~arm64"' in out
    assert "\thttps://example.org/demo/v${PV}.tar.gz\n" in out
    assert "# TODO: add project source URL" not in out
    assert 'IUSE="X extra foo test"' in out
    assert 'RESTRICT="!test? ( test )"' in out
    assert "\t\t-Dpie=true\n" in out
    assert "\t\t-Dbackend=$(usex X x11 wayland)\n" in out
    assert "\t\t$(usev foo -Dfoo=true)\n" in out
    expected_if = (
        "if use extra; then\n"
        "\t\tmy_zbs_args+=( -Da=1 -Db=2 )\n"
        "\telse\n"
        "\t\tmy_zbs_args+=( -Da=0 )\n"
        "\tfi"
    )
    assert expected_if in out
    # Already-configured options are not duplicated as commented placeholders.
    assert "# -Dbackend=" not in out
    assert "# -Dfoo=" not in out
    # -fsys=zlib from base_args is not duplicated alongside system_integrations.
    assert out.count("-fsys=zlib") == 1


def test_gentoo_experimental_zbs_packages_and_fno_sys() -> None:
    model = replace(
        _make_sample_model(),
        pass_config=PassConfig(base_args=("-fno-sys=rarelib",)),
    )

    out = render_gentoo_ebuild(
        model, comments=CommentStyle.COMPACT, use_zbs_packages=True, year=2026
    )
    assert "declare -g -r -A ZBS_PACKAGES=(" in out
    assert "declare -g -r -A ZBS_DEPENDENCIES=(" not in out
    assert "\t[known_folders]=" in out
    assert "\t[test_helper]=" in out
    assert "\t# [wayland]=" in out
    assert "declare -g -r -a ZBS_BUNDLED_PACKAGES=(" in out
    assert "\tcustom_git-0.1.0-3333.tar.gz\n" in out
    assert "\t${ZBS_PACKAGES[known_folders]}\n" in out
    assert "\ttest? ( ${ZBS_PACKAGES[test_helper]} )\n" in out
    # -fno-sys=rarelib suppresses auto-emitted -fsys=rarelib.
    assert "-fno-sys=rarelib" in out
    assert "-fsys=rarelib" not in out

    deps_out = render_gentoo_deps_only(model, use_zbs_packages=True)
    assert deps_out.startswith("declare -g -r -A ZBS_PACKAGES=(\n")
    assert "declare -g -r -a ZBS_BUNDLED_PACKAGES=(" in deps_out
