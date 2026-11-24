"""Zig process wrapper and std.Build.Configuration inspector."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

from semver import Version

from zig_recipe.cache import CacheDirs
from zig_recipe.errors import ZigProcessError, ZigVersionError

logger = logging.getLogger(__name__)

MIN_ZIG_VERSION = Version(0, 17, 0)

STANDARD_TARGET_OPTIONS = frozenset({"target", "cpu", "ofmt", "dynamic-linker"})
STANDARD_OPTIMIZE_OPTIONS = frozenset({"optimize", "release"})
ALL_STANDARD_OPTIONS = STANDARD_TARGET_OPTIONS | STANDARD_OPTIMIZE_OPTIONS


@dataclass(frozen=True, slots=True)
class ZigVersion:
    """Parsed Zig compiler version."""

    raw: str
    semver: Version

    @classmethod
    def parse(cls, raw: str) -> ZigVersion:
        text = raw.strip()
        try:
            ver = Version.parse(text)
        except ValueError as exc:
            raise ZigVersionError(f"Could not parse Zig version {text!r}: {exc}") from exc

        base = Version(ver.major, ver.minor, ver.patch)
        if base < MIN_ZIG_VERSION:
            raise ZigVersionError(
                f"Zig {text} is not supported by zig-recipe (requires Zig >= {MIN_ZIG_VERSION})."
            )
        return cls(raw=text, semver=ver)

    @property
    def is_live(self) -> bool:
        return self.semver.prerelease is not None

    @property
    def major_minor(self) -> str:
        return f"{self.semver.major}.{self.semver.minor}"


@dataclass(frozen=True, slots=True)
class UserOption:
    """A build.zig option declared via b.option(...)."""

    name: str
    description: str
    type_id: str
    values: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SystemIntegration:
    """A build.zig system integration declared via b.systemIntegrationOption(...)."""

    name: str
    enabled: bool


class LibraryUserKind(StrEnum):
    """Kind of build graph node that links a system library."""

    ARTIFACT = "artifact"
    MODULE = "module"
    TRANSLATE_C = "translate_c"


@dataclass(frozen=True, slots=True)
class LibraryUser:
    """An artifact, module, or translate-c step that links a system library."""

    kind: LibraryUserKind
    name: str


@dataclass(frozen=True, slots=True)
class SystemLibrary:
    """A deduplicated system library discovered across reachable modules/steps."""

    name: str
    used_by: tuple[LibraryUser, ...]
    use_pkg_config: str  # "yes", "force", "no"

    @property
    def pkg_config_hint(self) -> str | None:
        if self.use_pkg_config == "force":
            return "pkg-config required"
        if self.use_pkg_config == "no":
            return "no pkg-config"
        return None


@dataclass(frozen=True, slots=True)
class TopLevelStep:
    """A root top-level build step."""

    name: str
    description: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class UsedPackage:
    """A package referenced in the serialized build graph."""

    hash: str
    dep_prefix: str
    root_path: str


@dataclass(frozen=True, slots=True)
class BuildInspection:
    """Structured result from inspecting a Zig 0.17+ std.Build.Configuration."""

    user_options: tuple[UserOption, ...]
    system_integrations: tuple[SystemIntegration, ...]
    system_libraries: tuple[SystemLibrary, ...]
    top_level_steps: tuple[TopLevelStep, ...]
    used_packages: tuple[UsedPackage, ...]
    find_programs: tuple[tuple[str, ...], ...]
    has_c_sources: bool

    @property
    def used_package_hashes(self) -> frozenset[str]:
        return frozenset(p.hash for p in self.used_packages)


class ZigInspector:
    """Drives `zig fetch`, `zig build --print-configuration-path`, and `dump_config`."""

    def __init__(self, zig_exe: str | Path, cache: CacheDirs) -> None:
        self.zig_exe = str(zig_exe)
        self.cache = cache
        self.version = self._detect_version()

    def _detect_version(self) -> ZigVersion:
        proc = self._run([self.zig_exe, "version"])
        return ZigVersion.parse(proc.stdout)

    def fetch_all(self, *, project_dir: Path, build_zig: Path) -> None:
        """Fetch all transitive (including lazy) dependencies into `cache.packages_dir`."""
        cmd = [
            self.zig_exe,
            "build",
            "--build-file",
            str(build_zig),
            "--cache-dir",
            str(self.cache.zig_cache_dir),
            "--pkg-dir",
            str(self.cache.packages_dir),
            "--fetch=all",
        ]
        logger.info("Fetching all dependencies into %s...", self.cache.packages_dir)
        self._run(cmd, cwd=project_dir)

    def inspect(
        self,
        *,
        project_dir: Path,
        build_zig: Path,
        extra_args: tuple[str, ...] = (),
    ) -> BuildInspection:
        """Run `zig build --print-configuration-path` and decode the binary Configuration."""
        cmd = [
            self.zig_exe,
            "build",
            "--build-file",
            str(build_zig),
            "--cache-dir",
            str(self.cache.zig_cache_dir),
            "--system",
            str(self.cache.packages_dir),
            "--print-configuration-path",
            *extra_args,
        ]
        logger.debug("Running: %s (cwd=%s)", cmd, project_dir)
        proc = self._run(cmd, cwd=project_dir)

        raw_path = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
        if not raw_path:
            raise ZigProcessError(
                "`zig build --print-configuration-path` did not output a configuration path"
            )

        cfg_path = Path(raw_path)
        if not cfg_path.is_absolute():
            cfg_path = (project_dir / cfg_path).resolve()
        if not cfg_path.is_file():
            raise ZigProcessError(f"Configuration file does not exist: {cfg_path}")

        helper_exe = self._ensure_dump_helper()
        dump_proc = self._run([str(helper_exe), str(cfg_path)])
        try:
            payload = json.loads(dump_proc.stdout)
        except json.JSONDecodeError as exc:
            raise ZigProcessError(f"Failed to parse dump_config JSON output: {exc}") from exc

        return parse_inspection_payload(payload)

    def _ensure_dump_helper(self) -> Path:
        """Compile dump_config.zig once per (zig version, source hash) into cache.tools_dir."""
        src_ref = resources.files("zig_recipe.zig").joinpath("dump_config.zig")
        src_bytes = src_ref.read_bytes()
        src_hash = hashlib.sha256(src_bytes).hexdigest()[:12]
        safe_ver = re.sub(r"[^0-9A-Za-z._-]", "_", self.version.raw)
        exe_ext = ".exe" if sys.platform == "win32" else ""
        bin_path = self.cache.tools_dir / f"dump_config-{safe_ver}-{src_hash}{exe_ext}"

        if bin_path.is_file():
            return bin_path

        src_copy = self.cache.tools_dir / f"dump_config-{src_hash}.zig"
        if not src_copy.is_file():
            src_copy.write_bytes(src_bytes)

        logger.info("Compiling dump_config helper for Zig %s...", self.version.raw)
        self._run(
            [
                self.zig_exe,
                "build-exe",
                str(src_copy),
                f"-femit-bin={bin_path}",
            ],
            cwd=self.cache.tools_dir,
        )
        if not bin_path.is_file():
            raise ZigProcessError(f"Failed to produce helper binary at {bin_path}")
        return bin_path

    def _run(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise ZigProcessError(f"Executable not found: {cmd[0]}") from exc
        except OSError as exc:
            raise ZigProcessError(f"Failed to run {cmd[0]}: {exc}") from exc

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            stdout = proc.stdout.strip()
            detail = stderr or stdout or f"exit code {proc.returncode}"
            raise ZigProcessError(f"Command failed ({' '.join(cmd)}):\n{detail}")
        return proc


def parse_inspection_payload(payload: dict[str, Any]) -> BuildInspection:
    """Convert raw JSON from dump_config.zig into a validated, sorted BuildInspection."""
    raw_options = payload.get("user_options", [])
    seen_option_names: set[str] = set()
    user_options: list[UserOption] = []
    for item in raw_options:
        name = str(item["name"])
        seen_option_names.add(name)
        if name in ALL_STANDARD_OPTIONS:
            continue
        vals_raw = item.get("values")
        values = tuple(str(v) for v in vals_raw) if vals_raw is not None else None
        user_options.append(
            UserOption(
                name=name,
                description=str(item.get("description", "")),
                type_id=str(item["type"]),
                values=values,
            )
        )

    _warn_missing_standard_options(seen_option_names)

    # Sort enabled integrations first, then alphabetically.
    raw_integrations = payload.get("system_integrations", [])
    integrations = sorted(
        (
            SystemIntegration(name=str(si["name"]), enabled=bool(si["enabled"]))
            for si in raw_integrations
        ),
        key=lambda si: (not si.enabled, si.name),
    )

    # Deduplicate system libraries by name, aggregating typed users and pkg-config mode.
    lib_users: dict[str, set[LibraryUser]] = defaultdict(set)
    lib_pkgcfg: dict[str, str] = {}
    pkgcfg_rank = {"no": 0, "yes": 1, "force": 2}
    for sl in payload.get("system_libraries", []):
        name = str(sl["name"])
        used_by = sl["used_by"]
        lib_users[name].add(
            LibraryUser(kind=LibraryUserKind(used_by["kind"]), name=str(used_by["name"]))
        )
        mode = str(sl.get("use_pkg_config", "yes"))
        prev = lib_pkgcfg.get(name)
        if prev is None or pkgcfg_rank.get(mode, 1) > pkgcfg_rank.get(prev, 1):
            lib_pkgcfg[name] = mode

    system_libraries = tuple(
        SystemLibrary(
            name=name,
            used_by=tuple(sorted(lib_users[name], key=lambda u: (u.kind, u.name))),
            use_pkg_config=lib_pkgcfg[name],
        )
        for name in sorted(lib_users)
    )

    top_level_steps = tuple(
        TopLevelStep(
            name=str(s["name"]),
            description=str(s.get("description", "")),
            is_default=bool(s.get("is_default", False)),
        )
        for s in payload.get("top_level_steps", [])
    )

    used_packages = tuple(
        UsedPackage(
            hash=str(p["hash"]),
            dep_prefix=str(p.get("dep_prefix", "")),
            root_path=str(p.get("root_path", "")),
        )
        for p in payload.get("used_packages", [])
    )

    # Deduplicate find_programs entries while preserving order.
    seen_progs: set[tuple[str, ...]] = set()
    find_programs: list[tuple[str, ...]] = []
    for prog_list in payload.get("find_programs", []):
        tup = tuple(str(n) for n in prog_list)
        if tup and tup not in seen_progs:
            seen_progs.add(tup)
            find_programs.append(tup)

    return BuildInspection(
        user_options=tuple(user_options),
        system_integrations=tuple(integrations),
        system_libraries=system_libraries,
        top_level_steps=top_level_steps,
        used_packages=used_packages,
        find_programs=tuple(find_programs),
        has_c_sources=bool(payload.get("has_c_sources", False)),
    )


def _warn_missing_standard_options(option_names: set[str]) -> None:
    missing_target = {"target", "cpu"} - option_names
    if missing_target:
        logger.warning(
            "Project build.zig is missing standard target option(s): %s "
            "(usually added via b.standardTargetOptions)",
            ", ".join(sorted(missing_target)),
        )
    if "optimize" not in option_names and "release" not in option_names:
        logger.warning(
            "Project build.zig is missing standard 'optimize' option "
            "(usually added via b.standardOptimizeOption)"
        )
    elif "release" in option_names and "optimize" not in option_names:
        logger.warning("Project build.zig exposes 'release' instead of standard 'optimize' option")
