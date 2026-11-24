"""Tests for Zig version parsing and validation."""

from __future__ import annotations

import pytest

from zig_recipe.errors import ZigVersionError
from zig_recipe.zig.inspector import ZigVersion


def test_rejects_versions_before_017() -> None:
    with pytest.raises(ZigVersionError):
        ZigVersion.parse("0.16.0")


def test_parses_release_and_dev_versions() -> None:
    release = ZigVersion.parse("0.17.1")
    assert release.major_minor == "0.17"
    assert release.is_live is False

    dev = ZigVersion.parse("0.18.0-dev.9+abc123")
    assert dev.major_minor == "0.18"
    assert dev.is_live is True
