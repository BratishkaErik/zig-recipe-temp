"""Real Zig 0.17+ integration tests using fixture projects."""

from __future__ import annotations

import json
import os
import shutil
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


def _stage_project_and_cache(tmp_path: Path) -> tuple[Path, Path]:
    proj = tmp_path / "demo_app"
    shutil.copytree(FIXTURES_DIR / "demo_app", proj)

    cache_dir = tmp_path / "cache"
    p_dir = cache_dir / "deps" / "p"
    shutil.copytree(FIXTURES_DIR / "cached_deps", p_dir)
    (p_dir / UNUSED_HASH).mkdir(parents=True, exist_ok=True)
    return proj, cache_dir


@pytest.mark.requires_zig
@pytest.mark.skipif(ZIG_EXE is None, reason="No Zig executable found (set ZIG_EXE or PATH)")
def test_gentoo_inspect_and_render_with_real_zig(tmp_path: Path) -> None:
    assert ZIG_EXE is not None
    proj, cache_dir = _stage_project_and_cache(tmp_path)
    runner = CliRunner()
    common = ["--zig", ZIG_EXE, "--cache-dir", str(cache_dir), "--no-fetch"]

    # 1. Default ebuild generation: checks structural contract, used vs unused deps, no step leak.
    res = runner.invoke(app, ["gentoo", str(proj), "-o", "-", *common])
    assert res.exit_code == 0, res.output
    assert "EAPI=8" in res.stdout
    assert "ZIG_NEEDS_LLVM=1" not in res.stdout
    assert "should-not-leak" not in res.stdout
    assert "wayland-client" in res.stdout
    assert "-fsys=zlib" in res.stdout
    assert f"[{USED_HASH}.tar.gz]=" in res.stdout
    assert f"[{UNUSED_HASH}.tar.gz]=" in res.stdout
    assert res.stdout.index(USED_HASH) < res.stdout.index(UNUSED_HASH)

    # 2. --deps-only prints only ZBS_DEPENDENCIES array.
    res_deps = runner.invoke(app, ["gentoo", str(proj), "--deps-only", *common])
    assert res_deps.exit_code == 0, res_deps.output
    assert res_deps.stdout.startswith("declare -g -r -A ZBS_DEPENDENCIES=(\n")
    assert "EAPI=8" not in res_deps.stdout

    # 3. Forwarding `zig build` options after `--` switches backend and enables C sources.
    res_json = runner.invoke(
        app,
        ["inspect", str(proj), "--format", "json", *common, "--", "-Dbackend=x11", "-Duse-c=true"],
    )
    assert res_json.exit_code == 0, res_json.output
    data = json.loads(res_json.stdout)
    assert data["project"]["name"] == "demo_app"
    assert data["build"]["has_c_sources"] is True

    libs = {sl["name"] for sl in data["build"]["system_libraries"]}
    assert "X11" in libs
    assert "wayland-client" not in libs
    assert "should-not-leak" not in libs

    pkgs = {p["hash"]: p["used"] for p in data["dependencies"]["packages"]}
    assert pkgs[USED_HASH] is True
    assert pkgs[UNUSED_HASH] is False

    # 4. Custom template rendering (uses generic inspect JSON schema).
    tpl = tmp_path / "custom.j2"
    tpl.write_text(
        "{{ project.name }}:{{ project.version }} ({{ zig.major_minor }})\n", encoding="utf-8"
    )
    res_render = runner.invoke(app, ["render", str(tpl), str(proj), *common])
    assert res_render.exit_code == 0, res_render.output
    assert res_render.stdout.strip() == "demo_app:0.1.0 (0.17)"
