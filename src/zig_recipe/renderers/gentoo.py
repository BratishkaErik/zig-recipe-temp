"""Gentoo EAPI 8 ebuild renderer."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from pathlib import Path
from textwrap import wrap
from typing import Any

from zig_recipe import __version__
from zig_recipe.model import RecipeModel
from zig_recipe.renderers.jinja import make_jinja_env
from zig_recipe.zig.inspector import LibraryUser, UserOption, ZigVersion

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


class CommentStyle(StrEnum):
    """Verbosity of generated comments in Gentoo ebuilds."""

    FULL = "full"
    COMPACT = "compact"
    NONE = "none"


def gentoo_zig_slot(version: ZigVersion) -> str:
    """Map a ZigVersion to Gentoo's ZIG_SLOT value (`9999` for prereleases, else `major.minor`)."""
    return "9999" if version.is_live else version.major_minor


def default_gentoo_filename(model: RecipeModel) -> str:
    """Generate default Gentoo ebuild filename `<name>-<version>.ebuild`."""
    safe_name = model.project_name.replace("_", "-")
    return f"{safe_name}-{model.project_version}.ebuild"


def build_gentoo_context(
    model: RecipeModel,
    *,
    comments: CommentStyle = CommentStyle.FULL,
    year: int | None = None,
) -> dict[str, Any]:
    """Build template context for Gentoo EAPI 8 ebuild rendering."""
    current_year = year if year is not None else datetime.now(UTC).year
    enabled_integrations = tuple(si for si in model.system_integrations if si.enabled)
    disabled_integrations = tuple(si for si in model.system_integrations if not si.enabled)

    return {
        "generator_version": __version__,
        "year": current_year,
        "comments": comments.value,
        "model": model,
        "project_name": model.project_name,
        "project_version": model.project_version,
        "summary": model.summary,
        "zig_slot": gentoo_zig_slot(model.zig_version),
        "needs_llvm": model.has_c_sources,
        "used_tarballs": model.used_tarballs,
        "unused_tarballs": model.unused_tarballs,
        "git_commits": model.git_commits,
        "tarball_tarball": str(model.tarball_tarball) if model.tarball_tarball else None,
        "system_libraries": model.system_libraries,
        "find_programs": model.find_programs,
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
    template_path: Path | None = None,
    year: int | None = None,
) -> str:
    """Render a Gentoo EAPI 8 ebuild using a built-in or custom Jinja2 template."""
    if template_path is not None:
        template_text = template_path.read_text(encoding="utf-8")
    else:
        template_name = f"gentoo_{comments.value}.ebuild.j2"
        template_text = (
            resources.files("zig_recipe.templates")
            .joinpath(template_name)
            .read_text(encoding="utf-8")
        )

    env = make_jinja_env()
    env.filters["wrap_lines"] = lambda text, width=70: wrap(str(text), width=width)
    env.filters["option_type"] = _format_option_type
    env.filters["library_users"] = _format_library_users
    template = env.from_string(template_text)
    context = build_gentoo_context(model, comments=comments, year=year)
    return template.render(**context)


def render_gentoo_deps_only(
    model: RecipeModel,
    *,
    comments: CommentStyle = CommentStyle.FULL,
) -> str:
    """Render only the `ZBS_DEPENDENCIES=(...)` block for fast ebuild bumps."""
    used = model.used_tarballs
    unused = model.unused_tarballs
    git_commits = model.git_commits
    if not (used or unused or git_commits):
        return "# No remote Zig dependencies.\n" if comments is not CommentStyle.NONE else ""

    lines: list[str] = ["declare -g -r -A ZBS_DEPENDENCIES=("]
    for dep in used:
        lines.append(f"\t[{dep.filename}]='{dep.url}'")

    if unused:
        if used and comments is not CommentStyle.NONE:
            lines.append("")
        if comments is not CommentStyle.NONE:
            lines.append("\t# Not used with the current build arguments:")
        for dep in unused:
            lines.append(f"\t[{dep.filename}]='{dep.url}'")

    if model.tarball_tarball and git_commits:
        if (used or unused) and comments is not CommentStyle.NONE:
            lines.append("")
        if comments is not CommentStyle.NONE:
            lines.append("\t# Some Git dependencies could not be converted to tarball URLs.")
            lines.append("\t# Host this archive somewhere and add its URL to SRC_URI:")
            lines.append(f"\t# {model.tarball_tarball}")
        for dep in git_commits:
            lines.append(f"\t[{dep.filename}]=''")

    lines.append(")")
    return "\n".join(lines) + "\n"
