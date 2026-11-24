"""Configuration loading (global user config + project config) and pass-plan construction."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

from zig_recipe.errors import ConfigError

RESERVED_VARIANT_NAMES = frozenset({"default"})
_VALID_GENTOO_COMMENTS = frozenset({"full", "compact", "none"})
_VALID_GENTOO_UNUSED_DEPS = frozenset({"comment", "drop", "keep"})


@dataclass(frozen=True, slots=True)
class GeneralConfig:
    """User-level machine/environment defaults from `[general]` in the global config."""

    zig: str | None = None
    cache_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    """Optional cross-distro project metadata from `[package]`."""

    description: str | None = None
    homepage: tuple[str, ...] = ()
    license: str | None = None


@dataclass(frozen=True, slots=True)
class GentooOutputConfig:
    """Optional Gentoo renderer settings from `[output.gentoo]`."""

    comments: str | None = None
    unused_deps: str | None = None
    src_uri: tuple[str, ...] = ()
    keywords: str | None = None
    license: str | None = None


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """A named build variant: either additive (`on_args`) or a toggle (`on_args`/`off_args`)."""

    name: str
    on_args: tuple[str, ...]
    off_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PassConfig:
    """User-supplied base arguments and named variants for multi-pass analysis."""

    base_args: tuple[str, ...] = ()
    variants: tuple[VariantSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class UserConfig:
    """Global user configuration loaded from `~/.config/zig-recipe/config.toml`."""

    general: GeneralConfig = GeneralConfig()
    gentoo: GentooOutputConfig = GentooOutputConfig()


@dataclass(frozen=True, slots=True)
class RecipeConfig:
    """Project TOML configuration (`[package]`, `[build]`, `[output.gentoo]`)."""

    package: PackageMetadata = PackageMetadata()
    build: PassConfig = PassConfig()
    gentoo: GentooOutputConfig = GentooOutputConfig()


def default_user_config_path() -> Path:
    """Return the platform-standard global user config path (`.../zig-recipe/config.toml`)."""
    return user_config_path("zig-recipe") / "config.toml"


def merge_gentoo_config(
    *,
    user: GentooOutputConfig,
    project: GentooOutputConfig,
) -> GentooOutputConfig:
    """Merge global user `[output.gentoo]` defaults with project overrides."""
    return GentooOutputConfig(
        comments=project.comments if project.comments is not None else user.comments,
        unused_deps=project.unused_deps if project.unused_deps is not None else user.unused_deps,
        src_uri=project.src_uri,
        keywords=project.keywords if project.keywords is not None else user.keywords,
        license=project.license,
    )


@dataclass(frozen=True, slots=True)
class PlannedInvocation:
    """A single `zig build --print-configuration-path` invocation to execute."""

    name: str
    args: tuple[str, ...]


def _validate_variant_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError("Variant name cannot be empty.")
    if cleaned in RESERVED_VARIANT_NAMES:
        raise ConfigError(f"Variant name {cleaned!r} is reserved.")
    return cleaned


def _validate_str_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"Expected a list of strings for {field!r}.")
    return tuple(value)


def _validate_optional_str(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Expected a string for {field!r}.")
    return value


def _validate_str_or_list(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item for item in value if item.strip())
    raise ConfigError(f"Expected a string or list of strings for {field!r}.")


def _parse_package_section(raw_pkg: object, *, source: str) -> PackageMetadata:
    if raw_pkg is None:
        return PackageMetadata()
    if not isinstance(raw_pkg, dict):
        raise ConfigError(f"Expected [package] table in {source}.")
    unknown = set(raw_pkg.keys()) - {"description", "homepage", "license"}
    if unknown:
        raise ConfigError(f"Unknown key(s) in [package] ({source}): {', '.join(sorted(unknown))}.")
    return PackageMetadata(
        description=_validate_optional_str(raw_pkg.get("description"), field="package.description"),
        homepage=_validate_str_or_list(raw_pkg.get("homepage"), field="package.homepage"),
        license=_validate_optional_str(raw_pkg.get("license"), field="package.license"),
    )


def _parse_general_section(raw_general: object, *, source: str) -> GeneralConfig:
    if raw_general is None:
        return GeneralConfig()
    if not isinstance(raw_general, dict):
        raise ConfigError(f"Expected [general] table in {source}.")
    unknown = set(raw_general.keys()) - {"zig", "cache_dir"}
    if unknown:
        raise ConfigError(f"Unknown key(s) in [general] ({source}): {', '.join(sorted(unknown))}.")
    zig_exe = _validate_optional_str(raw_general.get("zig"), field="general.zig")
    raw_cache = _validate_optional_str(raw_general.get("cache_dir"), field="general.cache_dir")
    cache_dir = Path(raw_cache).expanduser() if raw_cache else None
    return GeneralConfig(zig=zig_exe, cache_dir=cache_dir)


def _parse_build_section(raw_build: object, *, source: str) -> PassConfig:
    if raw_build is None:
        return PassConfig()
    if not isinstance(raw_build, dict):
        raise ConfigError(f"Expected [build] table in {source}.")
    unknown = set(raw_build.keys()) - {"base_args", "variants"}
    if unknown:
        raise ConfigError(f"Unknown key(s) in [build] ({source}): {', '.join(sorted(unknown))}.")

    base_args = _validate_str_list(raw_build.get("base_args", []), field="build.base_args")

    raw_variants = raw_build.get("variants", {})
    if not isinstance(raw_variants, dict):
        raise ConfigError(f"Expected [build.variants] table in {source}.")

    variants: list[VariantSpec] = []
    for raw_name, entry in raw_variants.items():
        name = _validate_variant_name(str(raw_name))
        if isinstance(entry, list):
            on_args = _validate_str_list(entry, field=f"build.variants.{name}")
            if not on_args:
                raise ConfigError(f"build.variants.{name} must not be empty.")
            variants.append(VariantSpec(name=name, on_args=on_args))
        elif isinstance(entry, dict):
            unknown_keys = set(entry.keys()) - {"on", "off"}
            if unknown_keys:
                raise ConfigError(
                    f"Unknown key(s) in build.variants.{name}: {', '.join(sorted(unknown_keys))}."
                )
            if "on" not in entry:
                raise ConfigError(f"build.variants.{name} table must define 'on'.")
            on_args = _validate_str_list(entry["on"], field=f"build.variants.{name}.on")
            if not on_args:
                raise ConfigError(f"build.variants.{name}.on must not be empty.")
            off_args = _validate_str_list(entry.get("off", []), field=f"build.variants.{name}.off")
            variants.append(VariantSpec(name=name, on_args=on_args, off_args=off_args))
        else:
            raise ConfigError(
                f"build.variants.{name} must be a list of args or a table with 'on'/'off' lists."
            )

    return PassConfig(base_args=base_args, variants=tuple(variants))


def _parse_output_section(
    raw_output: object,
    *,
    source: str,
    allowed_gentoo_keys: frozenset[str] = frozenset(
        {"comments", "unused_deps", "src_uri", "keywords", "license"}
    ),
) -> GentooOutputConfig:
    if raw_output is None:
        return GentooOutputConfig()
    if not isinstance(raw_output, dict):
        raise ConfigError(f"Expected [output] table in {source}.")
    unknown_targets = set(raw_output.keys()) - {"gentoo"}
    if unknown_targets:
        raise ConfigError(
            f"Unknown key(s) in [output] ({source}): {', '.join(sorted(unknown_targets))}."
        )

    raw_gentoo = raw_output.get("gentoo")
    if raw_gentoo is None:
        return GentooOutputConfig()
    if not isinstance(raw_gentoo, dict):
        raise ConfigError(f"Expected [output.gentoo] table in {source}.")

    unknown_keys = set(raw_gentoo.keys()) - allowed_gentoo_keys
    if unknown_keys:
        raise ConfigError(
            f"Unknown key(s) in [output.gentoo] ({source}): {', '.join(sorted(unknown_keys))}."
        )

    comments = _validate_optional_str(raw_gentoo.get("comments"), field="output.gentoo.comments")
    if comments is not None and comments not in _VALID_GENTOO_COMMENTS:
        raise ConfigError(
            f"Invalid output.gentoo.comments {comments!r}: expected full, compact, or none."
        )

    unused_deps = _validate_optional_str(
        raw_gentoo.get("unused_deps"), field="output.gentoo.unused_deps"
    )
    if unused_deps is not None and unused_deps not in _VALID_GENTOO_UNUSED_DEPS:
        raise ConfigError(
            f"Invalid output.gentoo.unused_deps {unused_deps!r}: expected comment, drop, or keep."
        )

    src_uri = _validate_str_or_list(raw_gentoo.get("src_uri"), field="output.gentoo.src_uri")

    raw_keywords = raw_gentoo.get("keywords")
    if raw_keywords is None:
        keywords = None
    elif isinstance(raw_keywords, str):
        keywords = raw_keywords
    elif isinstance(raw_keywords, list) and all(isinstance(item, str) for item in raw_keywords):
        keywords = " ".join(item.strip() for item in raw_keywords if item.strip())
    else:
        raise ConfigError("Expected a string or list of strings for 'output.gentoo.keywords'.")

    license_str = _validate_optional_str(raw_gentoo.get("license"), field="output.gentoo.license")

    return GentooOutputConfig(
        comments=comments,
        unused_deps=unused_deps,
        src_uri=src_uri,
        keywords=keywords,
        license=license_str,
    )


def load_config_text(text: str, *, source: str = "<stdin>") -> RecipeConfig:
    """Parse TOML configuration text (`[package]`, `[build]`, `[output.gentoo]`)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Failed to parse config {source}: {exc}") from exc

    unknown_top = set(raw.keys()) - {"package", "build", "output"}
    if unknown_top:
        raise ConfigError(
            f"Unknown top-level key(s) in {source}: {', '.join(sorted(unknown_top))}."
        )

    return RecipeConfig(
        package=_parse_package_section(raw.get("package"), source=source),
        build=_parse_build_section(raw.get("build"), source=source),
        gentoo=_parse_output_section(raw.get("output"), source=source),
    )


