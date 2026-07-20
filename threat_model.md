# Threat Model

## Project Overview

A personal finance management application for a single user in the Dominican Republic. The system consists of:

- **Express 5 API server** (`artifacts/api-server/`) serving financial data stored in `finance_data.json` and proxying image analysis to the OpenAI API.
- **React frontend** (`artifacts/finanzas-web/`) for viewing income, expenses, debts, and credit card balances.
- **Streamlit web app** (`artifacts/finanzas-web/app.py`) providing an alternative UI backed by a separate `finanzas/datos.json` file.
- **CLI Python app** (`finanzas/finanzas.py`) for terminal-based management.

Stack: Node.js 24 / TypeScript 5.9, Express 5, Python 3.12, Streamlit, PostgreSQL + Drizzle ORM (workspace-level, not yet wired into the API), pnpm workspaces. Deployed on Replit autoscale. Not currently deployed (`isDeployed: false`).

## Assets

- **Personal financial data** — real bank account names with partial card numbers, balances (e.g., RD$80,949 in one account), credit card balances, debt records, daily expenses. Stored in `finance_data.json` and `finanzas/datos.json`.
- **OpenAI API key** — configured via `OPENAI_API_KEY` environment variable. Compromise enables unlimited cost-generating API calls.
- **Financial history** — full income, expense, debtor, and credit card payment history. Exposure reveals spending patterns and financial health.

## Trust Boundaries

- **Browser to API** — The React frontend calls the Express API. The API currently applies no authentication; all endpoints are public to anyone who can reach the server.
- **API to OpenAI** — The server proxies image analysis requests to OpenAI using the server-stored `OPENAI_API_KEY`. No caller authentication or rate limiting protects this proxy.
- **API to Filesystem** — Financial data is stored as JSON files on the server filesystem. The PUT endpoint accepts arbitrary JSON bodies and writes them without validation.
- **Public / Authenticated boundary** — Currently absent. There is no authentication layer.

## Scan Anchors

- **Production entry points**: `artifacts/api-server/src/routes/finanzas.ts` (GET /api/finanzas, PUT /api/finanzas, POST /api/analizar-baucher), `artifacts/api-server/src/app.ts` (CORS, middleware).
- **Highest-risk areas**: `finanzas.ts` route file — unauthenticated data read/write + OpenAI proxy; wildcard CORS configuration in `app.ts`.
- **Public surfaces**: All `/api/*` routes are fully public with no authentication.
- **Dev-only**: `finanzas/finanzas.py` CLI (terminal only), `artifacts/finanzas-web/app.py` Streamlit app (separate port).

## Threat Categories

### Spoofing / Authentication

No authentication exists on any API endpoint. Any actor that can reach the API server URL can read all financial data or overwrite it entirely. There is no session, token, or identity check. All `/api/*` endpoints MUST require authentication before this application is deployed publicly.

### Tampering

The `PUT /api/finanzas` endpoint writes `req.body` directly to the filesystem with no schema validation. An attacker can overwrite the entire financial dataset with arbitrary content, including invalid or malformed data that corrupts the application state. The endpoint MUST validate the request body against the expected schema before writing.

### Information Disclosure

The `GET /api/finanzas` endpoint returns the complete financial dataset — real account names with partial card numbers, all balances, full expense and debt history — to any unauthenticated caller. Error responses from the OpenAI proxy use `String(err)` which may expose internal file paths or Node.js error details.

### Denial of Service / Cost Abuse

The `POST /api/analizar-baucher` endpoint has no rate limiting, no authentication, and no request body size validation beyond Express's default 100 KB JSON limit. Any party that discovers the endpoint URL can send unlimited requests, exhausting the OpenAI API key quota and incurring unbounded financial costs against the user's OpenAI account.

### Elevation of Privilege

Not applicable in the current single-user model, but the OpenAI API key functions as a privileged credential — unauthenticated access to the proxy endpoint is functionally equivalent to stealing the key for cost purposes.
