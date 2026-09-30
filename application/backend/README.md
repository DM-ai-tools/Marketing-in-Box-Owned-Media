# Marketing-in-a-Box — Backend

FastAPI orchestrator for the Marketing-in-a-Box DAG pipeline (L2/L3 in the 5-Layer Engine):
dependency-aware asset generation via the Anthropic Messages API, with a human-in-the-loop
review gate. See `docs/Marketing_in_a_Box_Session_Context.md` and
`docs/Conversational_Intake_Engine_Design.md` at the repo root for the full architecture and
field-schema spec this service implements.

## Status

Skeleton only. `app/db/` (SQLAlchemy models) and `alembic/` (migrations) are intentionally left
empty here — they are owned by a separate database-architect pass to avoid merge collisions.

## Requirements

- Python 3.11+
- Postgres (for `DATABASE_URL`)
- Redis (for `REDIS_URL` — Celery broker/backend and short-TTL draft storage)
- An Anthropic API key (for `ANTHROPIC_API_KEY`)

## Setup

```bash
cd application/backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # then fill in real values — never commit .env
```

## Run the API

```bash
python -m app --reload
```

The app starts on http://127.0.0.1:8001 — the port the frontend's Vite dev server proxies `/api/*`
to (see `application/frontend/vite.config.ts`). Health check:

```bash
curl http://127.0.0.1:8001/health
```

Interactive API docs (Swagger UI) are auto-served at http://127.0.0.1:8001/docs.

Use `python -m app`, not a bare `uvicorn app.main:app`: on Windows, uvicorn ≥ 0.36 builds a
ProactorEventLoop for a single-process server, and psycopg's async mode cannot run on one — every
database-backed route would fail with a 500 while `/health` and `/docs` kept answering 200. The
launcher in `app/__main__.py` picks a compatible loop; `app.main` also refuses to start on a bad
one rather than serving a half-broken API.

`uvicorn app.main:app --reload` gets the *loop* right (its reload path runs the app in a
subprocess, which picks a selector loop) but not the *port* — uvicorn's own default is 8000, and
the frontend proxies `/api/*` to 8001, so every request from the browser fails at the proxy and
the UI reports "Couldn't load chat history" while `curl http://127.0.0.1:8000/health` looks fine.
If you do launch it that way, pass `--port 8001`, or point the frontend at it with
`VITE_API_TARGET=http://127.0.0.1:8000 npm run dev`.

## Authentication

The app sits behind a sign-in gate (`app/routers/auth.py`, `app/services/auth.py`): email and
password against this project's own Postgres. There is no third-party/OAuth sign-in.

Adds no dependencies — passwords use stdlib `hashlib.scrypt`, so there is no bcrypt/passlib/JWT
wheel to install or pin.

### Configuration (`.env`)

| Variable | Required | Notes |
|---|---|---|
| `APP_BASE_URL` | no | The **frontend** origin a password-reset link points at. Defaults to `http://localhost:5173`. |
| `MAIL_FROM` | for sending mail | From address, e.g. `Marketing-in-a-Box <noreply@yourdomain>`. Required together with a transport. |
| `RESEND_API_KEY` | no | Preferred transport (HTTPS). Used when set; SMTP is ignored if this is present. |
| `SMTP_HOST` | no | Fallback transport when Resend is unset. |
| `SMTP_PORT` | no | Defaults to `587`. Use `465` for implicit TLS. |
| `SMTP_USER` / `SMTP_PASSWORD` | no | SMTP auth, if the host requires it. |
| `SMTP_STARTTLS` | no | Default on. Set `0` only for a local unencrypted relay. |

The password floor (6 characters) is served to the form by `GET /auth/config`, so the rule the UI
enforces is always the rule the API enforces.

Mail is sent from `app/services/mail.py` only. `deliver_password_reset` in `app/services/auth.py`
calls it; token issuing, expiry, single-use redemption, and session revocation do not change.

### Password reset

`POST /auth/forgot-password` issues a real, single-use, one-hour token. If Resend or SMTP is
configured, the link is emailed and **not** written to the log (it is a live credential). If neither
is configured, the URL is logged at WARNING so local development still works — copy it from the
server log and open it.

A send failure still returns the same 200 as a success ("if an account exists…"), so a down mailer
cannot be used to enumerate which addresses are registered.

### Where credentials live

| Table | Holds |
|---|---|
| `users` | email (lowercased, unique), `password_hash`, name, `email_verified` |
| `user_sessions` | one row per signed-in browser; SHA-256 of the cookie value |
| `password_reset_tokens` | SHA-256 of the reset token, 1 hour, single-use |

Nothing is stored recoverably: the password is scrypt (`scrypt$n$r$p$salt$digest`, cost carried in
the hash so it can be raised later without invalidating existing passwords), and both token tables
hold only a digest. A dump of this schema cannot be replayed as a login.

### Notes

- The session is an **httpOnly cookie** (`miab_session`), not a token the frontend holds. This app
  renders model-authored HTML, so an XSS-readable credential in `localStorage` would turn one bad
  string into a stolen account.
- Sessions are rows, not JWTs, so sign-out actually revokes. A password reset signs out every
  *other* browser.
- The pipeline routes (`/pipeline`, `/chat-sessions`, `/usage`) are **not yet per-user** — that is a
  data-model change, since `chat_sessions`/`runs`/`context_entries` have no owner column. The
  dependency to attach them to already exists: `Depends(current_user)` in `app/routers/auth.py`.

## Run tests

```bash
pytest
```

## Project layout

```
app/
  main.py            FastAPI app entrypoint + health check route
  routers/            API route modules (empty package — populated per feature)
  services/           Business/service-layer logic (empty package — populated per feature)
  db/                 SQLAlchemy models + session setup (owned by database-architect pass)
scripts/
  parse_recipes_to_schema.py   One-time migration: manual_execution/*.md -> Field Schema Registry drafts
schemas/
  drafts/              Generated draft Field Schema Registry JSON files (Day-1 output, needs human review)
```

## Architecture note

Layered: **Controller (routers) -> Service (services) -> Repository (db)**. Routers depend only on
services; services depend only on repository/db interfaces — never the other way around. All I/O
(DB, Redis, Anthropic calls) is async. All external input is validated through Pydantic v2 models
before it reaches a service. No secrets are hardcoded — everything sensitive comes from environment
variables (see `.env.example`).