def load_user_config_text(text: str, *, source: str = "<user-config>") -> UserConfig:
    """Parse global user TOML config (`[general]` and `[output.gentoo]` user preferences)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Failed to parse user config {source}: {exc}") from exc

    unknown_top = set(raw.keys()) - {"general", "output"}
    if unknown_top:
        raise ConfigError(
            f"Unknown top-level key(s) in user config {source}: {', '.join(sorted(unknown_top))}."
        )

    return UserConfig(
        general=_parse_general_section(raw.get("general"), source=source),
        gentoo=_parse_output_section(
            raw.get("output"),
            source=source,
            allowed_gentoo_keys=frozenset({"comments", "unused_deps", "keywords"}),
        ),
    )


def load_user_config_file(path: Path | None = None) -> UserConfig:
    """Load global user config if present; return empty defaults if the file does not exist."""
    cfg_path = path if path is not None else default_user_config_path()
    if not cfg_path.is_file():
        return UserConfig()
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Failed to read user config {cfg_path}: {exc}") from exc
    return load_user_config_text(text, source=str(cfg_path))


def load_config_file(path: Path) -> RecipeConfig:
    """Load a TOML configuration file (`[package]`, `[build]`, `[output.gentoo]`)."""
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Failed to read config file {path}: {exc}") from exc
    return load_config_text(text, source=str(path))


def merge_pass_config(
    *,
    file_config: PassConfig | None = None,
    cli_base_args: tuple[str, ...] = (),
) -> PassConfig:
    """Combine optional TOML config with extra CLI base args after `--`."""
    base_args = (file_config.base_args if file_config else ()) + cli_base_args
    variants = file_config.variants if file_config else ()
    return PassConfig(base_args=base_args, variants=variants)


def _d_option_key(arg: str) -> str | None:
    """Return the option name for `-Dname` or `-Dname=value`, else None."""
    if not arg.startswith("-D") or len(arg) <= 2:
        return None
    body = arg[2:]
    return body.split("=", 1)[0]


def _merge_zig_args(base: tuple[str, ...], overlay: tuple[str, ...]) -> tuple[str, ...]:
    """Overlay Zig build args onto base args, replacing any overridden `-D<name>` options.

    In Zig's `std.Build`, passing `-Dname=a -Dname=b` turns `name` into a list rather than
    overriding `a` with `b`. Filtering overridden `-D` keys lets variants cleanly override
    baseline `-D` options (including enum and bool options).
    """
    overlay_keys = {key for arg in overlay if (key := _d_option_key(arg)) is not None}
    filtered = [arg for arg in base if _d_option_key(arg) not in overlay_keys]
    return tuple(filtered) + overlay


def build_zig_invocations(config: PassConfig) -> tuple[PlannedInvocation, ...]:
    """Expand a PassConfig into `1 + N` Zig build invocations (`default` + each variant)."""
    all_off = tuple(arg for v in config.variants for arg in v.off_args)
    invocations: list[PlannedInvocation] = [
        PlannedInvocation(name="default", args=_merge_zig_args(config.base_args, all_off))
    ]

    for variant in config.variants:
        other_off = tuple(
            arg for other in config.variants if other.name != variant.name for arg in other.off_args
        )
        base_with_off = _merge_zig_args(config.base_args, other_off)
        invocations.append(
            PlannedInvocation(
                name=variant.name,
                args=_merge_zig_args(base_with_off, variant.on_args),
            )
        )

    return tuple(invocations)
