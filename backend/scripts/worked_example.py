"""Drive one strategic objective end-to-end through the live API and print what happens.

Not a test — a demonstration that the orchestrator, approval policy and evidence gate
work together against a running server rather than only in unit tests.

    uvicorn app.main:app --port 8030
    python scripts/worked_example.py
"""
import asyncio
import os

import httpx

BASE = os.environ.get("ATLAS_BASE", "http://localhost:8030")


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * len(title))


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": "demo@atlas.example.com", "password": "secret123"},
        )
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        sid = (
            await c.post(
                "/api/v1/strategy",
                headers=h,
                json={
                    "title": "Control the Brazil to Nigeria sugar chain",
                    "north_star": "50k MT/quarter at >$18/MT",
                    "commodity": "sugar",
                    "origin_region": "Brazil",
                    "destination_region": "Nigeria",
                    "target_volume_mt": 50000,
                    "target_margin_per_mt": 18,
                },
            )
        ).json()["id"]

        oid = (
            await c.post(
                "/api/v1/opportunities",
                headers=h,
                json={
                    "title": "Nigeria sugar 50k MT CFR Lagos",
                    "commodity": "sugar",
                    "volume_mt": 50000,
                    "destination_country": "Nigeria",
                    "destination_port": "Lagos",
                    "incoterms": "CFR",
                    "status": "sourcing",
                },
            )
        ).json()["id"]

        for name, email, status, volume in (
            ("Dangote Sugar", "buyer@dangote.example.com", "committed", 20000),
            (
                "Golden Sugar Refinery",
                "procurement@goldensugar.example.com",
                "engaged",
                15000,
            ),
        ):
            lead = (
                await c.post(
                    f"/api/v1/opportunities/{oid}/buyer-leads",
                    headers=h,
                    json={
                        "buyer_name": name,
                        "email": email,
                        "volume_mt": volume,
                    },
                )
            ).json()
            await c.patch(
                f"/api/v1/opportunities/{oid}/buyer-leads/{lead['id']}",
                headers=h,
                json={"status": status},
            )

        # An existing exchange with Dangote, so the follow-up below is genuinely a
        # follow-up rather than a first approach.
        await c.post(
            "/api/v1/email/send",
            headers=h,
            json={
                "to_email": "buyer@dangote.example.com",
                "subject": "Nigeria sugar programme",
                "body": "Good to speak. Sending details shortly.",
            },
        )
        await c.post(
            f"/api/v1/opportunities/{oid}/supplier-leads",
            headers=h,
            json={
                "supplier_name": "Atlas Mill Ltda",
                "email": "sales@atlasmill.example.com",
                "status": "new",
            },
        )

        rule("1. Orchestrator plans from the real pipeline")
        plan = (
            await c.post(f"/api/v1/execution/strategies/{sid}/plan", headers=h)
        ).json()
        print("reasoning:", plan["run"]["reasoning"])
        print("summary:  ", plan["run"]["summary"])

        rule("2. The task tree it produced")
        tree = (
            await c.get(
                f"/api/v1/execution/strategies/{sid}/tasks/tree", headers=h
            )
        ).json()

        def show(nodes, depth=0):
            for n in nodes:
                pad = "  " * depth
                gate = "[evidence]" if n["requires_evidence"] else "          "
                print(f"{pad}- {gate} ({n['kind']}/{n['assignee']}) {n['title']}")
                if n["acceptance_criteria"]:
                    print(f"{pad}    done when: {n['acceptance_criteria']}")
                show(n["children"], depth + 1)

        show(tree)

        rule("3. Re-running changes nothing")
        again = (
            await c.post(f"/api/v1/execution/strategies/{sid}/plan", headers=h)
        ).json()
        print("tasks created on second run:", len(again["created_task_ids"]))

        rule("4. Completing a gated task without evidence is refused")
        target = None

        def find(nodes):
            nonlocal target
            for n in nodes:
                if n["requires_evidence"] and target is None:
                    target = n
                find(n["children"])

        find(tree)
        r = await c.post(
            f"/api/v1/execution/tasks/{target['id']}/complete", headers=h, json={}
        )
        print(f"POST complete -> {r.status_code}")
        print(" ", r.json()["detail"])

        rule("5. Executing the plan — the agent acts, and is stopped where it must be")
        run = (
            await c.post(f"/api/v1/execution/strategies/{sid}/execute", headers=h)
        ).json()
        print(f"  {run['run']['summary']}")
        for o in run["outcomes"]:
            print(f"  [{o['state']:18}] {o['capability']}")
            if o["detail"]:
                print(f"      {o['detail']}")
        print("\n  Nothing was sent. Each outbound message is now sitting in the")
        print("  approval queue with the exact words that would go out:")
        queue = (
            await c.get(f"/api/v1/execution/strategies/{sid}/approvals", headers=h)
        ).json()
        for item in queue:
            a, act = item["approval"], item["action"]
            print(f"  #{a['id']} [{a['risk']}] {item['task_title']}")
            print(f"      to: {act['payload'].get('to_email')}")
            print(f"      subject: {act['payload'].get('subject')}")

        if queue:
            print("\n  Approving one dispatches it and parks the action awaiting a")
            print("  reply — sending is not the same as succeeding:")
            aid = queue[0]["approval"]["id"]
            await c.post(
                f"/api/v1/execution/approvals/{aid}/decide",
                headers=h,
                json={"approved": True},
            )
            act = (
                await c.get(
                    f"/api/v1/execution/strategies/{sid}/actions", headers=h
                )
            ).json()
            for a in act:
                if a["id"] == queue[0]["action"]["id"]:
                    print(f"      action -> {a['state']}")

        rule("6. What the approval policy says about a draft")
        for label, payload in (
            (
                "first approach, quotes a price",
                {
                    "recipient": "sales@atlasmill.example.com",
                    "subject": "RFQ",
                    "body": "We can work at USD 430 per MT CFR Lagos.",
                    "thread_key": "rfq-atlas-mill",
                    "template_key": "rfq_v1",
                },
            ),
            (
                "plain chase-up, known contact, no grant",
                {
                    "recipient": "buyer@dangote.example.com",
                    "subject": "Following up",
                    "body": "Just checking in. Any thoughts?",
                    "thread_key": "dangote-followup",
                    "template_key": "followup_v1",
                },
            ),
        ):
            d = (
                await c.post(
                    f"/api/v1/execution/strategies/{sid}/policy/preview",
                    headers=h,
                    json=payload,
                )
            ).json()
            print(f"{label}:")
            print(f"  approval required: {d['requires_approval']} ({d['risk']})")
            print(f"  {d['reason']}")

        rule("7. Same chase-up once the user grants a standing authorisation")
        grant = (
            await c.post(
                f"/api/v1/execution/strategies/{sid}/grants",
                headers=h,
                json={
                    "thread_key": "dangote-followup",
                    "recipient": "buyer@dangote.example.com",
                    "template_key": "followup_v1",
                    "max_messages": 3,
                    "expires_in_days": 14,
                },
            )
        ).json()
        d = (
            await c.post(
                f"/api/v1/execution/strategies/{sid}/policy/preview",
                headers=h,
                json={
                    "recipient": "buyer@dangote.example.com",
                    "subject": "Following up",
                    "body": "Just checking in. Any thoughts?",
                    "thread_key": "dangote-followup",
                    "template_key": "followup_v1",
                },
            )
        ).json()
        print(f"  approval required: {d['requires_approval']} ({d['risk']})")
        print(f"  {d['reason']}")

        print("\n  ...but the moment it mentions price, the grant stops applying:")
        d = (
            await c.post(
                f"/api/v1/execution/strategies/{sid}/policy/preview",
                headers=h,
                json={
                    "recipient": "buyer@dangote.example.com",
                    "subject": "Following up",
                    "body": "Just checking in — we can hold USD 430/MT for you.",
                    "thread_key": "dangote-followup",
                    "template_key": "followup_v1",
                },
            )
        ).json()
        print(f"  approval required: {d['requires_approval']} ({d['risk']})")
        print(f"  {d['reason']}")

        rule("8. Pausing the grant stops it immediately")
        await c.post(
            f"/api/v1/execution/grants/{grant['id']}/pause",
            headers=h,
            json={"paused": True},
        )
        d = (
            await c.post(
                f"/api/v1/execution/strategies/{sid}/policy/preview",
                headers=h,
                json={
                    "recipient": "buyer@dangote.example.com",
                    "subject": "Following up",
                    "body": "Just checking in. Any thoughts?",
                    "thread_key": "dangote-followup",
                    "template_key": "followup_v1",
                },
            )
        ).json()
        print(f"  approval required: {d['requires_approval']} — {d['reason']}")

        rule("9. Audit trail")
        for entry in (
            await c.get(f"/api/v1/execution/strategies/{sid}/audit", headers=h)
        ).json()[:8]:
            print(f"  {entry['actor_type']:6} {entry['action']}")


asyncio.run(main())
