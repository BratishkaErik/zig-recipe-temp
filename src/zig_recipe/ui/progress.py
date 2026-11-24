"""Rich progress UI with native Zig Progress Protocol (ZIG_PROGRESS) IPC support."""

from __future__ import annotations

import contextlib
import os
import struct
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

if TYPE_CHECKING:
    from types import TracebackType

    from rich.console import Console

_MAX_NODES = 253
_MAX_NAME_LEN = 120
_NODE_STORAGE_STRUCT = struct.Struct(f"<II{_MAX_NAME_LEN}s")
_NODE_STORAGE_SIZE = _NODE_STORAGE_STRUCT.size  # 128 bytes (Zig 0.17+)
_NODE_PARENT_SIZE = 1
_BYTES_PER_NODE = _NODE_STORAGE_SIZE + _NODE_PARENT_SIZE  # 129 bytes


@dataclass(frozen=True, slots=True)
class ZigProgressNode:
    """A single node from a Zig Progress Protocol snapshot."""

    name: str
    completed: int
    estimated_total: int
    parent: int


def parse_progress_packets(buf: bytes) -> tuple[tuple[ZigProgressNode, ...] | None, bytes]:
    """Parse complete Zig Progress Protocol packets from `buf`.

    Packet layout (Zig 0.17+ std.Progress IPC):
    - `nodes_len` (`u8`, 0..253)
    - `nodes_len` * 128-byte `Node.Storage`: `<II120s` (`completed`, `estimated_total`, `name`)
    - `nodes_len` * 1-byte `Node.Parent` (`u8`, 0xff for root)

    Returns `(latest_snapshot, remaining_unparsed_bytes)`.
    """
    latest: tuple[ZigProgressNode, ...] | None = None
    offset = 0
    total_len = len(buf)

    while offset < total_len:
        nodes_len = buf[offset]
        if nodes_len > _MAX_NODES:
            # Reserved/invalid byte; discard buffer to resync cleanly.
            return latest, b""
        packet_len = 1 + nodes_len * _BYTES_PER_NODE
        if offset + packet_len > total_len:
            break

        storage_start = offset + 1
        parents_start = storage_start + nodes_len * _NODE_STORAGE_SIZE
        nodes: list[ZigProgressNode] = []
        for idx in range(nodes_len):
            completed, estimated_total, raw_name = _NODE_STORAGE_STRUCT.unpack_from(
                buf, storage_start + idx * _NODE_STORAGE_SIZE
            )
            name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
            parent = buf[parents_start + idx]
            nodes.append(
                ZigProgressNode(
                    name=name,
                    completed=completed,
                    estimated_total=estimated_total,
                    parent=parent,
                )
            )
        latest = tuple(nodes)
        offset += packet_len

    return latest, buf[offset:]


def pick_active_zig_node(nodes: tuple[ZigProgressNode, ...]) -> ZigProgressNode | None:
    """Pick the most informative active node (deepest named node, preferring one with a total)."""
    named = [n for n in nodes if n.name]
    if not named:
        return None
    for node in reversed(named):
        if node.estimated_total > 0:
            return node
    return named[-1]


class ProgressReporter:
    """Rich progress bar with native `ZIG_PROGRESS` IPC on POSIX and cache-count fallback."""

    def __init__(self, console: Console, *, enabled: bool | None = None) -> None:
        self.enabled = console.is_terminal if enabled is None else enabled
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
            disable=not self.enabled,
        )
        self._stage_task: TaskID | None = None
        self._zig_task: TaskID | None = None

    def __enter__(self) -> Self:
        if self.enabled:
            self._progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.enabled:
            self._progress.__exit__(exc_type, exc, tb)

    def set_stage(self, description: str, *, total: int | None = None) -> None:
        """Set or update the top-level stage description and optional total count."""
        if not self.enabled:
            return
        if self._stage_task is None:
            self._stage_task = self._progress.add_task(description, total=total)
        else:
            self._progress.update(self._stage_task, description=description, total=total)

    def advance_stage(self, description: str | None = None, *, advance: int = 1) -> None:
        """Advance the top-level stage progress bar."""
        if not self.enabled or self._stage_task is None:
            return
        if description is not None:
            self._progress.update(self._stage_task, description=description)
        self._progress.advance(self._stage_task, advance)

    def update_zig_nodes(self, nodes: tuple[ZigProgressNode, ...]) -> None:
        """Reflect the latest Zig Progress Protocol snapshot in the child Rich task."""
        if not self.enabled:
            return
        active = pick_active_zig_node(nodes)
        if active is None:
            self._hide_zig_task()
            return
        total = active.estimated_total if active.estimated_total > 0 else None
        completed = min(active.completed, total) if total is not None else active.completed
        desc = f"  └─ zig: {active.name}"
        if self._zig_task is None:
            self._zig_task = self._progress.add_task(desc, total=total, completed=completed)
        else:
            self._progress.update(
                self._zig_task,
                description=desc,
                total=total,
                completed=completed,
                visible=True,
            )

    def _update_zig_substatus(self, text: str) -> None:
        if not self.enabled:
            return
        desc = f"  └─ {text}"
        if self._zig_task is None:
            self._zig_task = self._progress.add_task(desc, total=None)
        else:
            self._progress.update(self._zig_task, description=desc, total=None, visible=True)

    def _hide_zig_task(self) -> None:
        if self._zig_task is not None:
            self._progress.update(self._zig_task, visible=False)

    def run_zig(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        watch_dir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a Zig subprocess, streaming `ZIG_PROGRESS` on POSIX or watching `watch_dir`."""
        if not self.enabled:
            return subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        if os.name == "posix":
            return self._run_posix_with_ipc(cmd, cwd=cwd, env=env)
        return self._run_with_dir_watch(cmd, cwd=cwd, env=env, watch_dir=watch_dir)

    def _run_posix_with_ipc(
        self,
        cmd: list[str],
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        r_fd, w_fd = os.pipe()
        child_env = dict(os.environ if env is None else env)
        child_env["ZIG_PROGRESS"] = str(w_fd)
        reader_thread: threading.Thread | None = None

        try:
            os.set_blocking(w_fd, False)
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                pass_fds=(w_fd,),
            )
        except Exception:
            os.close(r_fd)
            os.close(w_fd)
            raise

        # Parent closes write end immediately so EOF arrives when child exits.
        os.close(w_fd)

        def _drain_progress() -> None:
            buf = b""
            try:
                while True:
                    chunk = os.read(r_fd, 4096)
                    if not chunk:
                        break
                    buf += chunk
                    latest, buf = parse_progress_packets(buf)
                    if latest is not None:
                        self.update_zig_nodes(latest)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    os.close(r_fd)

        reader_thread = threading.Thread(target=_drain_progress, daemon=True)
        reader_thread.start()
        try:
            stdout, stderr = proc.communicate()
        finally:
            reader_thread.join(timeout=1.0)
            self._hide_zig_task()

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _run_with_dir_watch(
        self,
        cmd: list[str],
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
        watch_dir: Path | None,
    ) -> subprocess.CompletedProcess[str]:
        stop_event = threading.Event()
        watcher: threading.Thread | None = None
        if watch_dir is not None:

            def _watch() -> None:
                while not stop_event.wait(0.2):
                    try:
                        count = sum(1 for _ in watch_dir.iterdir())
                    except OSError:
                        continue
                    if count > 0:
                        self._update_zig_substatus(f"cached packages: {count}")

            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()

        try:
            return subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        finally:
            stop_event.set()
            if watcher is not None:
                watcher.join(timeout=1.0)
            self._hide_zig_task()
