"""Executable example contract, included in the cumulative example test suite."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from coloph_toolset import tool_for

spec = importlib.util.spec_from_file_location("shipping_quote", Path(__file__).with_name("shipping_quote.py"))
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_quote_and_rejections(capsys):
    module.main()
    output = json.loads(capsys.readouterr().out)
    assert output["quote"]["amount_cents"] == 1000
    assert output["arguments"]["quantity"] == 2
    assert [errors[0]["type"] for errors in output["rejected"]] == [
        "greater_than",
        "missing",
        "extra_forbidden",
    ]
    assert "account_discount" not in output["schema"]["properties"]


def test_collection_and_insurance():
    declaration = tool_for(module.shipping_quote)
    values = declaration.validate_arguments({"quantity": 1, "destination": None, "insured": True})
    assert module.shipping_quote(None, **values)["amount_cents"] == 200
    with pytest.raises(ValidationError):
        declaration.validate_arguments({"quantity": 101, "destination": "AR"})
