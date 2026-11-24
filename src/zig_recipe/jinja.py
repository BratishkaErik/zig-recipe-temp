"""Format-agnostic Jinja2 template renderer built on the inspect JSON schema."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined

from zig_recipe.model import RecipeModel


def make_jinja_env() -> Environment:
    """Create a strict Jinja2 environment."""
    return Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def render_template(model: RecipeModel, *, template_path: Path) -> str:
    """Render an arbitrary user Jinja2 template using the exact `inspect --format json` schema."""
    template_text = template_path.read_text(encoding="utf-8")
    template = make_jinja_env().from_string(template_text)
    return template.render(**model.to_dict())
