"""Python 3.14 deferred annotations need no future import to remain lazy."""

import sys
from typing import TYPE_CHECKING

import pytest

from coloph_toolset import Tool

if TYPE_CHECKING:

    class MissingContext: ...

    class MissingReturn: ...


@pytest.mark.skipif(sys.version_info < (3, 14), reason="PEP 649 requires Python 3.14")
def test_context_and_return_annotations_are_not_evaluated():
    def fn(ctx: MissingContext, value: int) -> MissingReturn:
        return value

    assert Tool(fn).validate_arguments({"value": "2"}) == {"value": 2}
