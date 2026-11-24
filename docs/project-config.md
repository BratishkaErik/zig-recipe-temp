# Project Configuration

A project config saves your packaging settings for one project in a TOML file.
You can use it to store:

- build arguments and multi-pass variants,
- project metadata (description, homepage, and license),
- output settings for a specific renderer (currently Gentoo).

All examples on this page use `river 0.5.2`.

## Starting with build variants

Every section in a project config is optional. A minimal `river.toml` often
starts with just `[build]` and `[build.variants]`:

```toml
[build]
base_args = ["-Dpie=true"]

[build.variants]
man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
X = ["-Dxwayland=true"]
```

`base_args` lists `zig build` arguments shared by every pass (`default`,
`test`, and every named variant). It is a good home for build-wide choices
such as `-Dpie=true`, `-Dstrip=false`, `-fsys=...`, or `-fno-sys=...`.

Under `[build.variants]`, each entry defines one named pass:

- A **simple list** passes extra arguments only in that variant, which fits
  options that are already off by default in `build.zig`.
- An **`{ on, off }` table** passes `off` in `default` and every other
  variant, and passes `on` only in that variant. This fits options that
  default to on or auto-detect from the host.
- If a project needs an extra flag just to build or enable its test suite, you
  can also define a `test` entry (for example, `test = ["-Denable-tests=true"]`).

> [!NOTE]
> The name `default` is reserved for the baseline pass. See
> [Multi-Pass Analysis](multi-pass.md) for a full walkthrough of choosing a
> baseline and checking option defaults in `build.zig`.

## Adding package metadata

With `[build]` and `[build.variants]` in place, `zig-recipe` already traces
your optional dependencies and system libraries. Generated recipes still leave
`TODO` placeholders for description, homepage, and license, because
`build.zig.zon` does not have standard fields for them yet.

Adding a `[package]` section lets you record those details once so they carry
through to `inspect --format json`, custom Jinja2 templates, and built-in
renderers:

```toml
[package]
description = "Dynamic tiling Wayland compositor"
homepage = "https://codeberg.org/river/river"
license = "GPL-3.0-only"

[build]
base_args = ["-Dpie=true"]

[build.variants]
man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
X = ["-Dxwayland=true"]
```

- `description` sets a short one-line summary (and overrides `summary` in
  `build.zig.zon` if present).
- `homepage` accepts either a single URL or a list of URLs.
- `license` holds a project license string, such as an SPDX expression.

## Adding Gentoo settings

When you generate Gentoo ebuilds, `[output.gentoo]` lets you fill in the
remaining Gentoo-specific fields right alongside your build variants.

In the example below, `X` also switches from a simple list to an `{ on, off }`
table. For `zig-recipe gentoo`, that tells the renderer to emit an explicit
`-Dxwayland=$(usex X true false)` instead of `$(usev X -Dxwayland=true)`, so
both states are pinned even if upstream ever changes the default:

```toml
[package]
description = "Dynamic tiling Wayland compositor"
homepage = "https://codeberg.org/river/river"
license = "GPL-3.0-only"

[build]
base_args = ["-Dpie=true"]

[build.variants]
man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
X = { on = ["-Dxwayland=true"], off = ["-Dxwayland=false"] }

[output.gentoo]
comments = "compact"
src_uri = "https://codeberg.org/river/river/releases/download/v${PV}/${P}.tar.gz"
keywords = ["~amd64", "~arm64"]
license = "GPL-3"
```

Some fields here belong naturally to the project itself:

- `src_uri` sets the project source URL(s) placed above
  `${ZBS_DEPENDENCIES_SRC_URI}` (string or list of strings).
- `license` overrides `[package].license` when Gentoo's license name differs
  from SPDX.

You can also override your usual output style per project when needed:

- `comments` chooses the helper comment level (`"full"`, `"compact"`, or
  `"none"`).
- `unused_deps` controls how unused dependencies appear in `ZBS_DEPENDENCIES`
  (`"comment"`, `"drop"`, or `"keep"`).
- `keywords` sets `KEYWORDS` (string or list of strings, joined with spaces).

> [!TIP]
> Unlike a [user config](user-config.md), a project config does not have to
> live at a fixed path or use a special filename. Keep it wherever it fits
> your workflow (for example, alongside your packaging notes or in a separate
> repository) and point `--config` at it.

## Using the config

Pass your config file with `--config`:

```console
$ zig-recipe gentoo ./river --config river.toml
```

Any flag you pass on the command line (such as `--comments` or
`--unused-deps`) overrides the matching setting in your project config.

Arguments forwarded to `zig build` after `--` work differently: they are
appended to `[build].base_args` rather than replacing it, so you can layer a
one-off build option on top of your saved file.

> [!TIP]
> For quick one-off experiments without saving a file on disk, pass
> `--config -` to read TOML directly from standard input.

For personal and machine-wide defaults that apply across all projects, see
[User Configuration](user-config.md).
