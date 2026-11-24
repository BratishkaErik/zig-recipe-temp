"""Gentoo EAPI 8 ebuild renderer (`IUSE`, `src_configure()`, and Jinja2 templates)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from textwrap import wrap
from typing import Any

from zig_recipe import __version__
from zig_recipe.gentoo.packages import (
    VALID_USE_FLAG_RE,
    CommentStyle,
    UnusedDepsMode,
    build_zbs_package_plan,
    format_use_condition,
    render_gentoo_deps_only,
    resolve_unused_deps_mode,
)
from zig_recipe.jinja import make_jinja_env
from zig_recipe.model import RecipeModel
from zig_recipe.zig.inspector import LibraryUser, UserOption, ZigVersion

__all__ = [
    "CommentStyle",
    "UnusedDepsMode",
    "build_gentoo_context",
    "default_gentoo_filename",
    "gentoo_zig_slot",
    "render_gentoo_deps_only",
    "render_gentoo_ebuild",
    "resolve_unused_deps_mode",
]

_GENTOO_OPTION_LABELS: dict[str, str] = {
    "enum_list": "list of enums",
    "list": "list of strings",
    "build_id": "build ID",
    "lazy_path": "LazyPath",
    "lazy_path_list": "list of LazyPath",
}


def _format_option_type(opt: UserOption) -> str:
    label = _GENTOO_OPTION_LABELS.get(opt.type_id, opt.type_id)
    if opt.values:
        return f"{label}: {' | '.join(opt.values)}"
    return label


def _format_library_users(users: tuple[LibraryUser, ...]) -> str:
    return ", ".join(f"({u.kind.replace('_', '-')}) {u.name}" for u in users)


def gentoo_zig_slot(version: ZigVersion) -> str:
    """Map a ZigVersion to Gentoo's ZIG_SLOT value (`9999` for prereleases, else `major.minor`)."""
    return "9999" if version.is_live else version.major_minor


def default_gentoo_filename(model: RecipeModel) -> str:
    """Generate default Gentoo ebuild filename `<name>-<version>.ebuild`."""
    safe_name = model.project_name.replace("_", "-")
    return f"{safe_name}-{model.project_version}.ebuild"


def _parse_d_option(arg: str) -> tuple[str, str | None] | None:
    """Parse `-Dname=val` or `-Dname` into `(name, val)` (None if not a `-D` flag)."""
    if not arg.startswith("-D") or len(arg) <= 2:
        return None
    body = arg[2:]
    if "=" in body:
        name, val = body.split("=", 1)
        return (name, val)
    return (body, None)


@dataclass(frozen=True, slots=True)
class _GentooConfigurePlan:
    iuse: tuple[str, ...]
    restrict_test: bool
    base_zbs_args: tuple[str, ...]
    variant_zbs_args: tuple[str, ...]
    variant_if_blocks: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    remaining_options: tuple[UserOption, ...]


