# PR #4 Test Plan — V2 Opportunity Workspace UI

**PR:** https://github.com/d1t/atlas/pull/4
**Session:** https://app.devin.ai/sessions/6f9ab2c3bbdb437ab1aed856307fd8fd
**Scope:** Prove the primary orchestration flow end-to-end (create opp → add leads → matches/health/next-actions → promote → deal). Verify the stale-error-banner fix. No regression sweep.

## Environment
- Backend: `uvicorn app.main:app` on `127.0.0.1:8000`, fresh `atlas.db`, LLM=mock (no AI cost, deterministic output)
- Frontend: `next dev` on `:3000`
- Browser: Chrome (CDP on :29229), maximized via `wmctrl`

## Test user
- Register via `/login` (mode: Register) → email `tester@atlas.local`, password `password123!`, name `Test Trader`

## Canonical test opportunity
Fields chosen so the math is unambiguous and every assertion falsifiable:

| Field | Value |
|---|---|
| Title | Nigeria sugar 50k MT CFR Lagos |
| Commodity | sugar |
| Volume | 50000 MT |
| Destination | Lagos, Nigeria |
| Target min/max | 520 / 560 $/MT |
| Incoterms | CFR |

## Lead matrix

**Suppliers** (3): price is the only variable that matters for margin; credibility/responsiveness affect score.

| # | Name | Origin | Price $/MT | Credibility | Responsiveness |
|---|---|---|---|---|---|
| S1 | Copersucar | Brazil | 480 | 80 | 80 |
| S2 | Nordzucker | Germany | 520 | 70 | 70 |
| S3 | Mitr Phol | Thailand | 550 | 60 | 60 |

**Buyers** (2):

| # | Name | Country | Target $/MT | Appetite | Urgency |
|---|---|---|---|---|---|
| B1 | Dangote Refineries | Nigeria | 580 | high | high |
| B2 | BUA Foods | Nigeria | 560 | medium | medium |

Expected pairings (6 = 3 × 2). Margin per MT = buyer_target − supplier_price.

| Pair | Margin/MT |
|---|---|
| S1 × B1 | **100** (top) |
| S1 × B2 | 80 |
| S2 × B1 | 60 |
| S2 × B2 | 40 |
| S3 × B1 | 30 |
| S3 × B2 | 10 |

## Adversarial assertions (all must pass; any fail = red flag)

### A. Empty-state next actions (before any leads)
- **A1** — Workspace loads without error banner
- **A2** — Health badge reads `0` or near-zero
- **A3** — Next-actions panel contains literal text `Source 3 more suppliers` (priority high)
- **A4** — Next-actions panel contains literal text `Engage 2 more buyers` (priority high)
- **A5** — Matches table shows empty state (no rows)

### B. Lead panels accept data
- **B1** — Add supplier S1 → row appears with name `Copersucar`, price `$480`, country `Brazil`
- **B2** — Add all 3 suppliers → panel shows 3 rows
- **B3** — Add both buyers → panel shows 2 rows
- **B4** — No JS errors in console after lead additions (sampled)

### C. Matches table is correct
- **C1** — Matches panel shows **exactly 6 rows** (3 × 2 combinations)
- **C2** — Rows sorted by score DESC; the first row's supplier × buyer pair is `Copersucar × Dangote Refineries`
- **C3** — Top row margin-per-MT displays `$100` (or `100/MT`)
- **C4** — Top row score ≥ 70 (high margin + high credibility/responsiveness)
- **C5** — Each row has at least one rationale bullet

### D. Health score reacts
- **D1** — After 3 suppliers + 2 buyers added, health score > 50 (coverage + price alignment both positive)
- **D2** — Health panel shows 5 factor bars, at least one labelled `suppliers` / `buyers` / `price` (substring match)

### E. Next-actions update on populated state
- **E1** — `Source 3 more suppliers` no longer shown (at 3 active suppliers, shortfall = 0)
- **E2** — `Engage 2 more buyers` no longer shown (at 2 active buyers, shortfall = 0)
- **E3** — At least one action remains (engine guarantees ≥1 always)

### F. Promote to Deal (the critical smoke test)
- **F1** — Top match row's **Promote** button is enabled (margin > 0, score > 0)
- **F2** — Click Promote → confirm dialog appears → accept
- **F3** — Browser URL changes to `/deals/<id>`
- **F4** — Deal page shows `buy_price = $480`, `sell_price = $580`, `volume = 50,000 MT`, `margin_per_mt = $100`
- **F5** — Deal page does not show any error banner

### G. Error-banner fix (from Devin Review finding)
- **G1** — Navigate to `/opportunities/99999` (non-existent ID) → error banner appears
- **G2** — Navigate back to a valid opportunity → error banner disappears (does not persist)

## Out of scope
- Regression on `/deals`, `/suppliers`, `/pipeline` legacy pages
- Gmail integration (Phase 4, separate PR)
- Mobile responsiveness
- Real LLM outputs (using mock for deterministic testing)
- Performance / load testing

## Deliverable
- One comment on PR #4 with collapsed `<details>` sections, pass/fail bullet list, screenshots of any failures, link to this session
- Attached screen recording (`.webm`) of the full flow with structured annotations
