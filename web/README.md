# OpenQuant — Web

The Next.js 14 (App Router) front end for **OpenQuant**, an original, open-source
reference implementation of a quant-research SaaS architecture:

- **Data** — market + reference data served by the engine over a typed API.
- **Cloud compute** — per-project machines that run backtests.
- **AI research copilot** — an agent service that reasons over your data and runs.

This is a clean-room reference project. It contains no branding, copy, or code
from any existing product. Every integration point with a paid or external
service is marked with a `// SEAM:` comment so they are easy to find
(`grep -rn "SEAM:" lib app`).

## Architecture map

| Area | Where it lives | Backing service (seam) |
| --- | --- | --- |
| Auth / sessions | `middleware.ts`, `lib/auth.ts`, `/sign-in`, `/sign-up` | Clerk |
| Billing / entitlements | `lib/stripe.ts`, `/api/is-paid`, `/api/create-portal-session`, `/subscribe` | Stripe |
| Analytics | `lib/posthog.ts`, `app/providers.tsx` | PostHog |
| Market data + backtests | `lib/data-client.ts`, `/api/projects/[id]/*`, `/charts`, `/data` | Engine (`DATA_API_URL`) |
| AI copilot + memory | `lib/agent-client.ts`, `/quant-copilot`, `/api/memory/*`, `/api/slash-commands` | Agent service (`AGENT_SERVICE_URL`) |
| MCP tool surface | `/api/mcp` | Engine + agent (token-authed) |

The engine itself lives in `../engine` and is owned by a separate process; the
web app only talks to it over HTTP via `lib/data-client.ts`.

## Routes

Public: `/`, `/subscribe`, `/data`, `/sign-in`, `/sign-up`, `/post-sign-in`,
`/legal/*`.

Gated app shell (`app/(app)/`, wrapped by a sidebar layout + `requireUser`):
`/projects`, `/projects/[projectId]`, `/quant-copilot`,
`/quant-copilot/[sessionId]`, `/charts`, `/social`, `/social/[id]`,
`/u/[handle]`, `/profile`, `/admin`.

API route handlers (`app/api/`): `pricing-tiers`, `is-paid`, `usage`,
`create-portal-session`, `memory` (+ `memory/[id]`, `memory/settings`),
`projects/[id]/machine`, `projects/[id]/run-target`, `slash-commands`,
`notifications`, `me/admin`, `tour/create-qcp-session`, `mcp`.

Gating model: `middleware.ts` allowlists the public routes and protects the
rest; each gated API handler additionally calls `requireUser()` (or
`requirePaidUser()`) from `lib/auth.ts`, which maps to 401/402 via `lib/http.ts`.

## Getting started

```bash
cd web
cp .env.example .env.local   # fill in credentials (see below)
npm install
npm run dev                  # http://localhost:3000
```

Scripts: `dev`, `build`, `start`, `lint`.

## Seams that need real credentials

The skeleton runs with placeholders but these must be wired up for real
functionality (all listed in `.env.example`):

- **Clerk** — `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`.
- **Stripe** — `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID` (and webhook secret);
  replace the stub bodies in `lib/stripe.ts` with real Stripe SDK calls and the
  stub in `lib/auth.ts#getEntitlement` with a real subscription lookup.
- **PostHog** — `NEXT_PUBLIC_POSTHOG_KEY`, `NEXT_PUBLIC_POSTHOG_HOST`.
- **Engine / data API** — `DATA_API_URL`, `DATA_API_TOKEN` (`lib/data-client.ts`).
- **Agent service** — `AGENT_SERVICE_URL`, `AGENT_SERVICE_TOKEN`
  (`lib/agent-client.ts`).
- **MCP** — `MCP_TOKEN` (falls back to the agent token) for `/api/mcp`.

## Notes

- Shared domain types live in `lib/types.ts` and use `zod` schemas, which both
  the client fetch wrappers and the API handlers validate against — keep them in
  sync with the engine's output shapes.
- This project is intentionally not wired to live services; it type-checks in
  principle (`npx tsc --noEmit`) once dependencies are installed.

## License

MIT. Original work; not derived from any existing product.