def _build_gentoo_configure(model: RecipeModel) -> _GentooConfigurePlan:
    integration_names = {si.name for si in model.system_integrations}
    configured_options: set[str] = set()

    variant_d_keys: set[str] = set()
    for variant in model.pass_config.variants:
        for arg in (*variant.on_args, *variant.off_args):
            parsed = _parse_d_option(arg)
            if parsed is not None:
                variant_d_keys.add(parsed[0])

    base_zbs_args: list[str] = []
    overridden_base_d: dict[str, str] = {}
    for arg in model.pass_config.base_args:
        if arg.startswith("-fsys=") and arg[6:] in integration_names:
            continue
        parsed = _parse_d_option(arg)
        if parsed is not None:
            if parsed[0] in variant_d_keys:
                if parsed[1] is not None:
                    overridden_base_d[parsed[0]] = parsed[1]
                continue
            configured_options.add(parsed[0])
        base_zbs_args.append(arg)

    iuse_list: list[str] = []
    variant_zbs_args: list[str] = []
    variant_if_blocks: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    for variant in model.pass_config.variants:
        if not VALID_USE_FLAG_RE.match(variant.name):
            continue
        iuse_list.append(variant.name)
        for arg in (*variant.on_args, *variant.off_args):
            parsed = _parse_d_option(arg)
            if parsed is not None:
                configured_options.add(parsed[0])

        if len(variant.on_args) == 1 and len(variant.off_args) == 1:
            on_opt = _parse_d_option(variant.on_args[0])
            off_opt = _parse_d_option(variant.off_args[0])
            if (
                on_opt is not None
                and off_opt is not None
                and on_opt[0] == off_opt[0]
                and on_opt[1] is not None
                and off_opt[1] is not None
            ):
                variant_zbs_args.append(
                    f"-D{on_opt[0]}=$(usex {variant.name} {on_opt[1]} {off_opt[1]})"
                )
                continue
        if len(variant.on_args) == 1 and not variant.off_args:
            on_opt = _parse_d_option(variant.on_args[0])
            if on_opt is not None and on_opt[1] is not None and on_opt[0] in overridden_base_d:
                off_val = overridden_base_d[on_opt[0]]
                variant_zbs_args.append(
                    f"-D{on_opt[0]}=$(usex {variant.name} {on_opt[1]} {off_val})"
                )
                continue
            variant_zbs_args.append(f"$(usev {variant.name} {variant.on_args[0]})")
            continue

        variant_if_blocks.append((variant.name, variant.on_args, variant.off_args))

    has_test_only_items = (
        any("test" in p.used_in for p in model.conditional_tarballs)
        or any("test" in lib.used_in for lib in model.conditional_libraries)
        or any("test" in prog.used_in for prog in model.conditional_programs)
    )
    if has_test_only_items and "test" not in iuse_list:
        iuse_list.append("test")

    iuse = tuple(sorted(set(iuse_list)))
    restrict_test = "test" in iuse

    remaining_options = tuple(
        opt for opt in model.user_options if opt.name not in configured_options
    )
    return _GentooConfigurePlan(
        iuse=iuse,
        restrict_test=restrict_test,
        base_zbs_args=tuple(base_zbs_args),
        variant_zbs_args=tuple(variant_zbs_args),
        variant_if_blocks=tuple(variant_if_blocks),
        remaining_options=remaining_options,
    )


