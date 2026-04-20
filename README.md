# Atlas Trade OS

> AI-assisted, API-driven platform that enables commodity traders to systematically source, structure, and execute trades while maintaining full control over relationships, pricing, and risk.

This repository contains the initial MVP of Atlas Trade OS — an AI-native
commodity trading operating system built per the project PRD.

## Status

Initial MVP covering all five core modules end-to-end:

| # | Module | Status |
|---|--------|--------|
| 1 | Supplier Discovery | Service + scraper skeleton + AI-backed extraction |
| 2 | Counterparty Intelligence | LLM classification + rule-based risk scoring |
| 3 | Deal Structuring | Pricing / margin / scenario engine |
| 4 | Document Generation | NCNDA, SPA, LOI, FPA, IMFPA, outreach (MD + DOCX) |
| 5 | Deal Pipeline / CRM | Stage state machine, activity log, tasks |
| 6 | Market Reference Prices | Yahoo Finance live futures (sugar, wheat, corn, coffee, cocoa, cotton, soy, oil, gold) with 5-min cache |

Deliberately deferred (clearly marked as `TODO` in code):

- Live integrations with Clearbit / OpenCorporates / MarineTraffic
- Production-grade Playwright scraping (anti-bot, proxies)
- Neo4j relationship graph
- GraphQL
- Advanced RBAC

## Market price feed (Yahoo Finance)

The `/api/v1/prices/{commodity}` endpoint returns the latest futures quote for a
commodity, converted to `USD/MT` where the exchange unit differs (cents/lb for
soft commodities, cents/bushel for grains). No API key required.

Ticker map: sugar→`SB=F` (ICE), wheat→`ZW=F`, corn→`ZC=F`, soybeans→`ZS=F`
(CBOT), coffee→`KC=F`, cocoa→`CC=F`, cotton→`CT=F` (ICE), crude_oil→`CL=F`
(NYMEX), gold→`GC=F` (COMEX). Prices are cached in-memory for 5 minutes per
ticker. The dashboard and each deal's pricing card show a live quote with a
`vs buy` / `vs sell` deviation badge so traders can sanity-check quoted
numbers against the market.

## Architecture

```
Atlas Trade OS
├── backend/          FastAPI + SQLAlchemy (async) + Alembic
│   ├── app/
│   │   ├── api/v1/   REST endpoints (auth, suppliers, deals, documents, pipeline)
│   │   ├── core/     Config, DB, security (JWT)
│   │   ├── models/   SQLAlchemy models
│   │   ├── schemas/  Pydantic schemas
│   │   ├── services/ Business logic per module
│   │   ├── ai/       LLM abstraction (OpenAI / Anthropic / Mock)
│   │   ├── scrapers/ Web scraping (Playwright skeleton)
│   │   └── integrations/ Third-party provider stubs
│   └── tests/        Pytest suite (22 tests, mock LLM)
└── frontend/         Next.js 14 (App Router) + Tailwind CSS
    ├── app/          Pages: /login, /dashboard, /suppliers, /deals, /pipeline
    ├── components/   AppShell + shared UI
    └── lib/          API client, auth context, formatters
```

## Quickstart

### With Docker (one command)

```bash
docker compose up --build
```

- Backend: http://localhost:8000 — OpenAPI docs at http://localhost:8000/docs
- Frontend: http://localhost:3000

Create an account at `/login` (registration is open by default in MVP).

### Local development without Docker

**Backend:**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ai]"
cp .env.example .env   # default LLM_PROVIDER=mock, DATABASE_URL=sqlite
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

### Environment variables

See [`backend/.env.example`](backend/.env.example). Highlights:

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./atlas.db` | Use Postgres in production: `postgresql+asyncpg://...` |
| `LLM_PROVIDER` | `mock` | `openai`, `anthropic`, or `mock` |
| `OPENAI_API_KEY` | — | Required if `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | — | Required if `LLM_PROVIDER=anthropic` |
| `APP_SECRET_KEY` | `dev-secret-change-me` | **Must be set in production.** |

The default `mock` LLM provider is deterministic and works offline — the whole
system is testable without any API keys.

## Testing

```bash
cd backend
pytest -q
```

22 tests cover:
- Pricing engine (margins, structure recommendations, scenarios)
- Pipeline state machine (allowed transitions, terminal stages)
- Counterparty risk scoring (email/website checks, red flags)
- API smoke tests for all five modules end-to-end (auth, discovery,
  deal lifecycle, document generation + DOCX export)

Frontend type-check + build:

```bash
cd frontend
npm run typecheck && npm run build
```

## API overview

All endpoints live under `/api/v1` and require `Authorization: Bearer <jwt>`
(obtained via `POST /api/v1/auth/login` or `/register`).

| Group | Endpoints |
|-------|-----------|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` |
| Suppliers | `GET/POST /suppliers`, `GET/PATCH/DELETE /suppliers/{id}`, `POST /suppliers/discover`, `POST /suppliers/{id}/classify` |
| Deals | `GET/POST /deals`, `GET/PATCH/DELETE /deals/{id}`, `POST /deals/{id}/stage`, `POST /deals/structure` |
| Deal activity | `GET/POST /deals/{id}/activity`, `GET/POST /deals/{id}/tasks`, `PATCH /deals/tasks/{id}` |
| Documents | `GET/POST /documents/generate`, `GET/PATCH/DELETE /documents/{id}`, `GET /documents/{id}/export.{md,docx}` |
| Pipeline | `GET /pipeline/board`, `GET /pipeline/stats` |

Full OpenAPI schema at `http://localhost:8000/docs` when running.

## Design philosophy

- **Human-in-the-loop** — every AI output (classification, document, pricing
  recommendation) is editable. AI never makes final decisions.
- **Modular** — each of the 5 PRD modules lives in its own service; adding
  a new LLM provider, commodity-price source, or scraper is a drop-in.
- **Offline-friendly** — the default `mock` LLM provider returns deterministic,
  plausible outputs so the full app (and CI) runs without any API keys.
- **Execution-focused** — UI is minimal. Every screen supports closing deals.

## Roadmap (per PRD §11)

**Phase 2 follow-ups** (not shipped in this MVP):

- Real Playwright-backed supplier scraping (LinkedIn, trade directories)
- Wire live commodity price providers (Commodities API / Twelve Data)
- Company enrichment (Clearbit / OpenCorporates / Apify)
- Vector embeddings for supplier similarity search (pgvector or Pinecone)
- RBAC beyond the current `role` column
- Audit log UI
- Email + Slack notifications for stage changes and task reminders
