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

from zig_recipe import __version__
from zig_recipe.config import (
    PassConfig,
    RecipeConfig,
    UserConfig,
    load_config_file,
    load_config_text,
    load_user_config_file,
    merge_gentoo_config,
    merge_pass_config,
)
from zig_recipe.errors import ZigRecipeError
from zig_recipe.gentoo.renderer import (
    CommentStyle,
    UnusedDepsMode,
    default_gentoo_filename,
    render_gentoo_deps_only,
    render_gentoo_ebuild,
    resolve_unused_deps_mode,
)
from zig_recipe.jinja import render_template
from zig_recipe.model import RecipeModel
from zig_recipe.pipeline import analyze_project
from zig_recipe.ui.progress import ProgressReporter
from zig_recipe.ui.tables import print_inspect_tables

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


# Shared Typer option / argument type aliases across commands.
ProjectArg = Annotated[
    Path | None,
    typer.Argument(
        help="Project directory or build.zig (defaults to current directory).",
        show_default=False,
    ),
]
ZigOpt = Annotated[
    str | None,
    typer.Option("--zig", help="Zig executable to use (default: zig).", show_default=False),
]
ConfigOpt = Annotated[
    str | None,
    typer.Option(
        "--config",
        help="TOML configuration file ('-' for stdin).",
        show_default=False,
    ),
]
NoUserConfigOpt = Annotated[
    bool,
    typer.Option(
        "--no-user-config",
        help="Ignore the global user config file (~/.config/zig-recipe/config.toml).",
        rich_help_panel="Advanced",
    ),
]
FetchOpt = Annotated[
    bool,
    typer.Option(
        "--fetch/--no-fetch",
        help="Download missing dependencies into cache (use --no-fetch to run offline).",
    ),
]
TarballDirOpt = Annotated[
    Path | None,
    typer.Option(
        "--tarball-dir",
        help=(
            "Where to save the bundled git-dependencies archive, if any git URLs "
            "cannot be converted to tarballs."
        ),
        rich_help_panel="Advanced",
    ),
]
CacheDirOpt = Annotated[
    Path | None,
    typer.Option(
        "--cache-dir",
        envvar="ZIG_RECIPE_CACHE_DIR",
        help="Override zig-recipe cache directory.",
        rich_help_panel="Advanced",
    ),
]


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


def _load_effective_configs(
    *,
    ctx: typer.Context,
    config_source: str | None,
    no_user_config: bool,
) -> tuple[UserConfig, RecipeConfig, PassConfig]:
    user_cfg = UserConfig() if no_user_config else load_user_config_file()
    project_cfg = RecipeConfig()
    if config_source is not None:
        if config_source == "-":
            project_cfg = load_config_text(sys.stdin.read(), source="<stdin>")
        else:
            project_cfg = load_config_file(Path(config_source))

    merged_recipe = RecipeConfig(
        package=project_cfg.package,
        build=project_cfg.build,
        gentoo=merge_gentoo_config(user=user_cfg.gentoo, project=project_cfg.gentoo),
    )
    pass_cfg = merge_pass_config(
        file_config=project_cfg.build,
        cli_base_args=_extract_zig_build_args(ctx),
    )
    return user_cfg, merged_recipe, pass_cfg


def _run_analysis(
    *,
    ctx: typer.Context,
    project: Path | None,
    zig: str | None,
    config: str | None,
    fetch: bool,
    no_user_config: bool,
    cache_dir: Path | None,
    tarball_dir: Path | None = None,
    configs: tuple[UserConfig, RecipeConfig, PassConfig] | None = None,
) -> RecipeModel:
    user_cfg, recipe_cfg, pass_config = (
        configs
        if configs is not None
        else _load_effective_configs(
            ctx=ctx,
            config_source=config,
            no_user_config=no_user_config,
        )
    )
    effective_zig = zig or user_cfg.general.zig or "zig"
    effective_cache = cache_dir if cache_dir is not None else user_cfg.general.cache_dir
    with ProgressReporter(stderr_console) as progress:
        return analyze_project(
            project_path=project or Path("."),
            zig_exe=effective_zig,
            cache_dir=effective_cache,
            fetch=fetch,
            pass_config=pass_config,
            recipe_config=recipe_cfg,
            tarball_dir=tarball_dir,
            progress=progress,
        )


