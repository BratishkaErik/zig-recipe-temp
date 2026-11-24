"""Exception hierarchy for zig-recipe."""

from __future__ import annotations


class ZigRecipeError(Exception):
    """Base exception for all zig-recipe errors."""


class ManifestError(ZigRecipeError):
    """Raised when a build.zig.zon file is missing or invalid."""


class ZigVersionError(ZigRecipeError):
    """Raised when the Zig version is unsupported (< 0.17.0) or unparseable."""


class ZigProcessError(ZigRecipeError):
    """Raised when invoking the zig compiler or helper fails."""


class DependencyError(ZigRecipeError):
    """Raised when resolving, fetching, or archiving dependencies fails."""
