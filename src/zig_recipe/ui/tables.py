"""Rich table rendering for `zig-recipe inspect`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from zig_recipe.model import RecipeModel, UsageKind

if TYPE_CHECKING:
    from rich.console import Console

_OPTION_TYPE_LABELS: dict[str, str] = {
    "enum_list": "list of enums",
    "list": "list of strings",
    "build_id": "build ID",
    "lazy_path": "LazyPath",
    "lazy_path_list": "list of LazyPath",
}


def _format_option_type(type_id: str, values: tuple[str, ...] | None) -> str:
    label = _OPTION_TYPE_LABELS.get(type_id, type_id)
    if values:
        return f"{label}: {' | '.join(values)}"
    return label


def _format_usage_cell(usage: UsageKind, used_in: tuple[str, ...]) -> str:
    if usage is UsageKind.CONDITIONAL:
        return f"conditional ({', '.join(used_in)})"
    return usage.value


def print_inspect_tables(console: Console, model: RecipeModel) -> None:
    """Print human-readable Rich summary tables for an analyzed project."""
    console.print(
        f"[bold]{model.project_name}[/bold] {model.project_version} (Zig {model.zig_version.raw})"
    )
    if model.summary:
        console.print(f"Summary: {model.summary}")
    if model.homepage:
        console.print(f"Homepage: {' '.join(model.homepage)}")
    if model.license:
        console.print(f"License: {model.license}")
    pass_names = ", ".join(p.name for p in model.passes)
    console.print(f"Passes: {pass_names}")
    console.print(f"C/C++ sources detected: {'yes' if model.has_c_sources else 'no'}")

    if model.packages:
        dep_table = Table(title="Dependencies")
        dep_table.add_column("Name / Hash")
        dep_table.add_column("Kind")
        dep_table.add_column("Usage")
        dep_table.add_column("URL")
        for pkg in model.packages:
            dep_table.add_row(
                pkg.name or pkg.hash[:16],
                pkg.kind,
                _format_usage_cell(pkg.usage, pkg.used_in),
                pkg.url,
            )
        console.print(dep_table)

    if model.user_options:
        opt_table = Table(title="Build Options")
        opt_table.add_column("Option")
        opt_table.add_column("Type")
        opt_table.add_column("Description")
        for opt in model.user_options:
            opt_table.add_row(
                f"-D{opt.name}",
                _format_option_type(opt.type_id, opt.values),
                opt.description,
            )
        console.print(opt_table)

    if model.system_libraries:
        lib_table = Table(title="System Libraries")
        lib_table.add_column("Library")
        lib_table.add_column("Usage")
        lib_table.add_column("pkg-config")
        lib_table.add_column("Used By")
        for lib in model.system_libraries:
            users = ", ".join(f"{u.kind.replace('_', '-')} {u.name}" for u in lib.used_by)
            lib_table.add_row(
                lib.name,
                _format_usage_cell(lib.usage, lib.used_in),
                lib.use_pkg_config,
                users,
            )
        console.print(lib_table)

    if model.system_integrations:
        si_table = Table(title="System Integrations")
        si_table.add_column("Name")
        si_table.add_column("Enabled with --system")
        for si in model.system_integrations:
            si_table.add_row(si.name, "yes" if si.enabled else "no")
        console.print(si_table)

    if model.top_level_steps:
        step_table = Table(title="Top-Level Steps")
        step_table.add_column("Step")
        step_table.add_column("Default")
        step_table.add_column("Description")
        for step in model.top_level_steps:
            step_table.add_row(step.name, "*" if step.is_default else "", step.description)
        console.print(step_table)
