# Multi-Pass Analysis

When you package a Zig project with optional features, you usually need to
answer three questions:

- Which packages and libraries are needed in *every* build?
- Which ones only appear when a feature (like man pages or XWayland) or the
  test suite is enabled?
- Which lazy dependencies in `build.zig.zon` are never used in any of your
  builds?

Multi-pass analysis answers all three in a single `zig-recipe` run. It inspects
a baseline build, the test step, and any extra feature variants you define, then
compares the results.

All examples on this page use `river 0.5.2`.

## Built-in passes

A **pass** is one traced view of the project's build graph. Each pass records
three things: its name, the `zig build` arguments it used, and the top-level
build steps it traced.

Every `zig-recipe` run starts with a built-in pass called `default`. This pass
traces the project's default build step (usually `install`) using your base
arguments.

If the project also defines a `test` step separately from the default step,
`zig-recipe` adds a second built-in pass called `test`. By default, `test`
reads the exact same build configuration as `default`, so `zig build` still
runs only once. Any package, system library, or build tool reachable only from
`test` is separated automatically.

> [!NOTE]
> The name `default` is reserved for the baseline pass, so please choose other
> names for your feature variants. If a project needs extra `zig build` flags
> to build or enable tests, you can optionally define a `test` variant to
> customize the built-in `test` pass.

## Choosing your baseline

Every variant pass is measured against `default`. Before adding variants, it
helps to picture what your `default` build should include.

When an upstream author writes a `build.zig`, they pick out-of-the-box settings
that feel convenient for someone building straight from source. Depending on
the project, that might produce a minimal build with most options off, or a
full-featured build with extra tools and plugins turned on automatically.

In `zig-recipe`, the `default` pass does not have to mirror those upstream
choices. Different distributions package the same project in different ways:

- **Single fixed build:** ships one package with documentation and optional
  integrations always included.[^1]
- **Split packages:** ships a minimal base package and moves extras into
  separate subpackages.[^2]
- **Flag-driven builds:** lets users toggle individual features on and off.[^3]

Here is how each style maps to `default` and variants in `zig-recipe`:

| Packaging style | `default` pass | Variant passes |
| --- | --- | --- |
| **Single fixed build** | All shipped features | None needed |
| **Split packages** | Minimal base package | One per subpackage |
| **Flag-driven builds** | Required features only | One per optional flag |

Keep in mind that anything left enabled in `default` becomes part of every
build, so variant passes will not separate its dependencies out. A quick way to
decide is to ask: *should the smallest installation of this package include
this feature?* If the answer is yes, it fits naturally in `default`. If not,
keeping it out of `default` lets a variant pass show what it adds.

> [!TIP]
> Many packagers start by turning off optional features in `default` and
> enabling each one in its own variant pass. That keeps the baseline small and
> makes every variant's extra dependencies easy to spot.

## Checking option defaults

Once you have a rough baseline in mind, the next thing to look at is how the
project's options behave when left unset.

Running `zig-recipe inspect` lists the available build options along with their
descriptions. Those descriptions often mention the default behavior, and
opening `build.zig` to glance at the `b.option(...)` calls shows the exact
fallback values.

In practice, most boolean options fall into one of three cases.

### Off by default

In `river/build.zig`, `xwayland` is a classic opt-in option:

```zig
const xwayland = b.option(
    bool,
    "xwayland",
    "Set to true to enable xwayland support",
) orelse false;
```

Because `xwayland` falls back to `false`, an ordinary `default` pass already
builds without XWayland support. You do not need to pass anything extra to keep
it out of the baseline.

To see what XWayland adds, a single variant pass that passes `-Dxwayland=true`
is enough.

### On by default

In other projects, you will often see options that fall back to `true`:

```zig
const enable_wayland = b.option(
    bool,
    "wayland",
    "Enable Wayland support",
) orelse true;
```

When an option like this is left unset, `default` already builds with the
feature turned on:

- **If your package always ships it:** this already matches your baseline.
- **If you want it to be optional:** you will want to keep it out of `default`
  explicitly (we will look at how right below).

### Auto-detected

Some options pick their fallback by inspecting the machine where `zig build`
runs. In `river/build.zig`, `man-pages` works this way:

```zig
const man_pages = b.option(
    bool,
    "man-pages",
    "Set to true to build man pages. Requires scdoc. Defaults to true if scdoc is found.",
) orelse
    (b.findProgram(.{ .names = &.{"scdoc"} }) != null);
```

Here, `man-pages` turns itself on whenever `scdoc` happens to be installed on
your machine. On a minimal build host where `scdoc` is missing, the exact same
command leaves it off.

### Making the baseline explicit

When an option defaults to `true` or auto-detects from the host, leaving it
unset in `default` causes both `default` and the feature's variant to run the
same build:

```text
default  ──▶  core + feature   (enabled automatically)
variant  ──▶  core + feature   (same build as default)
```

Because the feature is already active in `default`, every dependency it pulls
in appears in the baseline. Comparing the variant against `default` then shows
no difference, so `zig-recipe` treats those dependencies as always required
rather than optional.

Passing an explicit `false` in `default` and `true` in the variant separates
the two builds cleanly:

```text
default  ──▶  core               (-Dman-pages=false)
man      ──▶  core + man pages   (-Dman-pages=true)
```

Now `default` stays free of the optional feature, and the `man` pass isolates
whatever man pages add on top.