def build_gentoo_context(
    model: RecipeModel,
    *,
    comments: CommentStyle = CommentStyle.FULL,
    unused_deps: UnusedDepsMode | None = None,
    use_zbs_packages: bool = False,
    year: int | None = None,
) -> dict[str, Any]:
    """Build template context for Gentoo EAPI 8 ebuild rendering."""
    effective_unused = resolve_unused_deps_mode(comments, unused_deps)
    current_year = year if year is not None else datetime.now(UTC).year
    explicitly_disabled_sys = {
        arg[len("-fno-sys=") :]
        for arg in model.pass_config.base_args
        if arg.startswith("-fno-sys=")
    }
    enabled_integrations = tuple(
        si
        for si in model.system_integrations
        if si.enabled and si.name not in explicitly_disabled_sys
    )
    disabled_integrations = tuple(
        si
        for si in model.system_integrations
        if not si.enabled and si.name not in explicitly_disabled_sys
    )
    cfg_plan = _build_gentoo_configure(model)
    pkg_plan = build_zbs_package_plan(model, unused_deps=effective_unused)

    always_tarballs = model.always_tarballs
    conditional_tarballs = model.conditional_tarballs
    unused_tarballs = model.unused_tarballs
    used_tarballs = model.used_tarballs
    git_commits = model.git_commits

    has_active_tarballs = bool(
        used_tarballs or (unused_tarballs and effective_unused is UnusedDepsMode.KEEP)
    )
    has_zbs_entries = bool(
        used_tarballs
        or (unused_tarballs and effective_unused is not UnusedDepsMode.DROP)
        or git_commits
    )
    has_configure_body = bool(
        cfg_plan.base_zbs_args
        or cfg_plan.variant_zbs_args
        or cfg_plan.variant_if_blocks
        or cfg_plan.remaining_options
        or enabled_integrations
        or disabled_integrations
    )
    has_none_configure_body = bool(
        cfg_plan.base_zbs_args
        or cfg_plan.variant_zbs_args
        or cfg_plan.variant_if_blocks
        or enabled_integrations
        or disabled_integrations
    )

    return {
        "generator_version": __version__,
        "year": current_year,
        "comments": comments.value,
        "unused_deps": effective_unused.value,
        "use_zbs_packages": use_zbs_packages,
        "model": model,
        "project_name": model.project_name,
        "project_version": model.project_version,
        "summary": model.summary,
        "homepage": " ".join(model.homepage) if model.homepage else "TODO",
        "license": model.gentoo_config.license or model.license or "TODO",
        "keywords": model.gentoo_config.keywords or "~amd64",
        "src_uri_entries": model.gentoo_config.src_uri,
        "zig_slot": gentoo_zig_slot(model.zig_version),
        "needs_llvm": model.has_c_sources,
        "iuse": cfg_plan.iuse,
        "restrict_test": cfg_plan.restrict_test,
        "base_zbs_args": cfg_plan.base_zbs_args,
        "variant_zbs_args": cfg_plan.variant_zbs_args,
        "variant_if_blocks": cfg_plan.variant_if_blocks,
        "remaining_options": cfg_plan.remaining_options,
        "has_configure_body": has_configure_body,
        "has_none_configure_body": has_none_configure_body,
        "used_tarballs": used_tarballs,
        "always_tarballs": always_tarballs,
        "conditional_tarballs": conditional_tarballs,
        "unused_tarballs": unused_tarballs,
        "always_pkg_entries": pkg_plan.always_entries,
        "conditional_pkg_entries": pkg_plan.conditional_entries,
        "unused_pkg_entries": pkg_plan.unused_entries,
        "conditional_src_uri_groups": pkg_plan.conditional_src_uri_groups,
        "has_zbs_packages": pkg_plan.has_packages,
        "has_active_tarballs": has_active_tarballs,
        "has_zbs_entries": has_zbs_entries,
        "git_commits": git_commits,
        "tarball_tarball": str(model.tarball_tarball) if model.tarball_tarball else None,
        "system_libraries": model.system_libraries,
        "always_libraries": model.always_libraries,
        "conditional_libraries": model.conditional_libraries,
        "find_programs": model.find_programs,
        "always_programs": model.always_programs,
        "conditional_programs": model.conditional_programs,
        "user_options": model.user_options,
        "system_integrations": model.system_integrations,
        "enabled_integrations": enabled_integrations,
        "disabled_integrations": disabled_integrations,
        "top_level_steps": model.top_level_steps,
    }


def render_gentoo_ebuild(
    model: RecipeModel,
    *,
    comments: CommentStyle = CommentStyle.FULL,
    unused_deps: UnusedDepsMode | None = None,
    use_zbs_packages: bool = False,
    template_path: Path | None = None,
    year: int | None = None,
) -> str:
    """Render a Gentoo EAPI 8 ebuild using a built-in or custom Jinja2 template."""
    effective_unused = resolve_unused_deps_mode(comments, unused_deps)
    if template_path is not None:
        template_text = template_path.read_text(encoding="utf-8")
    else:
        template_name = f"gentoo_{comments.value}.ebuild.j2"
        template_text = (
            resources.files("zig_recipe.gentoo.templates")
            .joinpath(template_name)
            .read_text(encoding="utf-8")
        )

    env = make_jinja_env()
    env.filters["wrap_lines"] = lambda text, width=70: wrap(str(text), width=width)
    env.filters["option_type"] = _format_option_type
    env.filters["library_users"] = _format_library_users
    env.filters["use_condition"] = format_use_condition
    template = env.from_string(template_text)
    context = build_gentoo_context(
        model,
        comments=comments,
        unused_deps=effective_unused,
        use_zbs_packages=use_zbs_packages,
        year=year,
    )
    return template.render(**context)
