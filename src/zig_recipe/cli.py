"""Command-line interface for zig-recipe."""

from __future__ import annotations

import json
import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from zig_recipe import __version__
from zig_recipe.errors import ZigRecipeError
from zig_recipe.model import RecipeModel
from zig_recipe.pipeline import analyze_project
from zig_recipe.renderers.gentoo import (
    CommentStyle,
    default_gentoo_filename,
    render_gentoo_deps_only,
    render_gentoo_ebuild,
)
from zig_recipe.renderers.jinja import render_template

app = typer.Typer(
    name="zig-recipe",
    help="Generate package manager recipes from Zig projects.",
    no_args_is_help=True,
    add_completion=False,
)

stderr_console = Console(stderr=True)
stdout_console = Console()


class LogLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


class InspectFormat(StrEnum):
    TABLE = "table"
    JSON = "json"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"zig-recipe {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show zig-recipe version and exit.",
        ),
    ] = False,
    log_level: Annotated[
        LogLevel,
        typer.Option(
            "--log-level",
            "-l",
            case_sensitive=False,
            help="Logging verbosity.",
        ),
    ] = LogLevel.INFO,
) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.value.upper()),
        format="%(message)s",
        handlers=[RichHandler(console=stderr_console, show_path=False, show_time=False)],
        force=True,
    )


def _extract_zig_build_args(ctx: typer.Context) -> tuple[str, ...]:
    args = list(ctx.args)
    if args and args[0] == "--":
        args = args[1:]
    return tuple(args)


