# How `zig-recipe` Works

When you point `zig-recipe` at a Zig project, it runs through three stages:

1. Fetch the project's dependencies into a local cache.
2. Walk the dependency tree to collect and normalize download URLs.
3. Inspect the build configuration to see what the build actually uses.

This page explains the choices `zig-recipe` makes at each stage.

## 1. Fetching and caching

Before `zig-recipe` can inspect a project or its transitive dependencies, those
packages need to exist on disk. By default, `zig-recipe` asks Zig to fetch the
entire dependency tree (including lazy dependencies) into its own cache
directory.

All working files (downloaded packages, temporary Zig build caches, and helper
binaries) are stored in your OS user cache directory. That keeps the project's
source tree clean and works even if the project directory is read-only.

You can change where the cache lives with `--cache-dir` or the
`ZIG_RECIPE_CACHE_DIR` environment variable. If the packages are already cached
and you want to run without network access, pass `--no-fetch`.

## 2. Resolving dependency URLs

Once all packages are in the cache, `zig-recipe` walks every `build.zig.zon`
starting from the root project. It traverses the dependency tree in
breadth-first order, visiting direct dependencies before deeper transitive ones.

In `build.zig.zon`, a remote dependency starts out as either a **direct archive
URL** (such as `.tar.gz`, `.tar.xz`, `.tar.zst`, or `.zip`) or a **Git commit
URL** (`git+https://...#<commit>`).

### Converting Git URLs

System package managers generally expect static archive URLs rather than
`git+https://` references. When `zig-recipe` sees a Git commit URL, it tries to
convert it into a tarball download URL (a **converted Git URL**). Automatic
conversion is supported for:

- GitHub (`github.com`)
- Codeberg (`codeberg.org`)
- GitLab (`gitlab.com`)
- SourceHut (`git.sr.ht`)

Sometimes a dependency comes from a self-hosted or unrecognized Git server whose
archive URL format cannot be guessed. Those remain **raw Git commit URLs**. In
that case, `zig-recipe` packs each of those cached packages into a
`<hash>.tar.gz` archive, and then combines them into a single reproducible
`<name>-<version>-git_dependencies.tar.gz` bundle in the cache directory. The
generated output notes the path to this bundle so you can host it yourself.

### Picking one URL when a package appears twice

In larger projects, the same package hash is often declared in more than one
`build.zig.zon`. For example, both the root project and a dependency might
reference the same library from different URLs.

#### Prefer more stable URL types

First, `zig-recipe` compares URL categories in this order:

    direct archive URL  >  converted Git URL  >  raw Git commit URL

Release archives are generally more stable than archives generated from a Git
commit.

#### Prefer the closer declaration on a tie

When two URLs belong to the same category, `zig-recipe` keeps the one declared
closer to the root project. Closer manifests are usually maintained more
recently by the project or its direct upstreams. Direct declarations beat
deeper transitive ones, keeping the choice predictable without favoring any
specific hosting forge.

## 3. Inspecting the build configuration

With all dependencies in place, `zig-recipe` runs Zig's build configuration
stage offline against the local cache. From the resulting build graph, it
collects:

- user build options (`-D`),
- system integrations (`-fsys`),
- linked system libraries,
- external build-time programs,
- whether C/C++ sources are present.

### Walking from the default build step

A project's `build.zig` often defines extra steps beyond the main build, such as
release matrices that cross-compile for other operating systems. Inspecting
every step in the file would pull in unrelated platform libraries (like Windows
libraries on a Linux build).

To avoid that noise, `zig-recipe` traces only what is reachable from the
project's default build step (usually `install`) for the current build
arguments. All top-level steps are still listed in `zig-recipe inspect` so you
can see what the project defines.

### Used vs. unused dependencies

Not every remote dependency in `build.zig.zon` is needed in every build. Some
are lazy dependencies for other platforms or optional features. Others are
vendored C libraries that get skipped when a system integration (`-fsys`) is
active.

During inspection, `zig-recipe` records whether each dependency was actually
referenced by the reachable build graph. That flag is passed on to the
renderer, which can expose it directly (as in JSON) or separate unused
dependencies from active ones (as in Gentoo ebuilds).

### Known limitations

There are a few things Zig's serialized build configuration does not expose:

- **Build-time-only dependencies.** If `build.zig` imports a package purely for
  configure-time logic and that package leaves no step, module, or file path in
  the final build graph, it will appear in the unused section even though
  `build.zig` needed it during configuration.
- **External programs.** Only programs requested via `b.findProgramLazy` appear
  in the serialized build graph. Direct `b.findProgram` calls run immediately
  while `build.zig` executes and are not recorded.
- **Default option values.** Zig build scripts compute option defaults in user
  code (`orelse ...`), so only each option's name, type, allowed enum values,
  and description are available.
