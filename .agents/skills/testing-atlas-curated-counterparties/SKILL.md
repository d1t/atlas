---
name: testing-atlas-curated-counterparties
description: Test the curated counterparties feature end-to-end in Atlas Trade OS. Use when verifying curated supplier seeding, Hunter.io contact enrichment, idempotency, and Curated badge on AI Discover.
---

# Testing Atlas Trade OS — Curated Counterparties

## Overview

The curated counterparties feature provides a static registry of vetted supplier desks for known trade lanes (e.g., Brazil raw sugar). This skill covers end-to-end testing of the feature via the browser UI.

## Devin Secrets Needed

- **`HUNTER_API_KEY`** (optional) — Hunter.io Domain Search API key for the contact-enrichment feature. If unset, the backend falls back to deterministic **mock** contacts (`trading@{domain}`, name "Trade Desk", title "Trading Desk"), so the flow is fully testable offline. Provide a real key only when you specifically want to verify **live** enrichment (real named contacts). Saved org-wide for reuse.
- Otherwise the app runs with `LLM_PROVIDER=mock` for deterministic offline testing — no other keys needed.

## Environment Setup

1. **Backend:** Start from the `backend/` directory:
   ```bash
   cd /home/ubuntu/repos/atlas/backend
   source .venv311/bin/activate           # Python 3.11 venv lives here
   rm -f atlas.db                         # fresh DB each session
   # Offline (mock contacts):
   LLM_PROVIDER=mock uvicorn app.main:app --host 0.0.0.0 --port 8000 &
   # OR live Hunter.io enrichment (real contacts):
   LLM_PROVIDER=mock HUNTER_API_KEY="${HUNTER_API_KEY}" uvicorn app.main:app --host 0.0.0.0 --port 8000 &
   ```
   - To switch between mock and live, restart the backend with/without `HUNTER_API_KEY` and re-seed a fresh opportunity (enrichment only runs at seed time).
   - Ports already in use after a restart? Free them: `fuser -k 8000/tcp; fuser -k 3000/tcp`

2. **Frontend:** Start from the `frontend/` directory:
   ```bash
   cd /home/ubuntu/repos/atlas/frontend
   npm install --no-audit --no-fund  # if deps not installed
   npm run dev &
   ```

3. **Register a test user:** Navigate to `http://localhost:3000/login`, switch to Register mode, and create:
   - Full name: `Test Trader`
   - Email: use a proper domain like `tester@atlas.example.com` (NOT `.local` — Pydantic rejects reserved TLDs)
   - Password: `password123!` (min 8 chars)

## Test Flow

### Test 1: Curated panel appears on sugar opportunity
- Navigate to `/opportunities` → click "+ New opportunity"
- Fill: Title (any), Commodity = "sugar" (default), Volume = any number, Destination country = any, Incoterms = CFR
- Submit → navigate to the opportunity workspace
- **Assert:** A green-bordered panel appears with:
  - Header: "Curated counterparties · vetted for sugar"
  - Subtext: "5 pre-vetted desks for this lane. 5 not yet attached."
  - Button: "+ Add all curated (5)"
  - Toggle: "show" button

### Test 2: Expanded view shows all 5 desks
- Click "show" on the curated panel
- **Assert:** 5 entries visible:
  - Copersucar S.A. (Brazil · producer co-op / trader)
  - Alvean (Brazil · trading house) — description mentions "2023 restructure" and "Copersucar"
  - Raízen (Brazil · producer / trader)
  - Sucden Brazil (Brazil · merchant trader)
  - Czarnikow Brazil (Brazil · merchant trader / broker)
- Each has a website link and individual "add" button

### Test 3: One-click seeding
- Click "+ Add all curated (5)"
- **Assert:**
  - "Supplier leads (5)" header
  - Panel subtext → "All already added."
  - Seed button disappears
  - 5 rows in supplier leads table

### Test 4: Idempotency
- After seeding, all entries in the expanded panel show "added" (not "add" button)
- **Assert:** Exactly 5 supplier leads — no duplicates

### Test 5: Curated badge on AI Discover
- Navigate to `/suppliers`
- Set Country = "Brazil", Commodity = "sugar"
- Click "AI Discover"
- **Assert:** The 5 curated suppliers each have a green "Curated" badge next to their name
- **Note:** The UI re-fetches from DB after discover, so curated entries might not appear first in the table (they appear first in the API response but the UI sorts by DB insertion order). The badge is the key indicator.

### Test 6: Hunter.io contact enrichment (new "Contact" column)
- After seeding curated suppliers (Test 3), the supplier-leads table has a **Contact** column between "Name / country" and "Stage".
- **Assert (mock mode, no key):** All 5 leads show a `mailto:` link reading "Trade Desk" with subtitle "Trading Desk". Emails are `trading@{domain}` (e.g. `trading@copersucar.com.br`, `trading@alvean.com`).
- **Assert (live mode, real `HUNTER_API_KEY`):** Each lead shows a **real named contact** (e.g. "Gabriel Carvalho" / "Head of Trading" for Alvean), a real email, and a title subtitle. Live contacts differ from the mock `trading@` pattern — that difference is the proof the live API ran.
- **Verify live API calls in backend logs:** `grep -i "hunter\|domain-search" <backend log>` should show 5 `GET https://api.hunter.io/v2/domain-search?domain=...` lines, all `HTTP/1.1 200 OK`, followed by `POST /curated-suppliers/seed ... 201 Created`.
- **Trading-role preference:** `_pick_best()` in `app/services/hunter.py` favors contacts whose position contains trad/sales/commercial/export. In live mode at least the trading houses (Alvean, Sucden) typically resolve to a "Head of Trading"-type role.
- **Persistence + idempotency:** Reload the page (F5) → same 5 contacts persist (committed to DB), panel still reads "All already added", no duplicate leads.
- **"no contact" indicator:** Leads with no email render italic gray "no contact" in the Contact column. Enrichment is best-effort — a domain returning no emails or an API error leaves the lead created but with "no contact"; the seed flow never breaks.

### Test 7: Negative case — non-sugar commodity
- Create an opportunity with Commodity = "iron ore"
- Navigate to the opportunity workspace
- **Assert:** No "Curated counterparties" panel appears

## Common Pitfalls

- **Email validation:** Don't use `.local` TLD for registration emails — Pydantic's email validator rejects reserved/special-use domains. Use `.example.com` instead.
- **Discover ordering:** The backend prepends curated entries in the API response, but the Suppliers page re-fetches all suppliers from the DB after discover. DB sorts by ID, so curated entries might not appear at the top of the UI table. Focus on verifying the "Curated" badge rather than position.
- **Mock LLM:** With `LLM_PROVIDER=mock`, AI Discover returns 3 deterministic mock results plus the curated entries. No API keys needed.
- **Frontend deps:** If `npm install` fails on `tsc@2.0.4`, try `npm install --no-audit --no-fund` which typically resolves the issue.
- **Hunter mock vs live:** Without `HUNTER_API_KEY`, contacts are always the deterministic mock set (`trading@{domain}` / "Trade Desk"). If you see those exact values, you're in mock mode — set the key and re-seed to test the live path. Enrichment runs **only at seed time**, so changing the key requires re-seeding (a fresh opportunity or fresh DB).
- **Hunter free tier:** 25 searches/month; each "Add all curated (5)" consumes 5 live searches. Don't burn quota by repeatedly re-seeding in live mode — verify once, then use mock mode for regression.
- **Unrelated 429s:** The dashboard's Yahoo Finance price widget often logs `429 Too Many Requests` and falls back to Stooq. This is independent of Hunter enrichment — don't flag it as a Hunter failure.
