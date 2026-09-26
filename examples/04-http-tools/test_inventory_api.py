import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from inventory_api import Inventory, build_app
from starlette.testclient import TestClient


def test_authenticated_selected_tools_execute() -> None:
    inventory = Inventory({"widget": 5})

    with TestClient(build_app(inventory)) as client:
        available = client.post(
            "/api/tools/available",
            headers={"authorization": "Bearer local-demo"},
            json={"args": {"sku": "widget"}},
        )
        reserved = client.post(
            "/api/tools/reserve",
            headers={"authorization": "Bearer local-demo"},
            json={"args": {"sku": "widget", "quantity": "2"}},
        )

    assert available.status_code == 200
    assert available.json()["result"] == "widget: 5 available"
    assert reserved.status_code == 200
    assert reserved.json()["result"] == "reserved 2 widget; 3 remain"
    assert inventory.stock == {"widget": 3}


def test_auth_validation_and_selection_rejections() -> None:
    with TestClient(build_app(Inventory({"widget": 5}))) as client:
        unauthenticated = client.post("/api/tools/available", json={"args": {"sku": "widget"}})
        invalid = client.post(
            "/api/tools/reserve",
            headers={"authorization": "Bearer local-demo"},
            json={"args": {"sku": "widget", "quantity": 0}},
        )
        unselected = client.post(
            "/api/tools/reset",
            headers={"authorization": "Bearer local-demo"},
            json={"args": {}},
        )

    assert unauthenticated.status_code == 401
    assert invalid.status_code == 422
    assert unselected.status_code == 404
