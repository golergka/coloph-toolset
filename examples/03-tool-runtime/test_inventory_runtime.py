import asyncio

import pytest
from inventory_runtime import Inventory, Session, available, build_runtime, reserve, reserve_text
from pydantic import ValidationError

from coloph_toolset import InvocationError


def test_sync_and_async_calls_with_explicit_shared_state() -> None:
    inventory = Inventory({"widget": 5})
    tools = build_runtime(inventory)
    session = Session()

    reserved = tools.invoke_sync(
        reserve,
        {"sku": "widget", "quantity": "2"},
        state=session,
        presenter=reserve_text,
    )
    observed = asyncio.run(tools.invoke(available, {"sku": "widget"}, state=session))

    assert reserved.output == "Reserved 2 widget; 3 remain."
    assert reserved.raw == {"sku": "widget", "reserved": 2, "remaining": 3}
    assert reserved.committed is True
    assert observed.raw == {"sku": "widget", "available": 3}
    assert session.calls == ["reserve:widget", "available:widget"]


def test_invalid_input_does_not_change_inventory() -> None:
    inventory = Inventory({"widget": 5})

    with pytest.raises(ValidationError):
        build_runtime(inventory).invoke_sync(reserve, {"sku": "widget", "quantity": "many"})

    assert inventory.stock == {"widget": 5}


def test_body_failure_rolls_back() -> None:
    inventory = Inventory({"widget": 1})

    with pytest.raises(InvocationError) as caught:
        build_runtime(inventory).invoke_sync(reserve, {"sku": "widget", "quantity": 2})

    assert caught.value.phase == "body"
    assert caught.value.committed is False
    assert inventory.stock == {"widget": 1}


def test_finalization_failure_is_distinct() -> None:
    inventory = Inventory({"widget": 5})

    with pytest.raises(InvocationError) as caught:
        build_runtime(inventory, fail_commit=True).invoke_sync(reserve, {"sku": "widget", "quantity": 2})

    assert caught.value.phase == "finalization"
    assert inventory.stock == {"widget": 5}


def test_presentation_failure_reports_committed_work() -> None:
    inventory = Inventory({"widget": 5})

    def fail_to_present(_ctx, _result):
        raise RuntimeError("template unavailable")

    with pytest.raises(InvocationError) as caught:
        build_runtime(inventory).invoke_sync(
            reserve,
            {"sku": "widget", "quantity": 2},
            presenter=fail_to_present,
        )

    assert caught.value.phase == "presentation"
    assert caught.value.committed is True
    assert inventory.stock == {"widget": 3}
