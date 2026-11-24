"""Tests for Zig Progress Protocol parsing and Rich ProgressReporter."""

from __future__ import annotations

import struct

from rich.console import Console

from zig_recipe.ui.progress import (
    ProgressReporter,
    ZigProgressNode,
    parse_progress_packets,
    pick_active_zig_node,
)


def _encode_packet(nodes: list[tuple[str, int, int, int]]) -> bytes:
    buf = bytearray([len(nodes)])
    for name, completed, total, _parent in nodes:
        raw_name = name.encode("utf-8")[:120].ljust(120, b"\x00")
        buf.extend(struct.pack("<II120s", completed, total, raw_name))
    for _name, _completed, _total, parent in nodes:
        buf.append(parent)
    return bytes(buf)


def test_parse_single_and_multiple_packets() -> None:
    pkt1 = _encode_packet([("Fetch", 1, 10, 0xFF)])
    pkt2 = _encode_packet([("Fetch", 4, 10, 0xFF), ("Download zig-wayland", 2, 5, 0)])

    latest, rem = parse_progress_packets(pkt1 + pkt2)
    assert rem == b""
    assert latest == (
        ZigProgressNode("Fetch", 4, 10, 0xFF),
        ZigProgressNode("Download zig-wayland", 2, 5, 0),
    )
    active = pick_active_zig_node(latest)
    assert active == ZigProgressNode("Download zig-wayland", 2, 5, 0)


def test_parse_partial_and_empty_packets() -> None:
    pkt = _encode_packet([("Compile", 3, 7, 0xFF)])
    part1, part2 = pkt[:20], pkt[20:]

    latest, rem = parse_progress_packets(part1)
    assert latest is None
    assert rem == part1

    latest2, rem2 = parse_progress_packets(rem + part2)
    assert rem2 == b""
    assert latest2 == (ZigProgressNode("Compile", 3, 7, 0xFF),)

    # Zero-node packet clears active nodes.
    cleared, rem3 = parse_progress_packets(b"\x00")
    assert rem3 == b""
    assert cleared == ()
    assert pick_active_zig_node(cleared) is None


def test_parse_invalid_node_count_resyncs_cleanly() -> None:
    latest, rem = parse_progress_packets(b"\xffgarbage")
    assert latest is None
    assert rem == b""


def test_progress_reporter_disabled_mode_is_safe() -> None:
    console = Console(force_terminal=False)
    with ProgressReporter(console, enabled=False) as prog:
        prog.set_stage("Fetching dependencies")
        prog.update_zig_nodes((ZigProgressNode("Fetch", 2, 5, 0xFF),))
        prog.advance_stage("Inspecting default")