def _write_output(content: str, output: str | None, default_name: str) -> None:
    if output == "-":
        sys.stdout.write(content)
        return
    target = Path(output) if output is not None else Path(default_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    stderr_console.print(f"[green]Wrote[/green] {target}")


def _format_option_type(type_id: str, values: tuple[str, ...] | None) -> str:
    labels = {
        "enum_list": "list of enums",
        "list": "list of strings",
        "build_id": "build ID",
        "lazy_path": "LazyPath",
        "lazy_path_list": "list of LazyPath",
    }
    label = labels.get(type_id, type_id)
    if values:
        return f"{label}: {' | '.join(values)}"
    return label


def _print_inspect_table(model: RecipeModel) -> None:
    stdout_console.print(
        f"[bold]{model.project_name}[/bold] {model.project_version} (Zig {model.zig_version.raw})"
    )
    if model.summary:
        stdout_console.print(f"Summary: {model.summary}")
    stdout_console.print(f"C/C++ sources detected: {'yes' if model.has_c_sources else 'no'}")

    if model.dependencies.packages:
        dep_table = Table(title="Dependencies")
        dep_table.add_column("Name / Hash")
        dep_table.add_column("Kind")
        dep_table.add_column("Used")
        dep_table.add_column("URL")
        used = model.inspection.used_package_hashes
        for pkg in model.dependencies.packages:
            dep_table.add_row(
                pkg.name or pkg.hash[:16],
                pkg.kind,
                "yes" if pkg.hash in used else "no",
                pkg.url,
            )
        stdout_console.print(dep_table)

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
        stdout_console.print(opt_table)

    if model.system_libraries:
        lib_table = Table(title="System Libraries")
        lib_table.add_column("Library")
        lib_table.add_column("pkg-config")
        lib_table.add_column("Used By")
        for lib in model.system_libraries:
            users = ", ".join(f"{u.kind.replace('_', '-')} {u.name}" for u in lib.used_by)
            lib_table.add_row(lib.name, lib.use_pkg_config, users)
        stdout_console.print(lib_table)

    if model.system_integrations:
        si_table = Table(title="System Integrations")
        si_table.add_column("Name")
        si_table.add_column("Enabled with --system")
        for si in model.system_integrations:
            si_table.add_row(si.name, "yes" if si.enabled else "no")
        stdout_console.print(si_table)

    if model.top_level_steps:
        step_table = Table(title="Top-Level Steps")
        step_table.add_column("Step")
        step_table.add_column("Default")
        step_table.add_column("Description")
        for step in model.top_level_steps:
            step_table.add_row(step.name, "*" if step.is_default else "", step.description)
        stdout_console.print(step_table)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def gentoo(
    ctx: typer.Context,
    project: Annotated[
        Path | None,
        typer.Argument(
            help="Project directory or build.zig (defaults to current directory).",
            show_default=False,
        ),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Output path ('-' for stdout; defaults to <name>-<version>.ebuild, "
                "or stdout with --deps-only)."
            ),
        ),
    ] = None,
    zig: Annotated[
        str,
        typer.Option("--zig", help="Zig executable to use."),
    ] = "zig",
    comments: Annotated[
        CommentStyle,
        typer.Option(
            "--comments",
            case_sensitive=False,
            help="Comment detail level: full, compact, or none.",
        ),
    ] = CommentStyle.FULL,
    deps_only: Annotated[
        bool,
        typer.Option(
            "--deps-only",
            help="Print only the ZBS_DEPENDENCIES array (handy when bumping an ebuild).",
        ),
    ] = False,
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch/--no-fetch",
            help="Download missing dependencies into cache (use --no-fetch to run offline).",
        ),
    ] = True,
    template: Annotated[
        Path | None,
        typer.Option(
            "--template",
            help="Custom Jinja2 template overriding the built-in one.",
            rich_help_panel="Advanced",
        ),
    ] = None,
    tarball_dir: Annotated[
        Path | None,
        typer.Option(
            "--tarball-dir",
            help=(
                "Where to save the bundled git-dependencies archive, if any git URLs "
                "cannot be converted to tarballs."
            ),
            rich_help_panel="Advanced",
        ),
    ] = None,
    cache_dir: Annotated[
        Path | None,
        typer.Option(
            "--cache-dir",
            envvar="ZIG_RECIPE_CACHE_DIR",
            help="Override zig-recipe cache directory.",
            rich_help_panel="Advanced",
        ),
    ] = None,
) -> None:
    """Generate a Gentoo ebuild.

    Any arguments after '--' are forwarded to 'zig build' so you can inspect
    non-default build options (e.g. '-- -Dbackend=wayland').
    """
    zig_build_args = _extract_zig_build_args(ctx)
    try:
        model = analyze_project(
            project_path=project or Path("."),
            zig_exe=zig,
            cache_dir=cache_dir,
            fetch=fetch,
            zig_build_args=zig_build_args,
            tarball_dir=tarball_dir,
        )
        if deps_only:
            rendered = render_gentoo_deps_only(model, comments=comments)
        else:
            rendered = render_gentoo_ebuild(model, comments=comments, template_path=template)
    except ZigRecipeError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    effective_output = output if output is not None else ("-" if deps_only else None)
    _write_output(rendered, effective_output, default_gentoo_filename(model))


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def inspect(
    ctx: typer.Context,
    project: Annotated[
        Path | None,
        typer.Argument(
            help="Project directory or build.zig (defaults to current directory).",
            show_default=False,
        ),
    ] = None,
    fmt: Annotated[
        InspectFormat,
        typer.Option("--format", "-f", case_sensitive=False, help="Output format."),
    ] = InspectFormat.TABLE,
    zig: Annotated[
        str,
        typer.Option("--zig", help="Zig executable to use."),
    ] = "zig",
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch/--no-fetch",
            help="Download missing dependencies into cache (use --no-fetch to run offline).",
        ),
    ] = True,
    cache_dir: Annotated[
        Path | None,
        typer.Option(
            "--cache-dir",
            envvar="ZIG_RECIPE_CACHE_DIR",
            help="Override zig-recipe cache directory.",
            rich_help_panel="Advanced",
        ),
    ] = None,
) -> None:
    """Print build metadata (packages, options, system libraries) as a table or JSON.

    Any arguments after '--' are forwarded to 'zig build' so you can inspect
    non-default build options (e.g. '-- -Dbackend=wayland').
    """
    zig_build_args = _extract_zig_build_args(ctx)
    try:
        model = analyze_project(
            project_path=project or Path("."),
            zig_exe=zig,
            cache_dir=cache_dir,
            fetch=fetch,
            zig_build_args=zig_build_args,
        )
    except ZigRecipeError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if fmt is InspectFormat.JSON:
        sys.stdout.write(json.dumps(model.to_dict(), indent=2) + "\n")
    else:
        _print_inspect_table(model)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def render(
    ctx: typer.Context,
    template: Annotated[
        Path,
        typer.Argument(help="Path to a Jinja2 template file."),
    ],
    project: Annotated[
        Path | None,
        typer.Argument(
            help="Project directory or build.zig (defaults to current directory).",
            show_default=False,
        ),
    ] = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output file path ('-' for stdout)."),
    ] = "-",
    zig: Annotated[
        str,
        typer.Option("--zig", help="Zig executable to use."),
    ] = "zig",
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch/--no-fetch",
            help="Download missing dependencies into cache (use --no-fetch to run offline).",
        ),
    ] = True,
    tarball_dir: Annotated[
        Path | None,
        typer.Option(
            "--tarball-dir",
            help=(
                "Where to save the bundled git-dependencies archive, if any git URLs "
                "cannot be converted to tarballs."
            ),
            rich_help_panel="Advanced",
        ),
    ] = None,
    cache_dir: Annotated[
        Path | None,
        typer.Option(
            "--cache-dir",
            envvar="ZIG_RECIPE_CACHE_DIR",
            help="Override zig-recipe cache directory.",
            rich_help_panel="Advanced",
        ),
    ] = None,
) -> None:
    """Generate a recipe using a custom Jinja2 template.

    Any arguments after '--' are forwarded to 'zig build' so you can inspect
    non-default build options (e.g. '-- -Dbackend=wayland').
    """
    zig_build_args = _extract_zig_build_args(ctx)
    try:
        model = analyze_project(
            project_path=project or Path("."),
            zig_exe=zig,
            cache_dir=cache_dir,
            fetch=fetch,
            zig_build_args=zig_build_args,
            tarball_dir=tarball_dir,
        )
        rendered = render_template(model, template_path=template)
    except ZigRecipeError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _write_output(rendered, output, "recipe.out")
