"""Real Zig 0.17+ integration tests using fixture projects."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zig_recipe.cli import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"
USED_HASH = "used_dep-0.1.0-11111111111111111111111111111111111111111111"
UNUSED_HASH = "unused_lazy-0.1.0-22222222222222222222222222222222222222222222"


def _resolve_zig() -> str | None:
    env = os.environ.get("ZIG_EXE")
    if env:
        return env
    return shutil.which("zig")


ZIG_EXE = _resolve_zig()

pytestmark = [
    pytest.mark.requires_zig,
    pytest.mark.skipif(ZIG_EXE is None, reason="No Zig executable found (set ZIG_EXE or PATH)"),
]


@dataclass(frozen=True, slots=True)
class ZigTestEnv:
    """Shared staged fixture project and cache (so dump_config.zig compiles once)."""

    proj: Path
    cache_dir: Path
    common: tuple[str, ...]
    runner: CliRunner


@pytest.fixture(scope="module")
def zig_env(tmp_path_factory: pytest.TempPathFactory) -> ZigTestEnv:
    assert ZIG_EXE is not None
    root = tmp_path_factory.mktemp("zig017")
    proj = root / "demo_app"
    shutil.copytree(FIXTURES_DIR / "demo_app", proj)

    cache_dir = root / "cache"
    p_dir = cache_dir / "deps" / "p"
    shutil.copytree(FIXTURES_DIR / "cached_deps", p_dir)
    (p_dir / UNUSED_HASH).mkdir(parents=True, exist_ok=True)

    common = ("--zig", ZIG_EXE, "--cache-dir", str(cache_dir), "--no-fetch", "--no-user-config")
    return ZigTestEnv(proj=proj, cache_dir=cache_dir, common=common, runner=CliRunner())


def test_gentoo_default_ebuild(zig_env: ZigTestEnv) -> None:
    """Default ebuild generation checks used vs commented unused deps and automatic test step."""
    res = zig_env.runner.invoke(app, ["gentoo", str(zig_env.proj), "-o", "-", *zig_env.common])
    assert res.exit_code == 0, res.output
    assert "EAPI=8" in res.stdout
    assert "ZIG_NEEDS_LLVM=1" not in res.stdout
    assert "should-not-leak" not in res.stdout
    assert "wayland-client" in res.stdout
    assert "check-test-lib (test?)" in res.stdout
    assert 'IUSE="test"' in res.stdout
    assert 'RESTRICT="!test? ( test )"' in res.stdout
    assert "-fsys=zlib" in res.stdout
    assert f"\t[{USED_HASH}.tar.gz]=" in res.stdout
    assert f"\t# [{UNUSED_HASH}.tar.gz]=" in res.stdout
    assert res.stdout.index(USED_HASH) < res.stdout.index(UNUSED_HASH)


def test_gentoo_deps_only_and_option_conflict(zig_env: ZigTestEnv) -> None:
    """--deps-only drops unused deps when requested; conflicting flags fail cleanly."""
    res_deps = zig_env.runner.invoke(
        app,
        ["gentoo", str(zig_env.proj), "--deps-only", "--unused-deps", "drop", *zig_env.common],
    )
    assert res_deps.exit_code == 0, res_deps.output
    assert res_deps.stdout.startswith("declare -g -r -A ZBS_DEPENDENCIES=(\n")
    assert "EAPI=8" not in res_deps.stdout
    assert UNUSED_HASH not in res_deps.stdout

    res_conflict = zig_env.runner.invoke(
        app,
        [
            "gentoo",
            str(zig_env.proj),
            "--comments",
            "none",
            "--unused-deps",
            "comment",
            *zig_env.common,
        ],
    )
    assert res_conflict.exit_code != 0


def test_inspect_stdin_config_and_base_args(zig_env: ZigTestEnv) -> None:
    """Multi-pass via --config - (stdin) + base args after `--` detects X11 and test libs."""
    res_json = zig_env.runner.invoke(
        app,
        [
            "inspect",
            str(zig_env.proj),
            "--format",
            "json",
            "--config",
            "-",
            *zig_env.common,
            "--",
            "-Dbackend=headless",
            "-Duse-c=true",
        ],
        input='[build.variants]\nx11 = ["-Dbackend=x11"]\n',
    )
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert data["project"]["name"] == "demo_app"
    assert data["build"]["has_c_sources"] is True
    assert [p["name"] for p in data["passes"]] == ["default", "test", "x11"]

    libs = {sl["name"]: (sl["usage"], sl["used_in"]) for sl in data["build"]["system_libraries"]}
    assert libs["X11"] == ("conditional", ["x11"])
    assert libs["check-test-lib"] == ("conditional", ["test"])
    assert "wayland-client" not in libs
    assert "should-not-leak" not in libs

    pkgs = {p["hash"]: (p["usage"], p["used_in"]) for p in data["dependencies"]["packages"]}
    assert pkgs[USED_HASH][0] == "always"
    assert pkgs[UNUSED_HASH] == ("unused", [])


def test_toml_config_inspect_and_gentoo(zig_env: ZigTestEnv, tmp_path: Path) -> None:
    """Multi-pass via TOML --config populates [package], [build], and [output.gentoo]."""
    cfg_file = tmp_path / "zig-recipe.toml"
    cfg_file.write_text(
        """
