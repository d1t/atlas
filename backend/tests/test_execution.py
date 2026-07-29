"""Tests for the execution spine.

These target the invariants rather than the plumbing: an action cannot skip states,
an external action cannot bypass approval, a gated task cannot be completed without
evidence, and the same outreach cannot be fired twice.
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "execution.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")

    from importlib import reload

    import app.core.config as config_mod
    import app.core.db as db_mod
    reload(config_mod)
    reload(db_mod)

    import app.main as main_mod
    reload(main_mod)

    async with db_mod.engine.begin() as conn:
        from app.models import Base
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=main_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.db_module = db_mod
        yield c

    await db_mod.engine.dispose()
    if os.path.exists(db_path):
        os.remove(db_path)


async def _auth(client: AsyncClient) -> dict:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "exec@atlas.example.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _strategy(client: AsyncClient, headers: dict) -> int:
    r = await client.post(
        "/api/v1/strategy",
        headers=headers,
        json={
            "title": "Control the Brazil->Nigeria sugar chain",
            "north_star": "50k MT/quarter at >$18/MT",
            "commodity": "sugar",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _task(client: AsyncClient, headers: dict, strategy_id: int, **kw) -> dict:
    body = {"pillar": "demand", "title": "Secure a buyer", **kw}
    r = await client.post(
        f"/api/v1/strategy/{strategy_id}/tasks", headers=headers, json=body
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- Evidence-gated completion --------------------------------------------


async def test_gated_task_refuses_completion_without_evidence(client):
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    task = await _task(client, headers, sid)

    # Gate the task the way an agent would when the task asserts a real outcome.
    from app.models.strategy import StrategyTask

    db_mod = client.db_module
    async with db_mod.AsyncSessionLocal() as session:
        row = await session.get(StrategyTask, task["id"])
        row.requires_evidence = True
        row.acceptance_criteria = "A countersigned off-take contract is on file."
        await session.commit()

    r = await client.post(
        f"/api/v1/execution/tasks/{task['id']}/complete", headers=headers, json={}
    )
    assert r.status_code == 409
    assert "requires evidence" in r.json()["detail"]
    assert "countersigned off-take" in r.json()["detail"]


async def test_evidence_unlocks_completion(client):
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    task = await _task(client, headers, sid)

    from app.models.strategy import StrategyTask

    db_mod = client.db_module
    async with db_mod.AsyncSessionLocal() as session:
        row = await session.get(StrategyTask, task["id"])
        row.requires_evidence = True
        await session.commit()

    r = await client.post(
        f"/api/v1/execution/tasks/{task['id']}/evidence",
        headers=headers,
        json={"kind": "document_signed", "description": "SPA signed by Dangote"},
    )
    assert r.status_code == 201

    r = await client.post(
        f"/api/v1/execution/tasks/{task['id']}/complete", headers=headers, json={}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["evidence_count"] == 1


async def test_override_is_recorded_not_silent(client):
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    task = await _task(client, headers, sid)

    from app.models.strategy import StrategyTask

    db_mod = client.db_module
    async with db_mod.AsyncSessionLocal() as session:
        row = await session.get(StrategyTask, task["id"])
        row.requires_evidence = True
        await session.commit()

    r = await client.post(
        f"/api/v1/execution/tasks/{task['id']}/complete",
        headers=headers,
        json={"override_reason": "Contract signed offline, scan to follow"},
    )
    assert r.status_code == 200
    assert r.json()["override_reason"] == "Contract signed offline, scan to follow"

    audit = await client.get(
        f"/api/v1/execution/strategies/{sid}/audit", headers=headers
    )
    entries = [e for e in audit.json() if e["action"] == "task.completed"]
    assert entries and entries[0]["after"]["override"] is True


async def test_ungated_task_completes_normally(client):
    """Pre-existing tasks are not gated, so old behaviour is preserved."""
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    task = await _task(client, headers, sid)

    r = await client.post(
        f"/api/v1/execution/tasks/{task['id']}/complete", headers=headers, json={}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "done"


# --- Dependencies ----------------------------------------------------------


async def test_task_blocked_by_unfinished_dependency(client):
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    first = await _task(client, headers, sid, title="Verify contact details")
    second = await _task(client, headers, sid, title="Send outreach")

    from app.models.strategy import StrategyTask

    db_mod = client.db_module
    async with db_mod.AsyncSessionLocal() as session:
        row = await session.get(StrategyTask, second["id"])
        row.depends_on_ids = [first["id"]]
        await session.commit()

    r = await client.post(
        f"/api/v1/execution/tasks/{second['id']}/complete", headers=headers, json={}
    )
    assert r.status_code == 409
    assert "prerequisite" in r.json()["detail"]

    await client.post(
        f"/api/v1/execution/tasks/{first['id']}/complete", headers=headers, json={}
    )
    r = await client.post(
        f"/api/v1/execution/tasks/{second['id']}/complete", headers=headers, json={}
    )
    assert r.status_code == 200


# --- Task tree -------------------------------------------------------------


async def test_task_tree_nests_children_and_flags_blockers(client):
    headers = await _auth(client)
    sid = await _strategy(client, headers)
    parent = await _task(client, headers, sid, title="Establish three buyer contracts")
    child = await _task(client, headers, sid, title="Define ideal buyer profile")
    blocker = await _task(client, headers, sid, title="Select priority countries")

    from app.models.strategy import StrategyTask

    db_mod = client.db_module
    async with db_mod.AsyncSessionLocal() as session:
        row = await session.get(StrategyTask, child["id"])
        row.parent_id = parent["id"]
        row.kind = "subtask"
        row.depends_on_ids = [blocker["id"]]
        parent_row = await session.get(StrategyTask, parent["id"])
        parent_row.kind = "outcome"
        await session.commit()

    r = await client.get(
        f"/api/v1/execution/strategies/{sid}/tasks/tree", headers=headers
    )
    assert r.status_code == 200
    roots = r.json()
    outcome = next(n for n in roots if n["id"] == parent["id"])
    assert outcome["kind"] == "outcome"
    assert [c["id"] for c in outcome["children"]] == [child["id"]]
    assert outcome["children"][0]["blocked_by"] == [blocker["id"]]
    # The blocker itself is a root, not swallowed by the tree.
    assert any(n["id"] == blocker["id"] for n in roots)


# --- Action state machine --------------------------------------------------


async def test_illegal_transition_is_refused():
    from app.services.execution_service import ALLOWED_TRANSITIONS

    # A proposal must never be able to claim completion directly.
    assert "completed" not in ALLOWED_TRANSITIONS["proposed"]
    # Terminal states are genuinely terminal.
    for terminal in ("completed", "rejected", "cancelled"):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_external_actions_always_require_approval():
    from app.services.execution_service import requires_approval_for

    # Even a "trusted" workflow cannot pre-authorise leaving the building.
    for action_type in ("send_email", "commit_funds", "sign_document", "delete_data"):
        assert requires_approval_for(action_type, trusted=True) is True

    # Internal analysis runs unattended.
    for action_type in ("research", "draft_email", "analyse_replies"):
        assert requires_approval_for(action_type) is False

    # An unrecognised capability is gated by default.
    assert requires_approval_for("exfiltrate_everything") is True


def test_idempotency_key_is_stable_and_target_sensitive():
    from app.services.execution_service import build_idempotency_key

    a = build_idempotency_key(1, "send_email", "buyer@example.com")
    b = build_idempotency_key(1, "send_email", " Buyer@Example.com ")
    c = build_idempotency_key(1, "send_email", "other@example.com")
    assert a == b, "same recipient must collide regardless of case/whitespace"
    assert a != c
