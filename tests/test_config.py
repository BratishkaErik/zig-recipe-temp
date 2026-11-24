"""Tests for multi-pass configuration loading and pass-plan expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from zig_recipe.config import (
    GeneralConfig,
    GentooOutputConfig,
    PackageMetadata,
    PassConfig,
    UserConfig,
    VariantSpec,
    build_zig_invocations,
    load_config_file,
    load_config_text,
    load_user_config_text,
    merge_gentoo_config,
    merge_pass_config,
)
from zig_recipe.errors import ConfigError


def test_load_config_text_validates_reserved_and_test_variants() -> None:
    cfg = load_config_text('[build.variants]\ntest = ["-Denable-tests=true"]\n')
    assert cfg.build.variants == (VariantSpec(name="test", on_args=("-Denable-tests=true",)),)

    with pytest.raises(ConfigError):
        load_config_text('[build.variants]\ndefault = ["-Dfoo=true"]\n')
    with pytest.raises(ConfigError):
        load_config_text("[build.variants]\nempty = []\n")


def test_load_config_file_supports_full_schema_and_variants(tmp_path: Path) -> None:
    cfg_path = tmp_path / "zig-recipe.toml"
    cfg_path.write_text(
        """
[package]
description = "Demo compositor"
homepage = ["https://example.org/demo", "https://codeberg.org/example/demo"]
license = "GPL-3.0-only"

[build]
base_args = ["-Dapp-runtime=gtk", "-fsys=freetype"]

[build.variants]
man = ["-Demit-docs=true"]
X = { on = ["-Dgtk-x11=true"], off = ["-Dgtk-x11=false"] }
wayland = { on = ["-Dgtk-wayland=true"], off = ["-Dgtk-wayland=false"] }

[output.gentoo]
comments = "compact"
unused_deps = "drop"
src_uri = "https://example.org/demo/v${PV}.tar.gz"
keywords = ["~amd64", "~arm64"]
license = "GPL-3"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config_file(cfg_path)
    assert cfg.package == PackageMetadata(
        description="Demo compositor",
        homepage=("https://example.org/demo", "https://codeberg.org/example/demo"),
        license="GPL-3.0-only",
    )
    assert cfg.gentoo == GentooOutputConfig(
        comments="compact",
        unused_deps="drop",
        src_uri=("https://example.org/demo/v${PV}.tar.gz",),
        keywords="~amd64 ~arm64",
        license="GPL-3",
    )
    assert cfg.build.base_args == ("-Dapp-runtime=gtk", "-fsys=freetype")
    assert cfg.build.variants == (
        VariantSpec("man", ("-Demit-docs=true",)),
        VariantSpec("X", ("-Dgtk-x11=true",), ("-Dgtk-x11=false",)),
        VariantSpec("wayland", ("-Dgtk-wayland=true",), ("-Dgtk-wayland=false",)),
    )

    plan = build_zig_invocations(cfg.build)
    assert [(inv.name, inv.args) for inv in plan] == [
        (
            "default",
            ("-Dapp-runtime=gtk", "-fsys=freetype", "-Dgtk-x11=false", "-Dgtk-wayland=false"),
        ),
        (
            "man",
            (
                "-Dapp-runtime=gtk",
                "-fsys=freetype",
                "-Dgtk-x11=false",
                "-Dgtk-wayland=false",
                "-Demit-docs=true",
            ),
        ),
        (
            "X",
            ("-Dapp-runtime=gtk", "-fsys=freetype", "-Dgtk-wayland=false", "-Dgtk-x11=true"),
        ),
        (
            "wayland",
            ("-Dapp-runtime=gtk", "-fsys=freetype", "-Dgtk-x11=false", "-Dgtk-wayland=true"),
        ),
    ]


def test_merge_pass_config_appends_cli_base_args() -> None:
    file_cfg = PassConfig(
        base_args=("-Dpie=true",),
        variants=(
            VariantSpec("X", ("-Dx=1",), ("-Dx=0",)),
            VariantSpec("man", ("-Dman=true",)),
        ),
    )
    merged = merge_pass_config(
        file_config=file_cfg,
        cli_base_args=("-Dstrip=false",),
    )
    assert merged.base_args == ("-Dpie=true", "-Dstrip=false")
    assert merged.variants == (
        VariantSpec("X", ("-Dx=1",), ("-Dx=0",)),
        VariantSpec("man", ("-Dman=true",)),
    )


def test_load_config_file_rejects_unknown_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad.toml"
    cfg_path.write_text("unknown = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config_file(cfg_path)


def test_variant_overrides_base_d_option_instead_of_duplicating() -> None:
    cfg = PassConfig(
        base_args=("-Dbackend=headless", "-Duse-c=true", "-fsys=zlib"),
        variants=(VariantSpec("x11", ("-Dbackend=x11", "-fsys=x11")),),
    )
    plan = build_zig_invocations(cfg)
    assert [(inv.name, inv.args) for inv in plan] == [
        ("default", ("-Dbackend=headless", "-Duse-c=true", "-fsys=zlib")),
        ("x11", ("-Duse-c=true", "-fsys=zlib", "-Dbackend=x11", "-fsys=x11")),
    ]


def test_user_config_parsing_and_merging() -> None:
    user = load_user_config_text(
        """
[general]
zig = "zig-0.17.1"
cache_dir = "/tmp/my-cache"

[output.gentoo]
comments = "compact"
unused_deps = "drop"
keywords = ["~amd64", "~arm64"]
"""
    )
    assert user == UserConfig(
        general=GeneralConfig(zig="zig-0.17.1", cache_dir=Path("/tmp/my-cache")),
        gentoo=GentooOutputConfig(
            comments="compact",
            unused_deps="drop",
            keywords="~amd64 ~arm64",
        ),
    )

    merged = merge_gentoo_config(
        user=user.gentoo,
        project=GentooOutputConfig(
            comments="full",
            src_uri=("https://example.org/${P}.tar.gz",),
            license="GPL-3",
        ),
    )
    assert merged == GentooOutputConfig(
        comments="full",
        unused_deps="drop",
        src_uri=("https://example.org/${P}.tar.gz",),
        keywords="~amd64 ~arm64",
        license="GPL-3",
    )

    # Project-only sections and keys are rejected in global user config.
    with pytest.raises(ConfigError):
        load_user_config_text('[package]\ndescription = "no"\n')
    with pytest.raises(ConfigError):
        load_user_config_text('[output.gentoo]\nsrc_uri = "https://example.org/a.tar.gz"\n')
