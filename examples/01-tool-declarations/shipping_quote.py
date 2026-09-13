"""A standalone tool declaration with schema export and input validation."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from coloph_toolset import tool, tool_for


@tool(model_hidden_args=("account_discount",))
def shipping_quote(
    ctx,
    quantity: Annotated[int, "Number of parcels", Field(gt=0, le=100)],
    destination: Annotated[str | None, "Country code, or null for collection"],
    service: Annotated[Literal["standard", "express"], "Delivery service"] = "standard",
    insured: Annotated[bool, "Include insurance"] = False,
    account_discount: Annotated[bool, "Private account discount"] = False,
) -> dict[str, object]:
    """Calculate a shipping quote without external services."""
    unit_cost = 0 if destination is None else (1200 if service == "express" else 500)
    total = quantity * unit_cost + (200 if insured else 0)
    if account_discount:
        total = total * 9 // 10
    return {"amount_cents": total, "currency": "USD", "parcels": quantity}


def main() -> None:
    declaration = tool_for(shipping_quote)
    arguments = declaration.validate_arguments({"quantity": "2", "destination": "AR"})
    rejected = []
    for value in (
        {"quantity": 0, "destination": "AR"},
        {"quantity": 2},
        {"quantity": 2, "destination": None, "account_discount": True},
    ):
        try:
            declaration.validate_arguments(value)
        except ValidationError as error:
            rejected.append(error.errors(include_url=False))
    print(
        json.dumps(
            {
                "schema": declaration.json_schema(),
                "arguments": arguments,
                "quote": shipping_quote(None, **arguments),
                "rejected": rejected,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
