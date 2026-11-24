# Custom Jinja2 Templates

`zig-recipe render <template> [project]` renders a project's collected build
data through any Jinja2 template. Your template receives the same structured
data as `zig-recipe inspect --format json`.

All examples on this page use `river 0.5.2`.

## What data is available

Your template receives the exact same data structure printed by
`zig-recipe inspect --format json`. Running it on your project is the quickest
way to see every field and value:

```console
$ zig-recipe inspect ./river --format json
```

### Project and Zig metadata

- `project` — name, version, and summary from `build.zig.zon`, plus any
  metadata from `[package]` in a `--config` file.
- `zig` — detected Zig toolchain version.

```json
{
  "project": {
    "name": "river",
    "version": "0.5.2",
    "summary": null,
    "homepage": [],
    "license": null
  },
  "zig": {
    "version": "0.17.1",
    "major_minor": "0.17",
    "is_live": false
  }
}
```

### Passes and usage

`passes` lists every pass that ran (`default`, `test`, and any named variants
from `--config`). Each entry in `dependencies.packages`,
`build.system_libraries`, and `build.find_programs` carries two fields:

- `usage`: `"always"`, `"conditional"`, or `"unused"`.
- `used_in`: list of pass names that referenced it (such as
  `["default", "test"]` or `["X"]`).

```json
{
  "hash": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX",
  "name": "pixman",
  "url": "https://codeberg.org/ifreund/zig-pixman/archive/v0.3.0.tar.gz",
  "kind": "tarball",
  "origin": "native_tarball",
  "filename": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX.tar.gz",
  "usage": "always",
  "used_in": ["default", "test"]
}
```

### Build graph

`build` holds everything extracted from Zig's build configuration:

- `has_c_sources` — whether C or C++ sources were found,
- `user_options` — declared `-D` build options,
- `system_integrations` — `-fsys` integrations and their default state,
- `system_libraries` — linked system libraries and which artifacts use them,
- `top_level_steps` — top-level build steps (such as `install` and `test`),
- `find_programs` — external tools requested via `b.findProgramLazy`.

<details>
<summary>Full JSON example for <code>river 0.5.2</code> (trimmed to two packages and two libraries)</summary>

```json
{
  "project": {
    "name": "river",
    "version": "0.5.2",
    "summary": null,
    "homepage": [],
    "license": null
  },
  "zig": {
    "version": "0.17.1",
    "major_minor": "0.17",
    "is_live": false
  },
  "passes": [
    {
      "name": "default",
      "args": [],
      "steps": ["install"]
    },
    {
      "name": "test",
      "args": [],
      "steps": ["test"]
    }
  ],
  "dependencies": {
    "packages": [
      {
        "hash": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX",
        "name": "pixman",
        "url": "https://codeberg.org/ifreund/zig-pixman/archive/v0.3.0.tar.gz",
        "kind": "tarball",
        "origin": "native_tarball",
        "filename": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX.tar.gz",
        "usage": "always",
        "used_in": ["default", "test"]
      },
      {
        "hash": "pixman-0.4.0-LClMn_eWAAAa2zACATDpPwjuI5fWlO5cBp_F_M33hnNZ",
        "name": "pixman",
        "url": "https://codeberg.org/ifreund/zig-pixman/archive/v0.4.0.tar.gz",
        "kind": "tarball",
        "origin": "native_tarball",
        "filename": "pixman-0.4.0-LClMn_eWAAAa2zACATDpPwjuI5fWlO5cBp_F_M33hnNZ.tar.gz",
        "usage": "unused",
        "used_in": []
      }
    ],
    "tarball_tarball": null
  },
  "build": {
    "has_c_sources": true,
    "user_options": [
      {
        "name": "strip",
        "description": "Omit debug information",
        "type": "bool",
        "values": null
      },
      {
        "name": "version-string",
        "description": "Override `river -version` output.",
        "type": "string",
        "values": null
      }
    ],
    "system_integrations": [],
    "system_libraries": [
      {
        "name": "libevdev",
        "used_by": [
          { "kind": "artifact", "name": "river" },
          { "kind": "module", "name": "c" },
          { "kind": "translate_c", "name": "translate-c" }
        ],
        "use_pkg_config": "yes",
        "usage": "always",
        "used_in": ["default", "test"]
      },
      {
        "name": "wayland-server",
        "used_by": [
          { "kind": "artifact", "name": "river" }
        ],
        "use_pkg_config": "yes",
        "usage": "always",
        "used_in": ["default", "test"]
      }
    ],
    "top_level_steps": [
      {
        "name": "install",
        "description": "Copy build artifacts to prefix path",
        "is_default": true
      },
      {
        "name": "test",
        "description": "Run the tests",
        "is_default": false
      }
    ],
    "find_programs": []
  }
}
```

