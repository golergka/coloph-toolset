from inventory_agent import AgentDeps, Inventory, build_agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel


def test_deterministic_agent_recovers_then_runs_sync_and_async_tools() -> None:
    turn = 0

    def model(_messages, _info):
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResponse(parts=[ToolCallPart("inventory_add", {"sku": "A", "quantity": 0}, "bad")])
        if turn == 2:
            return ModelResponse(parts=[ToolCallPart("inventory_add", {"sku": "A", "quantity": 3}, "add")])
        if turn == 3:
            return ModelResponse(parts=[ToolCallPart("inventory_count", {"sku": "A"}, "count")])
        return ModelResponse(parts=[TextPart("Inventory updated.")])

    inventory = Inventory()
    result = build_agent(FunctionModel(model)).run_sync(
        "Add three A items, then count them.", deps=AgentDeps(inventory)
    )

    assert result.output == "Inventory updated."
    assert inventory.quantities == {"A": 3}
    assert turn == 4
