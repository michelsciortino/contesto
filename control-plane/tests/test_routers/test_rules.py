import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_rule(client: AsyncClient):
    payload = {
        "name": "Strip reminders",
        "priority": 10,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<system-reminder>"]},
    }
    resp = await client.post("/rules", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Strip reminders"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_rule_duplicate_priority_fails(client: AsyncClient):
    payload = {
        "name": "R1", "priority": 99,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<x>"]},
    }
    await client.post("/rules", json=payload)
    resp = await client.post("/rules", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_rules_ordered_by_priority(client: AsyncClient):
    for p in [30, 10, 20]:
        await client.post("/rules", json={
            "name": f"R{p}", "priority": p,
            "match_logic": {"==": [1, 1]},
            "mutate_logic": {"strip_tag": ["<x>"]},
        })
    resp = await client.get("/rules")
    assert resp.status_code == 200
    priorities = [r["priority"] for r in resp.json()]
    assert priorities == sorted(priorities)


@pytest.mark.asyncio
async def test_delete_rule_deactivates(client: AsyncClient):
    resp = await client.post("/rules", json={
        "name": "Temp", "priority": 50,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<x>"]},
    })
    rule_id = resp.json()["id"]
    del_resp = await client.delete(f"/rules/{rule_id}")
    assert del_resp.status_code == 200
    detail = await client.get(f"/rules/{rule_id}")
    assert detail.json()["is_active"] is False


@pytest.mark.asyncio
async def test_update_rule(client: AsyncClient):
    resp = await client.post("/rules", json={
        "name": "Original", "priority": 77,
        "match_logic": {"==": [1, 1]},
        "mutate_logic": {"strip_tag": ["<x>"]},
    })
    rule_id = resp.json()["id"]
    upd = await client.put(f"/rules/{rule_id}", json={"name": "Updated"})
    assert upd.status_code == 200
    assert upd.json()["name"] == "Updated"
