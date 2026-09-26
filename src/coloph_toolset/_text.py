"""Result presentation and explicit output limiting.

This file began as Coloph's ``core/tools/_text.py``. The public runtime does
not replace or route process-global stdout. Applications that need legacy
stdout capture can wrap their callable before passing it to the runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

ToolOutput = str | int | float | bool | None


@dataclass(frozen=True)
class PresentedOutput:
    """A transport value plus explicit truncation information."""

    value: ToolOutput
    truncated: bool = False
    original_chars: int | None = None


def canonical_output(result: Any, presenter: Callable[[Any], ToolOutput] | None = None) -> ToolOutput:
    """Project a raw tool result without changing scalar meanings."""
    if presenter is not None:
        rendered = presenter(result)
    elif isinstance(result, str | int | float | bool) or result is None:
        rendered = result
    else:
        rendered = json.dumps(result, default=str, ensure_ascii=False, sort_keys=True)
    if not isinstance(rendered, str | int | float | bool) and rendered is not None:
        raise TypeError("tool presenter must return str, int, float, bool, or None")
    return rendered


def limit_output(output: ToolOutput, max_chars: int | None, *, suffix: str = "…") -> PresentedOutput:
    """Limit the transport representation while retaining the raw result elsewhere."""
    if max_chars is None:
        return PresentedOutput(output)
    if max_chars < 0:
        raise ValueError("max_output_chars must be non-negative")
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    if len(text) <= max_chars:
        return PresentedOutput(output, original_chars=len(text))
    if max_chars == 0:
        shortened = ""
    elif len(suffix) >= max_chars:
        shortened = suffix[:max_chars]
    else:
        shortened = text[: max_chars - len(suffix)] + suffix
    return PresentedOutput(shortened, truncated=True, original_chars=len(text))


__all__ = ["PresentedOutput", "ToolOutput", "canonical_output", "limit_output"]
