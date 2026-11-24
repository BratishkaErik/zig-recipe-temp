# Gentoo Ebuilds

`zig-recipe gentoo` generates EAPI 8 ebuilds targeting Gentoo's `zig.eclass`.
You can use it for a quick single-pass ebuild, or pair it with a project
config to wire up `IUSE` and `src_configure()`.

## Basic usage

Running `zig-recipe gentoo` against a project directory (or `build.zig`)
writes `<name>-<version>.ebuild` in the current directory:

```console
$ zig-recipe gentoo ./river
```

If you prefer to inspect the result in your terminal first, `-o -` prints the
ebuild to standard output instead.

When bumping an existing ebuild to a new release, you often only need the
updated dependency table. Passing `--deps-only` prints just the
`ZBS_DEPENDENCIES` array to standard output:

```console
$ zig-recipe gentoo ./river --deps-only
```

## Helper comments and unused dependencies

By default, `zig-recipe gentoo` annotates the generated ebuild with detected
system libraries, build options, and external tools so you can review them
while finishing the ebuild by hand.

Two flags control how much of that extra detail is included: `--comments` and
`--unused-deps`. Both can also be stored under `[output.gentoo]` in a
[project config](project-config.md) or in your [user config](user-config.md)
(alongside your default `keywords`) so you do not have to repeat personal
preferences for every package.

### Comment detail (`--comments`)

| Level | What is included | Default `--unused-deps` |
| --- | --- | --- |
| **`full`** *(default)* | Descriptions for every build option, system library users, and build-time tools. | `comment` |
| **`compact`** | One-line summaries for options, libraries, and tools. | `comment` |
| **`none`** | No helper comments at all. | `drop` |

### Unused dependencies (`--unused-deps`)

Many Zig projects declare lazy dependencies for other operating systems or
vendored C libraries that are not needed in a Linux `--system` build.
`--unused-deps` controls how packages classified as `unused` appear in
`ZBS_DEPENDENCIES`:

- **`comment`** *(default with `full` and `compact`)*: comments out unused
  entries (`# [hash.tar.gz]='url'`) so Portage does not download them, while
  keeping them visible in case you want to uncomment one later.
- **`drop`** *(default with `none`)*: omits unused entries completely.
- **`keep`**: keeps unused entries uncommented in `ZBS_DEPENDENCIES`.

> [!NOTE]
> Combining `--comments none` with `--unused-deps comment` is rejected, since
> there are no comments to emit. You can still pass `--unused-deps keep` if
> you want unused entries included uncommented.

## Multi-pass mapping

A single-pass `zig-recipe gentoo ./project` is enough when a package has no
optional features to toggle. When a project exposes build options that you
want to wire up as Gentoo USE flags, passing a `--config` file with
`[build.variants]` (see [Multi-Pass Analysis](multi-pass.md)) lets the Gentoo
renderer populate `IUSE`, `src_configure()`, and conditional hints
automatically.

### `IUSE` and `RESTRICT`

Valid Gentoo USE flag names from `[build.variants]` are sorted alphabetically
and written into `IUSE`.

If the built-in `test` pass finds any test-only dependencies or libraries,
`zig-recipe` adds `test` to `IUSE` and emits `RESTRICT="!test? ( test )"`. The
same happens when you define a custom `test` variant.

### `src_configure()`

In `src_configure()`, `zig-recipe` puts `[build].base_args` into `my_zbs_args`
and translates each variant according to its shape:

| Variant shape in TOML | Generated Bash in `src_configure()` |
| --- | --- |
| Single `-D<opt>` toggle (`{ on, off }`) | `-D<opt>=$(usex <flag> <on> <off>)` |
| Single additive argument (`["-D<opt>=true"]`) | `$(usev <flag> -D<opt>=true)` |
| Multiple arguments or custom combinations | `if use <flag>; then ... else ... fi` |

For example:

```toml
[build.variants]
man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
X = ["-Dxwayland=true"]
extra = { on = ["-Da=1", "-Db=2"], off = ["-Da=0"] }
```

produces:

```bash
IUSE="X extra man"

src_configure() {
	local my_zbs_args=(
		-Dman-pages=$(usex man true false)
		$(usev X -Dxwayland=true)
	)

	if use extra; then
		my_zbs_args+=( -Da=1 -Db=2 )
	else
		my_zbs_args+=( -Da=0 )
	fi

	zig_src_configure
}
```

