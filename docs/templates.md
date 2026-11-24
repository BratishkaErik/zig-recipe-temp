# Custom Jinja2 Templates

`zig-recipe render <template> [project]` lets you turn a project's collected
data into any text format using a Jinja2 template.

All examples on this page use `river 0.5.2`.

## What data is available

Your template receives the exact same data structure printed by
`zig-recipe inspect --format json`. Run it on your project to see every field:

```console
$ zig-recipe inspect ./river --format json
```

Here is the real output for `river 0.5.2` (trimmed to two packages and two
system libraries for readability):

```json
{
  "project": {
    "name": "river",
    "version": "0.5.2",
    "summary": null
  },
  "zig": {
    "version": "0.17.1",
    "major_minor": "0.17",
    "is_live": false
  },
  "dependencies": {
    "packages": [
      {
        "hash": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX",
        "name": "pixman",
        "url": "https://codeberg.org/ifreund/zig-pixman/archive/v0.3.0.tar.gz",
        "kind": "tarball",
        "origin": "native_tarball",
        "filename": "pixman-0.3.0-LClMnz2VAAAs7QSCGwLimV5VUYx0JFnX5xWU6HwtMuDX.tar.gz",
        "used": true
      },
      {
        "hash": "pixman-0.4.0-LClMn_eWAAAa2zACATDpPwjuI5fWlO5cBp_F_M33hnNZ",
        "name": "pixman",
        "url": "https://codeberg.org/ifreund/zig-pixman/archive/v0.4.0.tar.gz",
        "kind": "tarball",
        "origin": "native_tarball",
        "filename": "pixman-0.4.0-LClMn_eWAAAa2zACATDpPwjuI5fWlO5cBp_F_M33hnNZ.tar.gz",
        "used": false
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
        "use_pkg_config": "yes"
      },
      {
        "name": "wayland-server",
        "used_by": [
          { "kind": "artifact", "name": "river" }
        ],
        "use_pkg_config": "yes"
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

## Example: Markdown project report (`report.md.j2`)

```jinja2
# {{ project.name }} {{ project.version }}

Built with Zig {{ zig.version }}.

## Dependencies

{% for pkg in dependencies.packages if pkg.used -%}
- **{{ pkg.name or pkg.hash }}** — {{ pkg.url }}
{% endfor -%}
{% set unused = dependencies.packages | rejectattr('used') | list -%}
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