For auto-detected options, being explicit has a second benefit:
reproducibility. Whether you run `zig-recipe` on your workstation, in CI, or
inside a clean chroot, the passes behave the same way regardless of what tools
happen to be installed on the host.

Here is a quick summary of how each fallback pattern maps to your passes:

| Option fallback in `build.zig` | `default` pass | Variant pass |
| --- | --- | --- |
| **Off by default** (`orelse false`) | Nothing extra needed | Pass `=true` |
| **On by default** (`orelse true`) | Pass `=false` | Pass `=true` |
| **Auto-detected** (environment check) | Pass `=false` | Pass `=true` |

> [!NOTE]
> Even for an option that is off by default, passing an explicit `=false` in
> `default` is completely fine. Some packagers prefer doing so anyway so that a
> future upstream change to the option's default will not quietly alter their
> baseline.

## Writing a pass config

Multi-pass analysis is configured with a small TOML file (for example,
`river.toml`). Keeping your passes in a file avoids shell quoting headaches and
makes it easy to re-run the exact same analysis whenever upstream tags a new
release.

### Base arguments

If some `zig build` arguments should stay the same across every pass, they go
in `base_args` under `[build]`:

```toml
[build]
base_args = ["-Dpie=true"]
```

This field is optional. It is a good home for build-wide choices such as
`-Dpie=true`, `-Dstrip=false`, or `-fsys=` system integrations.

### Variants

Each entry under `[build.variants]` defines one named pass, using one of two
shapes that match the fallback patterns from `build.zig`:

- **Simple list** for options that are already off by default:

  ```toml
  [build.variants]
  X = ["-Dxwayland=true"]
  ```

- **`{ on, off }` table** for options that default to on or auto-detect:

  ```toml
  [build.variants]
  man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
  ```

With `{ on, off }`, the `off` arguments keep the feature disabled during
`default` and during every other variant, while the `on` arguments enable it
only for `man`.

### Customizing the `test` pass

By default, `test` reuses the `default` build configuration without running
`zig build` a second time. If a project needs an extra flag just to enable or
build its test suite, you can define a `test` entry under `[build.variants]`:

```toml
[build.variants]
test = ["-Denable-tests=true"]
```

When a `test` variant is present, `zig-recipe` runs a dedicated `zig build` pass
for `test` with those arguments and traces the project's `test` step from it.

### Putting it together

Combining `[build]` and both `river` variants gives a compact `river.toml`:

```toml
[build]
base_args = ["-Dpie=true"]

[build.variants]
man = { on = ["-Dman-pages=true"], off = ["-Dman-pages=false"] }
X = ["-Dxwayland=true"]
```

You can pass the file with `--config`:

```console
$ zig-recipe inspect ./river --config river.toml
```

From `river.toml`, `zig-recipe` constructs four passes (three `zig build` runs,
plus `test` traced from `default`):

| Pass | `zig build` arguments | Steps |
| --- | --- | --- |
| `default` | `-Dpie=true -Dman-pages=false` | `install` |
| `test` | `-Dpie=true -Dman-pages=false` | `test` |
| `man` | `-Dpie=true -Dman-pages=true` | `install` |
| `X` | `-Dpie=true -Dman-pages=false -Dxwayland=true` | `install` |

First, `default` combines `base_args` with every variant's `off` arguments, and
`test` is traced from that same build configuration.

Then each named variant runs with `base_args`, every other variant's `off`
arguments, and its own `on` arguments. If a variant sets a `-D<option>` that
also appears in `base_args`, `zig-recipe` replaces the base value for that pass
rather than passing the option twice (since `zig build` rejects repeating
non-list `-D` options).

> [!TIP]
> The same TOML file can also hold package metadata and output renderer
> settings, or be piped in from stdin with `--config -`. See
> [Project Configuration](project-config.md) for the full reference.

## How items are classified

Once all passes finish, `zig-recipe` compares what each pass referenced. Every
package, system library, and build-time tool is placed into one of three
categories, alongside the list of passes that used it:

| Classification | Meaning |
| --- | --- |
| **`always`** | Used by the `default` pass (needed in every build). |
| **`conditional`** | Not used by `default`, but used by `test` or by one or more named variants. |
| **`unused`** | Not used in any analyzed pass. |

In `river 0.5.2`, for instance:

- `libevdev` is used by `default` (as well as by `X`), so it is classified as
  `always`.
- Any package, library, or build-time tool that appears only in `test` or in a
  named variant is classified as `conditional`, recording which passes needed
  it.
- `pixman-0.4.0` is declared in `build.zig.zon` but never used in any pass, so
  it is classified as `unused`.

From here, `zig-recipe gentoo` turns these passes and classifications into
`IUSE`, `src_configure()`, and conditional comments (see
[Gentoo Ebuilds](gentoo.md)), and `zig-recipe render` exposes the exact same
data to custom templates (see [Custom Jinja2 Templates](templates.md)).

[^1]: Common in rolling binary distributions such as Arch Linux or in Homebrew formulas, where a single package typically ships with standard features enabled.
[^2]: Common in Debian, Ubuntu, Fedora, and Alpine Linux, where one source build produces separate packages such as `-doc` or plugin subpackages.
[^3]: Examples include Gentoo `USE` flags, FreeBSD Ports `OPTIONS`, Void Linux `build_options`, and pkgsrc `PKG_OPTIONS`.