</details>

## Example: Markdown project report (`report.md.j2`)

Here is a small Jinja2 template that turns the collected data into a readable
Markdown summary:

```jinja2
# {{ project.name }} {{ project.version }}

Built with Zig {{ zig.version }}.

## Dependencies

{% for pkg in dependencies.packages if pkg.usage != 'unused' -%}
- **{{ pkg.name or pkg.hash }}** — {{ pkg.url }}
{% endfor -%}
{% set unused = dependencies.packages | selectattr('usage', 'equalto', 'unused') | list -%}
{% if unused %}
### Unused in this configuration

{% for pkg in unused -%}
- {{ pkg.name or pkg.hash }} — {{ pkg.url }}
{% endfor -%}
{% endif %}
{% if build.system_libraries %}
## System libraries

{% for lib in build.system_libraries -%}
- `{{ lib.name }}` (used by: {% for u in lib.used_by %}{{ u.kind }}:{{ u.name }}{% if not loop.last %}, {% endif %}{% endfor %})
{% endfor -%}
{% endif %}
{% if build.user_options %}
## Build options

{% for opt in build.user_options -%}
- `-D{{ opt.name }}=[{{ opt.type }}{% if opt.values %}: {{ opt.values | join(' | ') }}{% endif %}]` — {{ opt.description }}
{% endfor -%}
{% endif %}
```

Run it on `river 0.5.2`:

```console
$ zig-recipe render ./report.md.j2 ./river -o REPORT.md
```

Output (`REPORT.md`):

```markdown
# river 0.5.2

Built with Zig 0.17.1.

## Dependencies

- **pixman** — https://codeberg.org/ifreund/zig-pixman/archive/v0.3.0.tar.gz
- **wayland** — https://codeberg.org/ifreund/zig-wayland/archive/v0.7.0.tar.gz
- **wlroots** — https://codeberg.org/ifreund/zig-wlroots/archive/v0.20.3.tar.gz
- **xkbcommon** — https://codeberg.org/ifreund/zig-xkbcommon/archive/v0.5.0.tar.gz

### Unused in this configuration

- pixman — https://codeberg.org/ifreund/zig-pixman/archive/v0.4.0.tar.gz

## System libraries

- `libevdev` (used by: artifact:river, module:c, translate_c:translate-c)
- `libinput` (used by: artifact:river, module:c, translate_c:translate-c)
- `pixman-1` (used by: artifact:river)
- `wayland-server` (used by: artifact:river)
- `wlroots-0.20` (used by: artifact:river, module:c, translate_c:translate-c)
- `xkbcommon` (used by: artifact:river)

## Build options

- `-Dstrip=[bool]` — Omit debug information
- `-Dpie=[bool]` — Build a Position Independent Executable
- `-Dllvm=[bool]` — Force use of Zig's LLVM backend and the lld linker
- `-Dman-pages=[bool]` — Set to true to build man pages. Requires scdoc. Defaults to true if scdoc is found.
- `-Dxwayland=[bool]` — Set to true to enable xwayland support
- `-Dversion-string=[string]` — Override `river -version` output.
```

## Working with multi-pass data

If you run `zig-recipe render` with `--config project.toml` (see
[Project Configuration](project-config.md)), you can separate conditional
dependencies or libraries using Jinja2's `selectattr` filter:

```jinja2
{% for lib in build.system_libraries | selectattr('usage', 'equalto', 'conditional') -%}
- `{{ lib.name }}` (only in: {{ lib.used_in | join(', ') }})
{% endfor -%}
```

> [!TIP]
> If you only want to tweak the built-in Gentoo ebuild layout rather than
> writing a template from scratch, you can pass a custom template directly to
> `zig-recipe gentoo --template ./my-ebuild.j2`.
