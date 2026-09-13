"""Python tool declarations and canonical argument validation."""

from ._decorator import (
    DeclarationError,
    Tool,
    ToolParam,
    annotation_with_description,
    signature_with_tool_params,
    tool,
    tool_for,
)

__all__ = [
    "DeclarationError",
    "Tool",
    "ToolParam",
    "annotation_with_description",
    "signature_with_tool_params",
    "tool",
    "tool_for",
]
