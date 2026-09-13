"""Python tool declarations and canonical argument validation."""

from ._annotations import DeclarationError, annotation_with_description
from ._declaration import (
    Parameter,
    Tool,
    tool,
    tool_for,
)

__all__ = [
    "DeclarationError",
    "Parameter",
    "Tool",
    "annotation_with_description",
    "tool",
    "tool_for",
]