def _write_output(content: str, output: str | None, default_name: str) -> None:
    if output == "-":
        sys.stdout.write(content)
        return
    target = Path(output) if output is not None else Path(default_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    stderr_console.print(f"[green]Wrote[/green] {target}")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def gentoo(
    ctx: typer.Context,
    project: ProjectArg = None,
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
    zig: ZigOpt = None,
    comments: Annotated[
        CommentStyle | None,
        typer.Option(
            "--comments",
            case_sensitive=False,
            help="Comment detail level: full, compact, or none (default: full).",
            show_default=False,
        ),
    ] = None,
    unused_deps: Annotated[
        UnusedDepsMode | None,
        typer.Option(
            "--unused-deps",
            case_sensitive=False,
            help=(
                "How to handle unused dependencies in ZBS_DEPENDENCIES "
                "(default: comment, or drop with --comments none)."
            ),
            show_default=False,
        ),
    ] = None,
    deps_only: Annotated[
        bool,
        typer.Option(
            "--deps-only",
            help="Print only the ZBS_DEPENDENCIES array (handy when bumping an ebuild).",
        ),
    ] = False,
    config: ConfigOpt = None,
    fetch: FetchOpt = True,
    template: Annotated[
        Path | None,
        typer.Option(
            "--template",
            help="Custom Jinja2 template overriding the built-in one.",
            rich_help_panel="Advanced",
        ),
    ] = None,
    experimental_zbs_packages: Annotated[
        bool,
        typer.Option(
            "--experimental-zbs-packages",
            help=(
                "Emit ZBS_PACKAGES / ZBS_BUNDLED_PACKAGES with conditional SRC_URI "
                "(requires zig.eclass support)."
            ),
            rich_help_panel="Advanced",
        ),
    ] = False,
    no_user_config: NoUserConfigOpt = False,
    tarball_dir: TarballDirOpt = None,
    cache_dir: CacheDirOpt = None,
) -> None:
    """Generate a Gentoo ebuild.

    Any arguments after '--' are forwarded to 'zig build' as base arguments
    (e.g. '-- -Dbackend=wayland'). Use '--config' to run additional variant
    passes and detect conditional dependencies.
    """
    try:
        configs = _load_effective_configs(
            ctx=ctx,
            config_source=config,
            no_user_config=no_user_config,
        )
        _, recipe_cfg, _ = configs
        effective_comments = (
            comments
            if comments is not None
            else (
                CommentStyle(recipe_cfg.gentoo.comments)
                if recipe_cfg.gentoo.comments is not None
                else CommentStyle.FULL
            )
        )
        effective_unused_opt = (
            unused_deps
            if unused_deps is not None
            else (
                UnusedDepsMode(recipe_cfg.gentoo.unused_deps)
                if recipe_cfg.gentoo.unused_deps is not None
                else None
            )
        )
        effective_unused = resolve_unused_deps_mode(effective_comments, effective_unused_opt)

        model = _run_analysis(
            ctx=ctx,
            project=project,
            zig=zig,
            config=config,
            fetch=fetch,
            no_user_config=no_user_config,
            cache_dir=cache_dir,
            tarball_dir=tarball_dir,
            configs=configs,
        )
        if deps_only:
            rendered = render_gentoo_deps_only(
                model,
                comments=effective_comments,
                unused_deps=effective_unused,
                use_zbs_packages=experimental_zbs_packages,
            )
        else:
            rendered = render_gentoo_ebuild(
                model,
                comments=effective_comments,
                unused_deps=effective_unused,
                use_zbs_packages=experimental_zbs_packages,
                template_path=template,
            )
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
    project: ProjectArg = None,
    fmt: Annotated[
        InspectFormat,
        typer.Option("--format", "-f", case_sensitive=False, help="Output format."),
    ] = InspectFormat.TABLE,
    zig: ZigOpt = None,
    config: ConfigOpt = None,
    fetch: FetchOpt = True,
    no_user_config: NoUserConfigOpt = False,
    cache_dir: CacheDirOpt = None,
) -> None:
    """Print build metadata (packages, options, system libraries) as a table or JSON.

    Any arguments after '--' are forwarded to 'zig build' as base arguments
    (e.g. '-- -Dbackend=wayland'). Use '--config' to run additional variant
    passes and detect conditional dependencies.
    """
    try:
        model = _run_analysis(
            ctx=ctx,
            project=project,
            zig=zig,
            config=config,
            fetch=fetch,
            no_user_config=no_user_config,
            cache_dir=cache_dir,
        )
    except ZigRecipeError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if fmt is InspectFormat.JSON:
        sys.stdout.write(json.dumps(model.to_dict(), indent=2) + "\n")
    else:
        print_inspect_tables(stdout_console, model)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def render(
    ctx: typer.Context,
    template: Annotated[
        Path,
        typer.Argument(help="Path to a Jinja2 template file."),
    ],
    project: ProjectArg = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output file path ('-' for stdout)."),
    ] = "-",
    zig: ZigOpt = None,
    config: ConfigOpt = None,
    fetch: FetchOpt = True,
    no_user_config: NoUserConfigOpt = False,
    tarball_dir: TarballDirOpt = None,
    cache_dir: CacheDirOpt = None,
) -> None:
    """Generate a recipe using a custom Jinja2 template.

    Any arguments after '--' are forwarded to 'zig build' as base arguments
    (e.g. '-- -Dbackend=wayland'). Use '--config' to run additional variant
    passes and detect conditional dependencies.
    """
    try:
        model = _run_analysis(
            ctx=ctx,
            project=project,
            zig=zig,
            config=config,
            fetch=fetch,
            no_user_config=no_user_config,
            cache_dir=cache_dir,
            tarball_dir=tarball_dir,
        )
        rendered = render_template(model, template_path=template)
    except ZigRecipeError as exc:
        stderr_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _write_output(rendered, output, "recipe.out")
