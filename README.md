# KP ETSIP — platform foundation

The current slices provide local sign-in, profile preferences, portfolio and application inventory, and executive insight. MongoDB is required; there is no in-memory fallback.

## Local development

1. Start MongoDB at `mongodb://127.0.0.1:27017` (or supply `MONGODB_URI` and `MONGODB_DB`). Copy `backend/.env.example` to `backend/.env`. The explicit development signing key and seed passwords are for local use only. Initial administrator: `admin@ketsip.local` / `DevAdmin-Change123!`. Other local accounts are `executive`, `leader`, `engineer`, `auditor`, `readonly` at `@ketsip.local`, with `DevDemo-Change123!`. These passwords are configurable through `SEED_ADMIN_PASSWORD` and `SEED_DEMO_PASSWORD`; seed runs only when enabled and never overwrites existing records. The administrator must change its password on first login.
2. From `backend`: create a venv, `pip install -r requirements.txt`, then `uvicorn app.main:app --reload`. Config is loaded from `.env` in that directory. Liveness: `/api/health`.
3. From `frontend`: `npm install` and `npm run dev`. Vite proxies `/api` to `http://127.0.0.1:8000`; set `VITE_PROXY_TARGET` to change it. Browser API base defaults to same origin.
4. Alternatively, copy `backend/.env.example` to `backend/.env`, then run `docker compose up --build`; browse `http://localhost:8080`. The named Mongo volume preserves data between restarts. Do not expose development MongoDB publicly.

For production, use HTTPS, a protected MongoDB instance, a randomly generated unique `JWT_SECRET` of at least 32 characters, strong unique seed passwords, and `ENVIRONMENT=production`; the service rejects known development credentials. Disable seeding after provisioning. Do not use the sample secrets. Restrict access to environment values and rotate secrets securely. Backend CORS is configured with `CORS_ORIGINS` (comma-separated origins). The current token is stored in localStorage; protect the application against XSS. Password changes and sign-out invalidate all previously issued tokens for that user.

## API

`POST /api/auth/login` accepts `{email,password}` and returns `{token,user}`. `GET /api/auth/me` requires a bearer token and returns public profile only. `POST /api/auth/password` accepts `{currentPassword,newPassword}` and returns a replacement token and user. Passwords must be 12–72 UTF-8 bytes; the current password must differ. `PUT /api/auth/landing` accepts `{defaultLandingPage: "dashboard" | "applications"}`. `POST /api/auth/logout` invalidates sessions. Before an administrator changes its initial password only profile, password change and logout are available. Each request verifies the current user is active. Login attempts and successful profile changes are audited; no credentials are written to audit records. Future protected business routes must depend on `unrestricted_user` to enforce the initial-password gate; `/me`, password change, logout and health are intentionally exempt.

## Portfolios

`GET /api/portfolios` accepts `search`, `status`, `productArea`, `limit` (1–100) and `offset`; it returns `{items,total,canCreate}`. Product-area filtering selects portfolios containing an application in that area. `POST /api/portfolios`, `GET`/`PUT`/`DELETE /api/portfolios/{id}` and `GET /api/portfolios/{id}/applications` use bearer authentication. Names are unique; linked applications block deletion. Counts and rounded quality averages are calculated from applications, with null for no score. Administrators alone create or delete portfolios, and leaders may edit assigned portfolios; current role permissions must also grant the operation. Leaders, engineers and read-only users see only assignments. The seed inserts three portfolios and ten illustrative applications, and assigns the first portfolio to newly seeded scoped demo users without overwriting existing accounts. Application links navigate to application details.

## Executive dashboard

`GET /api/dashboard/executive` requires the current role's `view` permission and applies portfolio scope. Optional `portfolioId`, `productArea`, and `riskLevel` filter the applications; `limit` (1–100, default 25) and `offset` paginate only the portfolio breakdown, while scalar totals cover all matches. Without application filters, visible empty portfolios count; with product-area or risk filters, only portfolios containing matches count. Averages are rounded to the nearest integer and return null when no scores exist. The dashboard accepts a portfolio ID (available in portfolio URLs), product area, and risk level as filters.

## Tests

With the backend dependencies installed and MongoDB running: `cd backend && PYTHONPATH=. "$HOME/venvs/app/bin/python" -m pytest -q tests/test_auth.py`. Tests create and drop a uniquely named disposable database. Portfolio API tests: `cd backend && PYTHONPATH=. "$HOME/venvs/app/bin/python" -m pytest -q tests/test_portfolios.py`. Dashboard API tests: `cd backend && PYTHONPATH=. "$HOME/venvs/app/bin/python" -m pytest -q tests/test_dashboard.py`. Frontend component tests: `cd frontend && npm test`; typecheck and build: `cd frontend && npm run build`.

For E2E, run backend and frontend against a **fresh disposable** Mongo database with seeding enabled. From `frontend`, set `E2E_ADMIN_PASSWORD` to its initial administrator password, then run `node node_modules/@playwright/test/cli.js test`. The journeys change the seeded password, landing preference and portfolio description and therefore must not be run against valuable data. Set `E2E_DEMO_PASSWORD` if the seeded demo password differs from the development default. Install Playwright Chromium first if needed: `node node_modules/@playwright/test/cli.js install chromium`.
