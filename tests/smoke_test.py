"""Verify that built wheels and sdists include templates and dump_config.zig."""

from __future__ import annotations

from importlib import resources

import zig_recipe


def main() -> None:
    assert "." in zig_recipe.__version__

    dump_src = resources.files("zig_recipe.zig").joinpath("dump_config.zig")
    assert dump_src.is_file(), "dump_config.zig missing from package"

    templates = resources.files("zig_recipe.templates")
    for style in ("full", "compact", "none"):
        tpl = templates.joinpath(f"gentoo_{style}.ebuild.j2")
        assert tpl.is_file(), f"gentoo_{style}.ebuild.j2 missing from package"

    print(f"Smoke test passed for zig-recipe {zig_recipe.__version__}")


if __name__ == "__main__":
    main()
