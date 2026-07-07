# Support Chat Admin (React Admin)

A [react-admin](https://marmelab.com/react-admin/) SPA over the FastAPI support
chat admin API (`/admin/*`). Vite + React 18, JavaScript only, MUI (bundled
with react-admin), dark mode by default, no SSR.

## What's inside

| View | Backend endpoints |
| --- | --- |
| Dashboard (KPI cards + by-topic / by-language) | `GET /admin/overview`, `/admin/by-topic`, `/admin/by-language` |
| Conversations (list + full message thread) | `GET /admin/sessions`, `GET /admin/session/{id}` |
| Escalations / unresolved queue (read-only) | `GET /admin/unresolved` |
| Knowledge base (topics + KB content editor) | `GET/POST /admin/kb/topics`, `GET/PUT/DELETE /admin/kb/content` |
| KB variables (`{placeholder}` registry) | `GET /admin/kb/variables`, `PUT /admin/kb/variables/{key}` |
| Users (admin accounts, roles, passwords) | `GET/POST /admin/users`, `PUT/DELETE /admin/users/{email}` |
| Prompt preview (read-only assembled prompt) | `GET /admin/effective-prompt` |
| Prompt variables + escalation keywords + test player | `GET/PUT /admin/prompt-variables`, `PUT /admin/settings/escalation`, `GET/PUT /admin/test-profile` |
| Translations (player-facing copy per language, incl. `contact_url`) | `GET/PUT /admin/translations` |
| Settings (runtime groups, JSON editors) | `GET /admin/settings`, `PUT /admin/settings/{key}` |
| Structure (partners → products, widget keys + embed snippet, write-only secrets) | `GET /admin/structure`, `POST/PUT /admin/partners*`, `POST/PUT /admin/products*`, `PUT /admin/products/{id}/secrets`, `POST /admin/products/{id}/widget-key` |
| Retention · Telegram (config, retention KB, media, managers, analytics) | `/admin/retention/*` |

The AppBar carries the **Partner → Product switcher** (fed by
`GET /admin/structure`); the selection persists in localStorage and every
scoped request sends it as `product_id`/`partner_id`. "All products" = the
backend default (default product for editors, everything readable for
dashboards).

Notes on how this maps to the backend:

- Lists come back as `{items: [...], total: N}` (sessions) or wrapped arrays
  (`{topics}`, `{users}`, …); `src/dataProvider.js` translates both shapes for
  react-admin. Sessions are paginated server-side (25/page); the smaller lists
  are paginated client-side.
- There is no separate escalations table in the backend — a hard escalation
  closes the session. The "Escalations" view is the backend's *unresolved
  queue* (escalated + abandoned open sessions) and is read-only.
- Auth is the real backend login: `POST /admin/login` with **email + password**
  returns a JWT, sent as `Authorization: Bearer`. Roles: `admin` writes,
  `manager` is read-only (the server enforces this; write attempts get 403).
  For development you can instead set `VITE_ADMIN_TOKEN` to a pre-issued JWT
  and skip the login form.

## Deployment (default: single service)

The repo's two-stage `Dockerfile` builds this app (node stage → `admin/dist`)
and the FastAPI service serves it at **`/admin`**, same-origin with the
`/admin/*` JSON API — so in production there is nothing to deploy separately,
no CORS to configure for the admin, and no `VITE_API_URL` to set (the SPA uses
relative URLs). Just deploy the backend as usual and open `https://<host>/admin`.

A separate static deploy still works if you prefer it: `npm run build` with
`VITE_API_URL` pointing at the backend, host `dist/` anywhere (note the build
uses `base: '/admin/'`, so serve it under an `/admin` path), and add the
static site's origin to the backend's `CORS_ALLOW_ORIGINS`.

## Install

```bash
cd admin
npm install
```

## Development

```bash
cp .env.example .env      # set VITE_API_URL to your backend, e.g. http://localhost:8080
npm run dev               # http://localhost:5173/admin/
```

CORS: for `npm run dev` the backend must allow the dev origin
(`CORS_ALLOW_ORIGINS=http://localhost:5173` on the FastAPI service).

## Build

```bash
npm run build             # emits static files to admin/dist
```

## Environment variables

| Var | Required | Description |
| --- | --- | --- |
| `VITE_API_URL` | dev only | Base URL of the FastAPI backend, no trailing slash (e.g. `http://localhost:8080`). Leave unset when the FastAPI service serves the build itself (same-origin relative URLs). |
| `VITE_ADMIN_TOKEN` | no | Pre-issued admin JWT; when set, the login form is skipped and this token is used for every request (dev convenience only — it is baked into the bundle, never use in production) |
