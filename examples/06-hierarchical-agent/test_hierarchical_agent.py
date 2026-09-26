from hierarchical_agent import AgentDeps, build_agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel


def test_restricted_hierarchy_discovers_documents_recovers_and_finishes() -> None:
    turn = 0

    def model(messages, _info):
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResponse(parts=[ToolCallPart("orders", {}, "browse")])
        if turn == 2:
            group_text = str(messages[-1])
            assert "search" in group_text
            assert "close" in group_text
            assert "delete-all" not in group_text
            return ModelResponse(
                parts=[ToolCallPart("orders", {"command_path": "search", "arguments": {"query": "A-100"}}, "docs")]
            )
        if turn == 3:
            assert "Order operations" in str(messages[-1])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "orders",
                        {"command_path": "search", "arguments": {"query": "A-100", "limit": 0}},
                        "invalid",
                    )
                ]
            )
        if turn == 4:
            assert "greater_than" in str(messages[-1])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "orders",
                        {"command_path": "search", "arguments": {"query": "A-100", "limit": 2}},
                        "search",
                    )
                ]
            )
        if turn == 5:
            return ModelResponse(
                parts=[
                    ToolCallPart("orders", {"command_path": "close", "arguments": {"order_id": "A-100"}}, "close-docs")
                ]
            )
        if turn == 6:
            assert "The command did not run" in str(messages[-1])
            return ModelResponse(
                parts=[ToolCallPart("orders", {"command_path": "close", "arguments": {"order_id": "A-100"}}, "close")]
            )
        return ModelResponse(parts=[TextPart("Order closed.")])

    deps = AgentDeps()
    result = build_agent(FunctionModel(model)).run_sync("Find and close A-100.", deps=deps)

    assert result.output == "Order closed."
    assert deps.calls == ["search:A-100:2", "close:A-100"]
    assert deps.terminal_result == "closed:A-100"
    assert turn == 7
