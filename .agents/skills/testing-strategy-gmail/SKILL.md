---
name: testing-strategy-gmail
description: Test the Atlas strategy engine and Gmail send flow end-to-end. Use when verifying strategy board, weekly cadence, or Gmail send/reply UI or API changes.
---

# Testing: Strategy engine + Gmail integration

## Run locally
- Backend (:8000): `cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000`
  - Health check: `curl localhost:8000/health` → `{"status":"ok","llm_provider":"mock"...}`
  - Default dev config uses `LLM_PROVIDER=mock` (deterministic) and **no Gmail creds**, so Gmail runs in **offline** mode.
- Frontend (:3000): `cd frontend && npm run dev`
- If `frontend/node_modules` is missing, run `npm install` first (it is in the blueprint maintenance step but not auto-run).

## Auth
- Register a fresh user at `/login` (toggle to "Create account"). Any email + 8+ char password works; no email verification.

## Strategy flow (all in UI)
1. **Strategy** nav → `/strategy` → **+ New strategy**. The form is pre-filled with placeholder *examples* (not real values) — type into each field explicitly (Commodity defaults to `sugar`).
2. Board `/strategy/[id]`: four pillars (Origination / Demand / Supply / Execution) render objectives from `draft_pillars` (mock LLM falls back to deterministic `_fallback_pillars`).
3. **Generate this week's plan** builds tasks from live opportunities/leads via the existing `next_action` engine; empty pipeline still yields coverage-gap tasks. Tasks appear grouped by pillar + in "Today's focus".
4. Checkbox toggles task done → pillar "X/Y tasks done" count updates on reload.
5. Manual tasks (**+ Add task**) survive **Generate** (only `source="auto"` tasks are deleted/rebuilt).

## Offline Gmail send flow
1. Create an Opportunity, add a supplier lead via **Manual entry** (this tab exposes the **Email** field; "From library" does not).
2. Expand the lead (**edit**) → **Compose stage-1 email** (stage 1 = outreach_email, else follow_up_email) → routes to `/documents/[id]`.
3. Document page has a **Send via Gmail** panel: shows **Offline** badge (no creds), recipient prefilled from the draft's persisted `supplier_lead_id`/`buyer_lead_id` linkage.
4. **Record send (offline)** → green "Recorded offline (no Gmail credentials). Would have gone to <email>." No email transmitted.
5. Verify on the opportunity: supplier lead advances **new → contacted**, last-contact date stamped, engagement-freshness score rises. (Buyer leads go new → engaged.)

## Reply sync (not easily testable in UI offline)
- `POST /api/v1/email/sync` is a no-op offline. Reply-driven advancement (new/contacted → quoted supplier / engaged buyer) is covered by backend unit tests via a stubbed fetcher — see `backend/tests/test_email.py`.

## Live Gmail (deferred)
- Requires env `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` (Gmail 2FA on, app password from myaccount.google.com/apppasswords). Never persisted in DB.

## Devin Secrets Needed
- For offline testing: **none**.
- For live SMTP/IMAP testing: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (env only).

## Notes / gotchas
- Dashboard "Market reference prices" often shows Yahoo Finance 502s (upstream rate-limiting) — unrelated to these features.
- Validation commands: `ruff check app tests` + `python -m pytest -q` (backend), `npx tsc --noEmit` + `npm run build` (frontend).
