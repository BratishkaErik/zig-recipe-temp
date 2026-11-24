# How `zig-recipe` Works

When you point `zig-recipe` at a Zig project, it runs through three stages:

```text
1. Fetch dependencies   ──▶   2. Resolve URLs   ──▶   3. Inspect build graph
   (into local cache)         (walk manifests)        (trace passes offline)
```

## 1. Fetching and caching

Before `zig-recipe` can inspect a project or its transitive dependencies, those
packages need to be available on disk. By default, `zig-recipe` asks Zig to
fetch the entire dependency tree (including lazy dependencies) into its own
cache directory.

All working files (downloaded packages, temporary Zig build caches, and helper
binaries) live in your OS user cache directory. That keeps the project's source
tree untouched and works even when the project directory is read-only.

| Option | Effect |
| --- | --- |
| `--cache-dir <path>` | Use a custom cache directory for this run. |
| `ZIG_RECIPE_CACHE_DIR` | Environment variable equivalent of `--cache-dir`. |
| `[general].cache_dir` | Persistent default in your [user config](user-config.md). |
| `--no-fetch` | Skip network fetches and rely only on packages already in the cache. |

## 2. Resolving dependency URLs

Once the packages are in the cache, `zig-recipe` walks every `build.zig.zon`
starting from the root project. It traverses the dependency tree in
breadth-first order, visiting direct dependencies before deeper transitive
ones.

In `build.zig.zon`, a remote dependency starts out in one of two forms:

- a **direct archive URL** (such as `.tar.gz`, `.tar.xz`, `.tar.zst`, or `.zip`),
- a **Git commit URL** (`git+https://...#<commit>`).

### Converting Git URLs

System package managers generally expect static archive URLs rather than
`git+https://` references. When `zig-recipe` encounters a Git commit URL, it
tries to convert it into a tarball download URL (a **converted Git URL**).

Automatic conversion is supported for:

- GitHub (`github.com`)
- Codeberg (`codeberg.org`)
- GitLab (`gitlab.com`)
- SourceHut (`git.sr.ht`)

### Handling unrecognized Git hosts

Sometimes a dependency lives on a self-hosted or unrecognized Git server whose
archive URL pattern cannot be guessed. Those remain **raw Git commit URLs**.

In that case, `zig-recipe` packs each of those cached packages into a
`<hash>.tar.gz` archive, then combines them into a single reproducible
`<name>-<version>-git_dependencies.tar.gz` bundle inside the cache directory
(or `--tarball-dir` if specified). The generated recipe notes the path to this
bundle so you can mirror it yourself.

### Picking one URL when a package appears twice

In larger projects, the same package hash is often declared in more than one
`build.zig.zon`. For example, both the root project and one of its dependencies
might point to the same library through different URLs.

`zig-recipe` resolves duplicates in two steps:

1. **Prefer more stable URL categories.** Release archives tend to be more
   stable than on-the-fly archives generated from a Git commit, so categories
   are ranked as:

   | Priority | Category | Example |
   | --- | --- | --- |
   | 1 *(highest)* | Direct archive URL | `https://.../archive/v0.3.0.tar.gz` |
   | 2 | Converted Git URL | Git commit URL rewritten to a forge tarball |
   | 3 *(lowest)* | Raw Git commit URL | Self-hosted Git server bundled locally |

2. **Prefer the closer declaration on a tie.** When two URLs fall into the same
   category, `zig-recipe` keeps the one declared closer to the root project.
   Closer manifests are usually updated more recently, and direct declarations
   beat deeper transitive ones without favoring any particular forge.

## 3. Inspecting the build configuration

Reading `build.zig.zon` alone only tells you which dependencies a project
*declares*. To see what a build actually uses, `zig-recipe` runs Zig's build
configuration stage offline against the local cache and collects:

- user build options (`-D`),
- system integrations (`-fsys`),
- linked system libraries,
- external build-time programs,
- whether C/C++ sources are present.

### Walking from the default and `test` steps

A project's `build.zig` often defines extra steps beyond the main build, such
as release matrices that cross-compile for other operating systems. Inspecting
every step in the file would pull in unrelated platform libraries (like Windows
libraries on a Linux build).

To avoid that noise, `zig-recipe` traces only what is reachable from the
project's default build step (usually `install`) for the active build
arguments. If the project also defines a separate `test` step, `zig-recipe`
traces it as a second pass from the exact same build configuration. All
top-level steps are still listed in `zig-recipe inspect` so you can see
everything the project defines.

### Multi-pass variants and usage classification

When you supply a `--config` file with `[build.variants]`, `zig-recipe` runs
the baseline pass (`default`) followed by one pass per variant. Every
dependency, system library, and build-time program is then classified as
`always`, `conditional`, or `unused` based on which passes referenced it.

See [Multi-Pass Analysis](multi-pass.md) for how passes are planned and
[Project Configuration](project-config.md) for the full TOML format.

### Known limitations

There are a few things Zig's serialized build configuration does not expose.

#### Build-time-only dependencies

If `build.zig` imports a package purely for configure-time logic, and that
package leaves no step, module, or file path in the final build graph,
`zig-recipe` cannot see a reference to it in the graph. It will appear in the
`unused` group even though `build.zig` needed it during configuration. In
Gentoo ebuilds, `--unused-deps comment` keeps such entries visible right in
`ZBS_DEPENDENCIES` so you can uncomment them easily.

#### External programs

Only programs requested through `b.findProgramLazy` are recorded in the
serialized build graph. Direct `b.findProgram` calls run immediately while
`build.zig` executes and leave no entry behind, so tools discovered that way
(such as `scdoc` in `river 0.5.2`) still need to be checked in `build.zig` by
hand.

#### Default option values

Zig build scripts compute option fallbacks in ordinary Zig code (`orelse ...`),
so only each option's name, type, allowed enum values, and description appear
in the serialized graph. To see what an option falls back to when left unset,
glance at the `b.option(...)` calls in `build.zig`.