[package]
description = "Demo fixture app"
homepage = "https://example.org/demo_app"
license = "MIT"

[build]
base_args = ["-Dbackend=headless"]

[build.variants]
wayland = { on = ["-Dbackend=wayland"], off = ["-Dbackend=headless"] }
x11 = ["-Dbackend=x11"]

[output.gentoo]
comments = "compact"
src_uri = "https://example.org/demo_app-${PV}.tar.gz"
keywords = "~amd64 ~arm64"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    res_cfg = zig_env.runner.invoke(
        app,
        [
            "inspect",
            str(zig_env.proj),
            "--format",
            "json",
            "--config",
            str(cfg_file),
            *zig_env.common,
        ],
    )
    assert res_cfg.exit_code == 0, res_cfg.output
    cfg_data = json.loads(res_cfg.stdout)
    assert cfg_data["project"]["summary"] == "Demo fixture app"
    assert cfg_data["project"]["homepage"] == ["https://example.org/demo_app"]
    assert cfg_data["project"]["license"] == "MIT"
    assert [p["name"] for p in cfg_data["passes"]] == ["default", "test", "wayland", "x11"]
    cfg_libs = {
        sl["name"]: (sl["usage"], sl["used_in"]) for sl in cfg_data["build"]["system_libraries"]
    }
    assert cfg_libs["wayland-client"] == ("conditional", ["wayland"])
    assert cfg_libs["X11"] == ("conditional", ["x11"])

    res_gentoo_cfg = zig_env.runner.invoke(
        app, ["gentoo", str(zig_env.proj), "--config", str(cfg_file), "-o", "-", *zig_env.common]
    )
    assert res_gentoo_cfg.exit_code == 0, res_gentoo_cfg.output
    assert 'DESCRIPTION="Demo fixture app"' in res_gentoo_cfg.stdout
    assert 'HOMEPAGE="https://example.org/demo_app"' in res_gentoo_cfg.stdout
    assert 'LICENSE="MIT"' in res_gentoo_cfg.stdout
    assert 'KEYWORDS="~amd64 ~arm64"' in res_gentoo_cfg.stdout
    assert "\thttps://example.org/demo_app-${PV}.tar.gz\n" in res_gentoo_cfg.stdout


def test_render_custom_template(zig_env: ZigTestEnv, tmp_path: Path) -> None:
    """Custom Jinja2 template rendering consumes the generic inspect JSON schema."""
    tpl = tmp_path / "custom.j2"
    tpl.write_text(
        "{{ project.name }}:{{ project.version }} ({{ zig.major_minor }}, {{ passes | length }})\n",
        encoding="utf-8",
    )
    res_render = zig_env.runner.invoke(
        app, ["render", str(tpl), str(zig_env.proj), *zig_env.common]
    )
    assert res_render.exit_code == 0, res_render.output
    assert re.fullmatch(r"demo_app:0\.1\.0 \(\d+\.\d+, 2\)", res_render.stdout.strip())
