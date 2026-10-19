# Database Schema

Postgres schema for Marketing-in-a-Box, implemented as SQLAlchemy 2.0 declarative models in
`app/db/models.py` and materialized by the Alembic migration in
`alembic/versions/20260813_0000_f9e7c36b9b56_initial_schema.py`.

Two source documents are authoritative for what these tables must be — see each table's
"Satisfies" line below:
- `docs/Marketing_in_a_Box_Session_Context.md` (Sec. 4 "Data Storage — Permanent vs. Temporary",
  Sec. 6 "Asset Wiring — the 16 Phase 1 Assets")
- `docs/Conversational_Intake_Engine_Design.md` (Sec. 5 "App Architecture" → "Data model
  (Postgres, additive)")

## Design principles applied

- **UUID primary keys** everywhere except `asset_definitions` (see below), generated
  client-side (`default=uuid.uuid4` in Python), so no `pgcrypto`/`uuid-ossp` extension is
  required.
- **`TIMESTAMPTZ` for every timestamp** (`DateTime(timezone=True)` in SQLAlchemy), never naive
  `TIMESTAMP`.
- **`JSONB` only where the doc explicitly calls for flexible/structured data** (context values,
  resolved intake fields, DAG edge lists) — never as a generic substitute for a real column.
  Everywhere else uses a proper scalar type (`String` with an explicit length, `SmallInteger`,
  `Boolean`, `Text` for genuinely unbounded prose).
- **Native Postgres `ENUM` types** (not `CHECK (col IN (...))` strings) for closed, small state
  sets that the application branches on: `run_status`, `stage_status`, `field_session_status`,
  `audit_action`. A `CHECK` constraint is used instead for `asset_definitions.phase`, which is
  a plain 1-or-2 flag, not a state machine.
- **Foreign keys everywhere a row logically belongs to another row**, with an explicit
  `ON DELETE` behavior chosen per relationship (see "Cascade behavior" below) rather than left
  at the database default.
- **No column named `context_key` or `asset_id` is a Postgres ENUM.** Both are free-form
  strings validated at the application layer against a registry table
  (`asset_definitions`, and eventually a context-key catalog). The 22-asset vocabulary and the
  ~90 context keys it reads/writes are still being finalized (see
  `schemas/drafts/*.json` / `REVIEW_NEEDED.md` from the Day-1 registry pass) and will keep
  growing; a rigid enum would need a migration for every new asset or key.

## Tables

### `clients`
**Satisfies:** Session Context Sec. 4, "Client profile object" (Permanent tier).

One row per client engagement. Holds only the operator-entered fields the ICP stage's intake
needs before any pipeline context exists (Sec. 6, ICP row: "company, industry, region, service
focus, known competitors" — `known_competitors` is `JSONB` since it's a list of strings).
Everything the pipeline itself derives about a client (personas, CRO copy, funnel stages, ...)
lives in `context_entries`, not here — this keeps the client row schema stable regardless of how
many context keys the 22-asset registry grows to.

### `asset_definitions`
**Satisfies:** Session Context Sec. 6 (the 16+6-asset DAG table), machine-readable form.

One row per Phase-1/Phase-2 asset (`asset_id` is the natural key, e.g. `"icp"`,
`"lead_magnet"`, `"remarketing_pillar_page"` — matches the `asset_id` values already appearing
in `schemas/drafts/*.json` from the Day-1 Field Schema Registry migration script). Captures:
`phase` (1 or 2), `sequence_order` (position within its phase), `is_gated` (HITL gate or
auto-chain), `model_tier` (which Claude cost tier this stage's generation call uses — see "LLM
model tiers" below), and three `JSONB` arrays: `depends_on` (upstream `asset_id`s — the actual
DAG edges), `reads_context_keys` / `writes_context_keys` (the exact wiring table from Sec. 6,
e.g. `icp` writes `["icp_persona_name", "icp_pain_points", ...]`).

This table has no seed data in the initial migration — it must be populated from the
finalized asset registry (Day 1 roadmap task) before any `run_stages`, `prompt_recipes`, or
`field_schema_registry` rows can be inserted, since all three FK to `asset_definitions.asset_id`
with `ON DELETE RESTRICT`. The app applies this seed itself on every boot
(`app/main.py`'s lifespan calls `app/db/seed.py`), so a fresh database is usable without a manual
step; `scripts/seed_asset_definitions.py` runs the same seed by hand against an arbitrary DSN. The
25-stage DAG was rebuilt from the new prompt source at `assets/Prompts/` — see `app/db/seed.py`'s
docstring for the full row list and why it's a re-runnable upsert (idempotent
`INSERT ... ON CONFLICT DO UPDATE` keyed on `asset_id`) rather than a one-time data migration.

`asset_id` is a `VARCHAR(100)` primary key, not a surrogate UUID — it's a stable, human-readable
natural key referenced from five other tables and from the Field Schema Registry JSON files on
disk; a surrogate key would add a join everywhere for no benefit.

`model_tier` is a native Postgres `ENUM` (`model_tier_enum`: `opus` / `sonnet` / `haiku`), added
by a follow-up migration (`4dba3dae9f0a_add_model_tier_to_asset_definitions`) on top of the
initial schema rather than by editing it — see that migration's docstring for why (never mutate
an already-applied revision). Unlike `asset_id`, this genuinely is a small closed set the
application branches on (which Claude model to call), so a native enum is the right type here,
consistent with this doc's own "Native Postgres ENUM" design principle above.

### `runs`
**Satisfies:** Session Context Sec. 4, "Execution graph / run state" (the run half).

One row per client pipeline execution, covering all stages of **both** Phase 1 and Phase 2 for
that engagement (see "Open question 1" below for why). Tracks overall `status`
(`run_status` enum) and a denormalized `current_stage_id` pointer (FK to
`asset_definitions.asset_id`, `SET NULL` on delete) for fast dashboard queries that would
otherwise require aggregating `run_stages`.

### `run_stages`
**Satisfies:** Session Context Sec. 4, "Execution graph / run state" (the per-stage half) +
Sec. 6 gating model; reconciles the stage-state vocabulary from both source docs.

One row per `(run_id, asset_id)` (enforced by `uq_run_stages_run_asset`), holding that stage's
position in the `stage_status` lifecycle: `NOT_STARTED → COLLECTING → READY_FOR_GENERATION →
CONFIRMED → DRAFTING → AWAITING_REVIEW → APPROVED`, with `REJECTED` as the "sent back" loop
(Session Context Sec. 2's architecture diagram) that the app layer re-drives back to `DRAFTING`.

**Reconciliation note:** the Intake design (Sec. 3) defines the `FieldSession`'s own state
machine as `NOT_STARTED → COLLECTING → READY_FOR_GENERATION → CONFIRMED`. The PRD (referenced
from Intake design Sec. 5) separately names a stage sub-state `INTAKE_IN_PROGRESS` preceding
`DRAFTING`/`AWAITING_REVIEW`. These describe the same moment from two altitudes — one is the
fine-grained state of a single intake conversation, the other the coarse-grained state of the
whole stage shown on the strategist's timeline — so `stage_status` reuses the literal
`COLLECTING` (rather than introducing `INTAKE_IN_PROGRESS` as a second synonym) for every state
it shares with `field_session_status`, so a session's status can be copied onto its parent stage
without a translation table.

The DAG dependency/gating check itself (`is_gated`, `depends_on`) is **not** duplicated here —
it lives once, statically, on `asset_definitions`. Whether a stage is unblocked is a dynamic
join (has every `asset_id` in this stage's `depends_on` reached `APPROVED`?), which is
orchestrator logic, not something a fixed `CHECK` constraint can express.

### `context_entries`
**Satisfies:** Session Context Sec. 4, "Approved stage outputs (structured JSON)"; Sec. 6
cross-cutting rule "Context keys are additive, never overwritten."

The context store. Each row is one `(run_id, context_key, version)` — e.g.
`(run_a, "icp_pain_points", 1)`. "Revising the ICP means re-running Stage 1" (Sec. 6) is modeled
as inserting `version = 2`, never as an `UPDATE` of `version = 1`.

**The additive rule is enforced at two layers, not just documented as a convention:**
1. **Schema:** no `updated_at` column, and `UniqueConstraint(run_id, context_key, version)`
   instead of a single-row-per-key uniqueness — this allows many versions per key while still
   preventing two inserts from racing to claim the same version number.
2. **Database trigger:** the initial migration creates a `block_mutation()` PL/pgSQL function and
   attaches it as a `BEFORE UPDATE OR DELETE` trigger on this table. Any `UPDATE` or `DELETE`
   against `context_entries` — from the ORM, a raw `psql` session, or anything else — raises a
   Postgres exception (`ERRCODE 'restrict_violation'`). This is a **real, database-level
   constraint**, not an ORM-only guarantee.

"Current" value for a key is `SELECT ... WHERE run_id = :r AND context_key = :k ORDER BY version
DESC LIMIT 1` — the `ix_context_entries_run_key` index on `(run_id, context_key)` supports
exactly that query (and the plain "list everything written so far for this run" query the
Plan-of-Action synthesis stage needs, Sec. 6 row 10).

### `approval_audit_log`
**Satisfies:** Session Context Sec. 4, "Approval & edit audit trail."

One row per strategist action (`audit_action`: `APPROVED` / `REJECTED` / `EDITED`) against one
stage's draft output. `notes` is the free-text surface the Intake design's PRD amendment (Sec. 2)
scopes to "rejection notes on drafts" — this table is where that free text lands, permanently.
`diff_snapshot` (`JSONB`, nullable) carries a before/after payload for `EDITED` actions, since
`context_entries` only ever captures the final *approved* output, not a rejected-then-edited
intermediate — without this column that intermediate content would be unrecoverable.

Indexed on `(run_id, asset_id)` (audit trail for one stage) and on `created_at`
(chronological review across a run or client).

### `prompt_recipes`
**Satisfies:** Session Context Sec. 4, "Versioned prompt recipe library."

One row per `(asset_id, version)` of a stage's master prompt template (`recipe_body`, `TEXT` —
this is genuinely free-form template markdown, e.g. the contents of
`manual_execution/Lead-Magnet-Architect-Prompt.md`, so `TEXT` is the correct type here, not a
`TEXT`-as-catch-all shortcut). `is_active` plus a **partial unique index**
(`uq_prompt_recipes_one_active_per_asset`, `UNIQUE (asset_id) WHERE is_active`) guarantees at
most one active recipe version per asset at any time, so the Prompt Assembler (Intake design
Sec. 3) can resolve "the current recipe for this stage" with no ambiguity and no `ORDER BY
version DESC LIMIT 1` guesswork.

### `field_schema_registry`
**Satisfies:** Intake design Sec. 5, exact column list: `(asset_id, version, schema_json,
created_at)`.

Versioned, append-only, one JSON document per `(asset_id, version)` (Intake design Sec. 4's
schema format). Immutability matters concretely here: a `FieldSession` pins a `schema_version`
for reproducibility (FR-I8 — "the exact assembled prompt ... is stored alongside the generation
output for audit/reproducibility"), so the schema a run's intake was conducted against must
never change under it. Enforced the same way as `context_entries`: no `updated_at`, a
`(asset_id, version)` uniqueness constraint, and the same `block_mutation()` trigger blocking
`UPDATE`/`DELETE`.

### `field_sessions`
**Satisfies:** Intake design Sec. 5, exact column list: `(id, run_id, stage_id, schema_version,
status, resolved_fields JSONB, pending_field_ids JSONB, transcript JSONB, created_at,
updated_at)`.

The column is named `stage_id` (not `asset_id`) to match the design doc verbatim, but it carries
real referential integrity via a **composite foreign key** on `(stage_id, schema_version)` →
`field_schema_registry.(asset_id, version)`. This does two jobs a single FK to
`asset_definitions.asset_id` couldn't: it guarantees `stage_id` names a real asset (transitively,
since `field_schema_registry.asset_id` itself FKs to `asset_definitions`), *and* it guarantees
the pinned `schema_version` actually exists for that stage — a session can never point at a
schema version that was never registered.

`status` uses the `field_session_status` enum (`NOT_STARTED / COLLECTING /
READY_FOR_GENERATION / CONFIRMED`) exactly as specified in Intake design Sec. 3.

`(run_id, stage_id)` is indexed (`ix_field_sessions_run_stage`) for the lookup pattern called out
in the task, but is **deliberately not a hard uniqueness constraint** — Session Context Sec. 6
says revising an upstream stage "cascades a re-approval requirement down the chain," which can
reopen intake for a stage that already has a `CONFIRMED` session from an earlier pass. Instead, a
**partial unique index** (`uq_field_sessions_one_open_per_stage`, `UNIQUE (run_id, stage_id)
WHERE status != 'CONFIRMED'`) allows unlimited *closed* (`CONFIRMED`) sessions per stage over
time, while still preventing two concurrent *open* intake conversations for the same stage.

### `attachments`
**Satisfies:** Intake design Sec. 5, exact column list: `(id, field_session_id, field_id,
storage_url, mime_type, uploaded_at)`; FR-I7.

`field_id` is a plain string, not a foreign key: it addresses one field inside the parent
session's schema JSON document, and JSON array elements aren't rows a foreign key can target.
Per the intake sequence (Sec. 5's `POST /attachments`), a row here represents a promoted,
durable attachment — the short-TTL Redis pointer during an in-progress upload is out of scope
for this permanent-tier table by design.

### `users`
**Satisfies:** the sign-in gate (`app/routers/auth.py`), added by migration `f2a71c9d4e83`.

Email is the identity key, stored already-lowercased (see `normalize_email` in
`app/services/auth.py`) behind a single `UNIQUE` index rather than a functional `lower(email)`
index. Normalization happens in exactly one place in the application, and a case-sensitive unique
index would happily create a second account for the same person typing the same address.

`password_hash` is NOT NULL. A password is the only way into this system — there is no
federated/OAuth identity in this schema — so an account without one could never be signed in to
and has no reason to exist.

The hash itself is stdlib `hashlib.scrypt` (RFC 7914), formatted `scrypt$n$r$p$salt$digest` so the
work factors travel with each hash. Raising the cost later therefore does not invalidate existing
passwords — `verify_password` reads `n`/`r`/`p` from the stored value, not from the module
constants. No bcrypt/argon2 wheel is required anywhere in this project as a result.

`email_verified` starts false: a password signup proves nothing about the address. Redeeming a
reset link is the only thing that sets it, because reaching a link in the inbox is the only proof
of ownership this system can obtain.

### `user_sessions`
A signed-in browser. The cookie carries 256 bits of `secrets.token_urlsafe`; this table stores
only its SHA-256 hex, so a dump of the schema can confirm a token someone already holds and
cannot produce one.

Opaque server-side sessions rather than a JWT, deliberately. The two operations this product
actually needs — "sign out" and "sign out everywhere" (the latter fires automatically on every
password reset) — are a `DELETE` here and are simply not expressible against a stateless token
that stays valid until it expires. The cost is one indexed lookup per authenticated request,
which is nothing beside a Claude call.

Plain SHA-256 rather than scrypt is correct *here* and would be wrong for `users.password_hash`:
these tokens are full-entropy random, so there is no dictionary to attack and no reason to pay a
KDF's cost on every request. Passwords are low-entropy and human-chosen, hence scrypt.

Expired rows are deleted on sight by `resolve_session`, so ordinary traffic grooms the table and
no scheduled cleanup job is needed to stop it growing.

### `password_reset_tokens`
A single-use, time-limited reset grant (1 hour, see `RESET_TOKEN_TTL`). Same SHA-256-only storage
as `user_sessions`.

A row rather than a signed stateless token, for the same reason: "this link has already been
used" is a fact about the world that has to be *recorded* somewhere. A signed token carries its
own expiry but nothing can revoke it, so a reset link forwarded out of an inbox would stay live
for its whole window even after the password had already been changed. `used_at` is what closes
that; issuing a new link also deletes any prior unused one, so the most recent link is the only
live one.

Redeeming a token sets `users.email_verified` — reaching a link sent to the address is the proof
of ownership that a password signup could not establish.

## LLM model tiers

Every DAG stage (an `asset_definitions` row) declares a `model_tier` (`opus` / `sonnet` /
`haiku`) — which Claude cost tier the executor should call when generating that stage's draft.
The Interviewer Agent (Conversational Intake Engine's conversational field-intake component,
NFR-I1 in `docs/Conversational_Intake_Engine_Design.md`) is *not* itself a DAG asset — it runs
once per field per turn, across every stage's intake session, rather than once per stage — so it
has no `asset_definitions` row to carry this on. It is documented here instead: **the Interviewer
Agent uses `haiku`.**

Full seed data for the 25-stage DAG lives in `app/db/seed.py`, applied on every boot and by
`scripts/seed_asset_definitions.py` on demand (edit it whenever a stage's tier/gating/dependencies
change — see that module's docstring for why it's a re-runnable upsert, not a one-time migration).
Retiring a stage does not delete its row: it is parked above `PARK_BASE` so pre-existing runs can
still FK to it. Rationale for the three tiers, applied consistently across all
25 seeded stages plus the Interviewer Agent:

- **`opus`** — **no longer assigned to any stage.** It held the 3 gated foundation stages
  (`icp`, `cro`, `pillar_page`), on the argument that every other stage reads their output so an
  error there cascades further than one anywhere else. Those three are now `sonnet`. What changed
  is the reading of the second half of that argument: they are also the only `is_gated = true`
  stages, so a human strategist reviews their output before anything downstream unblocks — and a
  review gate, not a model tier, is what actually contains the blast radius. Against that, Opus
  cost 2.5x Sonnet on both input and output. The enum member is retained (not migrated away)
  because `asset_definitions` rows written before the move still carry it.
- **`sonnet`** — 11 of the 12 generation stages (`offers`, `funnel`, `funnel_hub_media`,
  `lead_magnet`, `plan_of_action`, `blog`, `content_marketing_strategy`,
  `social_content_strategy_audit`, `webinar`, `book`, `podcast` — `sms_sequence` is the
  12th generation stage and is the one exception, on `haiku` instead, see below) and all
  `competitor_analysis_*` stages. Competitor analysis specifically needs `sonnet` rather than
  `haiku` despite being an "auto-chain" (non-gated) stage: per
  `assets/Prompts/Competitor Analysis/00_README.md`, every competitor-analysis prompt requires
  genuine verification reasoning (a `verification_confidence` field, explicit exclusion of
  directories/marketplaces/aggregators, "return fewer than 10 rather than pad with weak
  matches") — a cheap pattern-matching model is exactly the failure mode that produces
  hallucinated fake competitors, which this prompt design is explicitly trying to prevent.
- **`haiku`** — narrow/short-output or high-frequency work: `sms_sequence` (the one generation
  stage on `haiku`, not `sonnet` — its output is short and templated, not analytical), and the
  Interviewer Agent (high-frequency: invoked once per field per turn, across every stage's intake
  session, per NFR-I1).

**A note on the source counts:** the task that produced this DAG referred to it as "24-stage,"
but the row-by-row source list enumerates 25 (3 gated + 13 generation + 9 competitor-analysis).
Separately, `assets/Prompts/Competitor Analysis/00_README.md` documents 10 competitor-analysis
prompts (CRO, Offers, Lead Magnet, Blog, SEO Pillar Page, Content Marketing, Social Content
Strategy, Webinars, Book, Podcast) — one more than the 9 `competitor_analysis_*` rows actually
seeded; `competitor_analysis_book` is conspicuously absent from the DAG as specified. Both
discrepancies are called out rather than silently resolved — see
`scripts/seed_asset_definitions.py`'s docstring — pending confirmation from the product owner on
whether `competitor_analysis_book` should be added as a 26th stage.

**Current composition (after the Pillar Page merge):** 25 rows = 3 gated + 12 generation + 10
competitor-analysis. `competitor_analysis_book` was since confirmed and added (26th row), and
`seo_pillar_page` was then merged into `pillar_page` (back to 25) — the SEO variant re-ran the same
prompt file as the design stage, so the two are now one stage whose prompt
(`Master_Prompt_Universal_Page_Design_v1.md` v2.0) carries the SEO + competitor-benchmark pass
itself. `pillar_page` consequently gained a hard `competitor_analysis_seo_pillar_page` edge.
`competitor_analysis_funnel` was then added (26 rows = 3 gated + 12 generation + 11
competitor-analysis), so the funnel's Step 2 compares real competitor funnels; `funnel` depends on it.
Re-running the seed script does **not** delete the retired `seo_pillar_page` row from a database
that already has it, and that is deliberate: pre-merge `run_stages` / `context_entries` /
`approval_audit_log` rows still FK to that `asset_id`.

## Cascade behavior (`ON DELETE`)

| Relationship | Behavior | Why |
|---|---|---|
| `runs.client_id → clients.id` | `RESTRICT` | A client with run history can't be deleted out from under its audit trail. |
| `runs.source_run_id → runs.id` | `SET NULL` | Losing the earlier run shouldn't block or cascade-delete a later, independent one. |
| `runs.current_stage_id → asset_definitions.asset_id` | `SET NULL` | Pure denormalized pointer; losing it doesn't lose any data. |
| `run_stages.run_id`, `context_entries.run_id`, `field_sessions.run_id`, `approval_audit_log.run_id → runs.id` | `CASCADE` | These are all dependent child data of one run (aggregate-root pattern) — deleting a run is expected to take its execution state, context, intake sessions, and audit trail with it. See open question 2 below if true independent-of-run audit retention is required. |
| `*.asset_id / stage_id → asset_definitions.asset_id`, `field_schema_registry.asset_id → asset_definitions.asset_id`, `prompt_recipes.asset_id → asset_definitions.asset_id` | `RESTRICT` | `asset_definitions` is slow-changing reference/config data; it must never be deletable while historical rows still point at it. |
| `field_sessions.(stage_id, schema_version) → field_schema_registry.(asset_id, version)` | `RESTRICT` | Append-only registry rows must never disappear from under a session that pinned them. |
| `attachments.field_session_id → field_sessions.id` | `CASCADE` | Pure child of its session. |
| `user_sessions.user_id`, `password_reset_tokens.user_id → users.id` | `CASCADE` | Unlike `api_usage`, neither is a record that must outlive its user: deleting an account should take its logins and its pending reset links with it, and leaving either behind would be a live credential for an account that no longer exists. |

## Indexes (query patterns they serve)

- `ix_context_entries_run_key` on `context_entries(run_id, context_key)` — "get all versions /
  current version of context key K for run R" (every stage's context read).
- `ix_field_sessions_run_stage` on `field_sessions(run_id, stage_id)` — "find the intake session
  for stage S of run R" (every intake endpoint in Intake design Sec. 5).
- `uq_field_schema_registry_asset_version` (unique) on `field_schema_registry(asset_id,
  version)` — "look up schema version V for asset A" (also doubles as the FK target for
  `field_sessions`).
- `ix_run_stages_asset_status` on `run_stages(asset_id, status)` — cross-run operational
  queries ("which runs are stuck AWAITING_REVIEW on stage X").
- `ix_runs_client_id` on `runs(client_id)` — "list all runs for a client."
- `ix_approval_audit_log_run_asset` / `ix_approval_audit_log_created_at` — audit trail lookup
  by stage, and chronological review.
- `ix_attachments_session_field` on `attachments(field_session_id, field_id)` — confirmation
  card rendering ("show the uploaded file for field F of this session").
- `ix_users_email` (unique) on `users(email)` — the lookup behind every sign-in, and behind
  `POST /auth/check-email`, which is what lets the sign-in form ask for an address first and
  then show the right second step instead of making a first-time visitor pick a tab.
- `ix_user_sessions_token_hash` (unique) on `user_sessions(token_hash)` — resolving the session
  cookie on every authenticated request.
- `ix_user_sessions_expires_at` on `user_sessions(expires_at)` — supports the "expired rows are
  deleted on sight" grooming in `resolve_session`, so the table needs no cleanup job.
- `ix_password_reset_tokens_token_hash` (unique) on `password_reset_tokens(token_hash)` —
  redeeming a reset link.

## Seeding order

Because of the `RESTRICT` FKs above, tables must be populated in this order for a new
environment:

1. `asset_definitions` (from the finalized 22-asset registry — Day 1 roadmap task)
2. `field_schema_registry` and `prompt_recipes` (per-asset, any order relative to each other)
3. `clients`
4. `runs` → `run_stages` / `context_entries` / `field_sessions` → `attachments` /
   `approval_audit_log`

## Open modeling questions for the user

1. **Is Phase 2 (remarketing) a stage set on the same `Run`, or a separate `Run` object?**
   This schema models Phase 1 and Phase 2 as stages of the *same* `runs` row for a client,
   distinguished only by `asset_definitions.phase`, because Phase 2's first stage reads Phase
   1's `icp_*` / `cro_rewritten_copy` / `design_tokens` context (Sec. 6), and keeping both
   phases on one `run_id` makes that a same-run `context_entries` lookup instead of a cross-run
   join. `runs.source_run_id` is included as an escape hatch for a client who buys the
   remarketing track later as a separate, later-dated engagement against an already-completed
   Phase 1 run — but that path (falling back to `source_run_id`'s context when a key isn't found
   on the current run) is not implemented in the schema beyond the nullable FK; it needs an
   explicit decision and matching resolver logic before Phase 2 work starts.

2. **Should `approval_audit_log` survive deletion of its `Run`?** It currently cascades
   (`ON DELETE CASCADE` from `runs.id`), consistent with treating a run as an aggregate root.
   If audit/compliance requirements mean the approval trail must outlive the run it came from
   (e.g. a run can be purged for storage reasons but "who approved what, when" must remain
   queryable forever), this FK should become `RESTRICT` (blocking run deletion entirely while
   audit rows exist) or the audit table should stop cascading and instead denormalize enough
   client/run identifying data to stand alone. No `Run` deletion path exists in the application
   yet, so this is currently a theoretical risk, not an active bug.

3. **No soft-delete column anywhere.** `clients`, `runs`, and `asset_definitions` all support
   hard deletion (subject to the `RESTRICT`/`CASCADE` rules above) with no `deleted_at` /
   `is_archived` flag. If the product needs "hide this client without losing history," that's a
   column this migration doesn't add yet.