Any build option already handled by `base_args` or `[build.variants]` is
omitted from the commented-out option placeholders, leaving only the options
you have not configured yet under `# Other project build options:`.

### Conditional hints

System libraries, packages, and build-time tools that appear only in `test` or
in a named variant are annotated with Gentoo USE hints, such as
`check (test?)` or `wayland-client (wayland?)`.

## Walkthrough (`river 0.5.2`)

Here is a complete `river.toml` using `[package]`, `[build]`, and
`[output.gentoo]` (with `{ on, off }` for both flags so both generate explicit
`$(usex ...)` arguments):

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
src_uri = "https://codeberg.org/river/river/releases/download/v${PV}/${P}.tar.gz"
license = "GPL-3"
```

Running `zig-recipe gentoo ./river --config river.toml -o -` produces:

```bash
# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

# Generated by zig-recipe 0.1.0

EAPI=8

declare -g -r -A ZBS_DEPENDENCIES=(
	[pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX.tar.gz]='https://codeberg.org/ifreund/zig-pixman/archive/v0.3.0.tar.gz'
	[wayland-0.7.0-lQa1kuP8AQD5dW7_o-VKLVD_QbqXe7Zob78r2su7Pyex.tar.gz]='https://codeberg.org/ifreund/zig-wayland/archive/v0.7.0.tar.gz'
	[wlroots-0.20.3-jmOlcsh0BACce9rpfvzUI4V3AalGg9GE0QQkvRFoHo1c.tar.gz]='https://codeberg.org/ifreund/zig-wlroots/archive/v0.20.3.tar.gz'
	[xkbcommon-0.5.0-VDqIe8y3AgDU-cGL02xZplwABH3WbpTimrrgbgTgD_3g.tar.gz]='https://codeberg.org/ifreund/zig-xkbcommon/archive/v0.5.0.tar.gz'

	# Not used in any analyzed pass:
	# [pixman-0.4.0-LClMn_eWAAAa2zACATDpPwjuI5fWlO5cBp_F_M33hnNZ.tar.gz]='https://codeberg.org/ifreund/zig-pixman/archive/v0.4.0.tar.gz'
)

# C/C++ sources detected; verify if LLVM backend is required
ZIG_NEEDS_LLVM=1
ZIG_SLOT="0.17"
inherit zig

DESCRIPTION="Dynamic tiling Wayland compositor"
HOMEPAGE="https://codeberg.org/river/river"
SRC_URI="
	https://codeberg.org/river/river/releases/download/v${PV}/${P}.tar.gz
	${ZBS_DEPENDENCIES_SRC_URI}
"

LICENSE="GPL-3"
SLOT="0"
KEYWORDS="~amd64"
IUSE="X man"

# Detected system libraries:
# * libevdev, used by: (artifact) river, (module) c, (translate-c) translate-c.
# * libinput, used by: (artifact) river, (module) c, (translate-c) translate-c.
# * pixman-1, used by: (artifact) river.
# * wayland-server, used by: (artifact) river.
# * wlroots-0.20, used by: (artifact) river, (module) c, (translate-c) translate-c.
# * xkbcommon, used by: (artifact) river.

src_configure() {
	local my_zbs_args=(
		-Dpie=true
		-Dman-pages=$(usex man true false)
		-Dxwayland=$(usex X true false)

		# Other project build options:

		# Omit debug information
		# -Dstrip=[bool]

		# Force use of Zig's LLVM backend and the lld linker
		# -Dllvm=[bool]

		# Override `river -version` output.
		# -Dversion-string=[string]
	)

	zig_src_configure
}
```

### What to review by hand

`zig-recipe` handles dependency URLs, `ZIG_SLOT`, `IUSE`, and `src_configure()`,
leaving a few Gentoo-specific details for you to finish:

- **`DEPEND` and `RDEPEND`:** map the detected system libraries (such as
  `libevdev` and `wlroots-0.20`) to Gentoo package atoms.
- **`BDEPEND`:** add build-time tools such as code generators or documentation
  tools (including ones discovered through `b.findProgram` that `zig-recipe`
  cannot trace automatically).
- **Remaining build options:** review the commented options under
  `# Other project build options:` and decide if any belong in `my_zbs_args`.
- **`TODO` placeholders:** when running without a `--config` file, fill in
  `DESCRIPTION`, `HOMEPAGE`, `SRC_URI`, and `LICENSE`.
