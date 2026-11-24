"""Gentoo ZBS_DEPENDENCIES / ZBS_PACKAGES alias planning and formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from zig_recipe.errors import ConfigError
from zig_recipe.model import AggregatedPackage, RecipeModel

VALID_USE_FLAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_@-]*$")
_ARCHIVE_SUFFIX_RE = re.compile(r"\.(?:tar\.(?:gz|xz|zst|bz2)|tgz|zip)$")
_TRAILING_VER_OR_HASH_RE = re.compile(r"[-_](?:v?\d+(?:\.\d+)*|[0-9a-fA-F]{12,}).*$")
_NON_IDENT_RE = re.compile(r"[^A-Za-z0-9_]+")
_MAX_SINGLE_LINE_LEN = 100


class CommentStyle(StrEnum):
    """Verbosity of generated comments in Gentoo ebuilds."""

    FULL = "full"
    COMPACT = "compact"
    NONE = "none"


class UnusedDepsMode(StrEnum):
    """How unused dependencies are rendered in ZBS_DEPENDENCIES / ZBS_PACKAGES."""

    KEEP = "keep"
    COMMENT = "comment"
    DROP = "drop"


def resolve_unused_deps_mode(
    comments: CommentStyle,
    unused_deps: UnusedDepsMode | None,
) -> UnusedDepsMode:
    """Resolve the effective UnusedDepsMode, rejecting `--comments none --unused-deps comment`."""
    if unused_deps is None:
        return UnusedDepsMode.DROP if comments is CommentStyle.NONE else UnusedDepsMode.COMMENT
    if comments is CommentStyle.NONE and unused_deps is UnusedDepsMode.COMMENT:
        raise ConfigError(
            "--comments none cannot be combined with --unused-deps comment; "
            "use --unused-deps drop or --unused-deps keep."
        )
    return unused_deps


def format_use_condition(used_in: tuple[str, ...]) -> str:
    """Format a tuple of pass names as Gentoo USE hints (`X? / wayland?`)."""
    return " / ".join(f"{name}?" for name in used_in)


@dataclass(frozen=True, slots=True)
class ZbsPackageEntry:
    """A single formatted `ZBS_PACKAGES` entry with its allocated Bash alias."""

    alias: str
    package: AggregatedPackage
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ZbsPackagePlan:
    """Grouped `ZBS_PACKAGES` entries and conditional `SRC_URI` flag groups."""

    always_entries: tuple[ZbsPackageEntry, ...]
    conditional_entries: tuple[ZbsPackageEntry, ...]
    unused_entries: tuple[ZbsPackageEntry, ...]
    conditional_src_uri_groups: tuple[tuple[str, tuple[str, ...]], ...]
    has_packages: bool


def _sanitize_alias(raw: str) -> str:
    cleaned = _NON_IDENT_RE.sub("_", raw).strip("_").lower()
    if not cleaned:
        return "pkg"
    if cleaned[0].isdigit():
        return f"pkg_{cleaned}"
    return cleaned


def _derive_base_alias(pkg: AggregatedPackage) -> str:
    if pkg.name:
        return _sanitize_alias(pkg.name)
    url_base = pkg.url.rsplit("/", 1)[-1]
    stem = _ARCHIVE_SUFFIX_RE.sub("", url_base)
    if stem and not stem.startswith("N-V-"):
        stripped = _TRAILING_VER_OR_HASH_RE.sub("", stem)
        if stripped:
            return _sanitize_alias(stripped)
    short_hash = _sanitize_alias(pkg.hash[-8:])
    return f"pkg_{short_hash}"


def _format_zbs_package_lines(
    alias: str,
    pkg: AggregatedPackage,
    *,
    commented: bool = False,
) -> tuple[str, ...]:
    prefix = "\t# " if commented else "\t"
    url_basename = pkg.url.rsplit("/", 1)[-1]
    if url_basename == pkg.filename:
        return (f"{prefix}[{alias}]='{pkg.url}'",)
    single = f"{prefix}[{alias}]='{pkg.url} -> {pkg.filename}'"
    if len(single) <= _MAX_SINGLE_LINE_LEN:
        return (single,)
    return (
        f"{prefix}[{alias}]='",
        f"{prefix}\t{pkg.url}",
        f"{prefix}\t\t-> {pkg.filename}",
        f"{prefix}'",
    )


def build_zbs_package_plan(
    model: RecipeModel,
    *,
    unused_deps: UnusedDepsMode,
) -> ZbsPackagePlan:
    """Build deterministic aliases, formatted lines, and conditional `SRC_URI` groups."""
    used_aliases: set[str] = set()

    def _allocate(pkg: AggregatedPackage) -> str:
        base = _derive_base_alias(pkg)
        if base not in used_aliases:
            used_aliases.add(base)
            return base
        idx = 2
        while f"{base}_{idx}" in used_aliases:
            idx += 1
        alias = f"{base}_{idx}"
        used_aliases.add(alias)
        return alias

    always_entries = tuple(
        ZbsPackageEntry(
            alias=(alias := _allocate(pkg)),
            package=pkg,
            lines=_format_zbs_package_lines(alias, pkg),
        )
        for pkg in model.always_tarballs
    )
    conditional_entries = tuple(
        ZbsPackageEntry(
            alias=(alias := _allocate(pkg)),
            package=pkg,
            lines=_format_zbs_package_lines(alias, pkg),
        )
        for pkg in model.conditional_tarballs
    )
    unused_entries = (
        tuple(
            ZbsPackageEntry(
                alias=(alias := _allocate(pkg)),
                package=pkg,
                lines=_format_zbs_package_lines(
                    alias,
                    pkg,
                    commented=(unused_deps is UnusedDepsMode.COMMENT),
                ),
            )
            for pkg in model.unused_tarballs
        )
        if unused_deps is not UnusedDepsMode.DROP
        else ()
    )

    by_flag: dict[str, list[str]] = {}
    for entry in conditional_entries:
        for flag in entry.package.used_in:
            if VALID_USE_FLAG_RE.match(flag):
                by_flag.setdefault(flag, []).append(entry.alias)
    conditional_src_uri_groups = tuple(
        (flag, tuple(aliases)) for flag, aliases in sorted(by_flag.items())
    )

    has_packages = bool(always_entries or conditional_entries or unused_entries)
    return ZbsPackagePlan(
        always_entries=always_entries,
        conditional_entries=conditional_entries,
        unused_entries=unused_entries,
        conditional_src_uri_groups=conditional_src_uri_groups,
        has_packages=has_packages,
    )


def render_gentoo_deps_only(
    model: RecipeModel,
    *,
    comments: CommentStyle = CommentStyle.FULL,
    unused_deps: UnusedDepsMode | None = None,
    use_zbs_packages: bool = False,
) -> str:
    """Render only the `ZBS_DEPENDENCIES` (or `ZBS_PACKAGES`) block for fast ebuild bumps."""
    effective_unused = resolve_unused_deps_mode(comments, unused_deps)
    if use_zbs_packages:
        return _render_zbs_packages_only(model, comments=comments, unused_deps=effective_unused)
    return _render_zbs_dependencies_only(model, comments=comments, unused_deps=effective_unused)


def _render_zbs_packages_only(
    model: RecipeModel,
    *,
    comments: CommentStyle,
    unused_deps: UnusedDepsMode,
) -> str:
    pkg_plan = build_zbs_package_plan(model, unused_deps=unused_deps)
    git_commits = model.git_commits
    if not (pkg_plan.has_packages or git_commits):
        return "# No remote Zig dependencies.\n" if comments is not CommentStyle.NONE else ""

    lines: list[str] = []
    if pkg_plan.has_packages:
        lines.append("declare -g -r -A ZBS_PACKAGES=(")
        for entry in pkg_plan.always_entries:
            lines.extend(entry.lines)
        if pkg_plan.conditional_entries:
            if pkg_plan.always_entries and comments is not CommentStyle.NONE:
                lines.append("")
            if comments is not CommentStyle.NONE:
                lines.append("\t# Conditional dependencies:")
            for entry in pkg_plan.conditional_entries:
                lines.extend(entry.lines)
        if pkg_plan.unused_entries:
            if (pkg_plan.always_entries or pkg_plan.conditional_entries) and (
                comments is not CommentStyle.NONE
            ):
                lines.append("")
            if comments is not CommentStyle.NONE:
                lines.append("\t# Not used in any analyzed pass:")
            for entry in pkg_plan.unused_entries:
                lines.extend(entry.lines)
        lines.append(")")

    if git_commits:
        if lines:
            lines.append("")
        if model.tarball_tarball and comments is not CommentStyle.NONE:
            lines.append("# Some Git dependencies could not be converted to tarball URLs.")
            lines.append("# Host this archive somewhere and add its URL to SRC_URI:")
            lines.append(f"# {model.tarball_tarball}")
        lines.append("declare -g -r -a ZBS_BUNDLED_PACKAGES=(")
        for dep in git_commits:
            lines.append(f"\t{dep.filename}")
        lines.append(")")

    return "\n".join(lines) + "\n"


def _render_zbs_dependencies_only(
    model: RecipeModel,
    *,
    comments: CommentStyle,
    unused_deps: UnusedDepsMode,
) -> str:
    always = model.always_tarballs
    conditional = model.conditional_tarballs
    unused = model.unused_tarballs if unused_deps is not UnusedDepsMode.DROP else ()
    git_commits = model.git_commits

    if not (always or conditional or unused or git_commits):
        return "# No remote Zig dependencies.\n" if comments is not CommentStyle.NONE else ""

    lines: list[str] = ["declare -g -r -A ZBS_DEPENDENCIES=("]
    for dep in always:
        lines.append(f"\t[{dep.filename}]='{dep.url}'")

    if conditional:
        if always and comments is not CommentStyle.NONE:
            lines.append("")
        if comments is not CommentStyle.NONE:
            lines.append("\t# Conditional dependencies:")
        for dep in conditional:
            if comments is not CommentStyle.NONE:
                lines.append(
                    f"\t[{dep.filename}]='{dep.url}' # {format_use_condition(dep.used_in)}"
                )
            else:
                lines.append(f"\t[{dep.filename}]='{dep.url}'")

    if unused:
        if (always or conditional) and comments is not CommentStyle.NONE:
            lines.append("")
        if comments is not CommentStyle.NONE:
            lines.append("\t# Not used in any analyzed pass:")
        prefix = "\t# " if unused_deps is UnusedDepsMode.COMMENT else "\t"
        for dep in unused:
            lines.append(f"{prefix}[{dep.filename}]='{dep.url}'")

    if model.tarball_tarball and git_commits:
        if (always or conditional or unused) and comments is not CommentStyle.NONE:
            lines.append("")
        if comments is not CommentStyle.NONE:
            lines.append("\t# Some Git dependencies could not be converted to tarball URLs.")
            lines.append("\t# Host this archive somewhere and add its URL to SRC_URI:")
            lines.append(f"\t# {model.tarball_tarball}")
        for dep in git_commits:
            lines.append(f"\t[{dep.filename}]=''")

    lines.append(")")
    return "\n".join(lines) + "\n"
