"""Prompt template utilities using Jinja2.

This module provides a thin wrapper around Jinja2 for rendering prompt templates.
For advanced usage, use Jinja2's Environment and Template classes directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, Template, Undefined


class SilentUndefined(Undefined):
    """Custom undefined handler that returns empty string for missing variables."""

    def _fail_with_undefined_error(self, *args, **kwargs):
        return ""

    __add__ = __radd__ = __mul__ = __rmul__ = __div__ = __rdiv__ = (
        __truediv__
    ) = __rtruediv__ = __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = (
        __pos__
    ) = __neg__ = __call__ = __getitem__ = __lt__ = __le__ = __gt__ = __ge__ = (
        __int__
    ) = __float__ = __complex__ = __pow__ = __rpow__ = _fail_with_undefined_error


# Default environments (cached)
_default_env_strict: Environment | None = None
_default_env_safe: Environment | None = None


def get_jinja_env(*, strict: bool = False) -> Environment:
    """Get a Jinja2 environment with sensible defaults for prompts.

    Args:
        strict: If True, raise error for undefined variables.
               If False (default), undefined variables render as empty string.

    Returns:
        Configured Jinja2 Environment instance
    """
    global _default_env_strict, _default_env_safe

    if strict:
        if _default_env_strict is None:
            _default_env_strict = Environment(
                undefined=StrictUndefined,
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        return _default_env_strict
    else:
        if _default_env_safe is None:
            _default_env_safe = Environment(
                undefined=SilentUndefined,
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        return _default_env_safe


def render_template(template_str: str, /, **context: Any) -> str:
    """Render a Jinja2 template string with the given context.

    Args:
        template_str: The Jinja2 template string
        **context: Variables to pass to the template

    Returns:
        Rendered string

    Examples:
        >>> render_template("Hello, {{ name }}!", name="Alice")
        "Hello, Alice!"

        >>> render_template('''
        ... {% for item in items %}
        ... - {{ item }}
        ... {% endfor %}
        ... ''', items=["a", "b", "c"])
        "- a\\n- b\\n- c\\n"
    """
    env = get_jinja_env(strict=False)
    template = env.from_string(template_str)
    return template.render(**context)


def render_template_strict(template_str: str, /, **context: Any) -> str:
    """Render a Jinja2 template string with strict undefined checking.

    Args:
        template_str: The Jinja2 template string
        **context: Variables to pass to the template

    Returns:
        Rendered string

    Raises:
        jinja2.UndefinedError: If any required variable is missing
    """
    env = get_jinja_env(strict=True)
    template = env.from_string(template_str)
    return template.render(**context)


def load_template(path: str | Path, *, strict: bool = False) -> Template:
    """Load a Jinja2 template from a file.

    Args:
        path: Path to the template file
        strict: Whether to use strict undefined checking

    Returns:
        Jinja2 Template object ready for rendering
    """
    path_obj = Path(path)
    content = path_obj.read_text(encoding="utf-8")
    env = get_jinja_env(strict=strict)
    return env.from_string(content)


# Aliases for convenience
render = render_template
render_strict = render_template_strict
