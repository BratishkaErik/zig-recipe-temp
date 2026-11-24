# User Configuration

Some settings belong to you or your machine rather than to a single package.
Instead of repeating them in every [project config](project-config.md), you
can put them in one user config file that `zig-recipe` loads automatically:

- which Zig executable and cache directory to use,
- your personal Gentoo output defaults (comment style, unused dependencies,
  and default `KEYWORDS`).

## File location

`zig-recipe` looks for `config.toml` in your OS user config directory:

- **Linux:** `~/.config/zig-recipe/config.toml` (respecting `XDG_CONFIG_HOME`)
- **macOS:** `~/Library/Application Support/zig-recipe/config.toml`
- **Windows:** `%APPDATA%\zig-recipe\config.toml`

When this file exists, `zig-recipe` loads it automatically on every run.
Whenever you need a clean run unaffected by local preferences (for example, in
CI or a shared script), pass `--no-user-config`.

## Machine settings

The `[general]` section sets the default Zig binary and cache directory for
this machine:

```toml
[general]
zig = "zig-0.17.1"
cache_dir = "/var/tmp/zig-recipe-cache"
```

- `zig` — command name on `PATH` (handy if you have several Zig versions
  installed) or a full path.
- `cache_dir` — lets you keep the
  [local dependency cache](how-it-works.md#1-fetching-and-caching) in a custom
  folder (for example, on another drive).

## Gentoo defaults

If you prefer a different comment style or target another architecture by
default, `[output.gentoo]` applies those choices to every ebuild you generate:

```toml
[output.gentoo]
comments = "compact"
unused_deps = "drop"
keywords = "~arm64"
```

- `comments` — `"full"`, `"compact"`, or `"none"`.
- `unused_deps` — `"comment"`, `"drop"`, or `"keep"`.
- `keywords` — a string or a list of strings.

> [!NOTE]
> Every section and key in `config.toml` is optional: a two-line file with
> only `[output.gentoo]` and `comments = "compact"` is completely valid.
> Project-specific sections such as `[package]` and `[build]` are not allowed
> here and belong in a [project config](project-config.md) instead.

## Overrides

When the same setting appears in more than one place, earlier sources win:

1. Command-line flags and environment variables (such as `ZIG_RECIPE_CACHE_DIR`).
2. A project `--config` file.
3. Your user config (`config.toml`).
