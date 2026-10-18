# Engineering decisions

Seven decisions that shaped this system, each with what I considered and what it costs.
The theme throughout: **let PostgreSQL enforce correctness** (transactions, row locks, constraints) instead of
hoping requests arrive one at a time.

---

## 1. Concurrency: optimistic versions for edits, a conditional UPDATE for claims

**Context.** People act on the same item at the same time: two people claim it, or someone saves an edit based on
a screen that is minutes old. Without control, the last write silently wins and earlier work disappears.

**Options.**
- *Last write wins:* simple, and silently loses data.
- *Pessimistic locking* ("check out" an item while editing): blocks others, and locks get abandoned when someone closes
  their laptop.
- *Optimistic concurrency:* every item has a `version`; a change says which version it was based on.

**Choice.**
- **Edits, assignments and status changes** run `UPDATE … SET version = version + 1 WHERE id = ? AND version = ?`.
  Zero rows updated means someone else changed it first: the API answers **409 `version_conflict` with the current item**,
  and the data is untouched (`items/service_command.py: _versioned_update`).
- **Claims** don't use the version at all: `UPDATE … SET assignee_id = me WHERE id = ? AND assignee_id IS NULL AND status = 'open'`.
  "Take it if nobody owns it" *is* the user's intent, so the condition is the intent. When two claims race, Postgres
  serialises them on the row lock; the second re-checks its `WHERE` against the new row and updates nothing. Exactly one
  wins. Claiming an item you already own returns 200, so a double-click is harmless.
- **The UI** remembers the version an edit started from (not the latest polled one), and on a conflict offers to
  re-apply **only the fields the user changed** on top of the latest version. Nobody's changes are overwritten without
  them seeing it.
- Comments don't bump the version, so chatting never causes edit conflicts.

**Trade-off.** Under heavy contention on one item, the loser has to reload and retry. For operational work, where the
same item is rarely edited by two people at the same second, that's far better than locks that block or go stale.

---

## 2. Safe retries: an Idempotency-Key on every write

**Context.** "A user repeating an action because they are unsure whether the first request succeeded." A create that
times out and is retried must not produce two items, and a retried approval must not come back as a confusing 409.

**Options.** Client-side button disabling only (doesn't cover network retries) · natural-key deduplication (items have
no natural key) · server-side idempotency keys.

**Choice.** Every write accepts an `Idempotency-Key`; the frontend generates one per user action and retries network
failures with the **same** key (`core/idempotency.py`).
- In the request's transaction, the key is reserved with `INSERT … ON CONFLICT DO NOTHING`. If an identical request is
  still running, Postgres makes this insert **wait** for it, so concurrent duplicates are serialised by the database,
  not by application locks.
- The response is stored in the same commit as the change, so "done but not remembered" is impossible.
- Same key with a different body gives **422** (a client bug, not a retry).
- Errors roll back the reservation too: failures aren't cached, and a retry re-evaluates against current state.
- Keys are scoped per user (primary key `(user_id, key)`): nobody can receive another user's stored response.

**Trade-off.** One extra row write per mutation and a table that grows; the worker deletes keys after 24 h, which
bounds both. This is also not a tool for *different* people colliding. That's decision 1.

---

## 3. Authorization: team-scoped roles, one `authorize()`, checked against the item

**Context.** Users belong to several teams with different roles. Permissions must be enforced by the server, per team,
and for some actions per item.

**Options.** Global roles (too coarse) · roles embedded in the JWT (go stale until the token expires) · per-request
lookup of team roles.

**Choice.**
- `memberships(user_id, team_id, role)` with primary key `(user_id, team_id)`: many teams, one role per team.
  `is_admin` is the only global permission.
- Roles are **loaded on every request**, not stored in the token, so demoting or removing someone takes effect on their
  next request.
- **One function decides everything:** `auth/permissions.py: authorize(user, action, team_id, item)`. It applies a
  role → action table, then a per-item rule: **members may only edit or move items they own; leads act on anything in
  their team.**
- Another team's item answers **404, not 403**, so its existence isn't confirmed. Lists are scoped in SQL
  (`WHERE team_id IN (my teams)`), so other teams' rows are never even read.
- **Whether approval is required is decided by the server** from the item type (payment, compliance), not by a field
  the client sends, so the requester can't switch it off.
- The UI receives an `allowed` block computed by the *same* functions, so buttons match what the server will accept.
  It is only a hint; every request is checked again.

**Trade-off.** One extra indexed query per request. Rules live in code, not in a configurable policy engine: easy to
read and test, but changing the permission table means a deploy.

---

## 4. History in the same transaction, and enums the database enforces

**Context.** "Important actions should not silently disappear." History must never disagree with the item.

**Choice.**
- `activity_events` is **append-only**, and each change writes its event **in the same transaction** as the change.
  Either both exist or neither does. Rejected changes leave no trace in history, and accepted ones always do.
- Comments are events too: one timeline, one query, one index (`item_id, id DESC`).
- Statuses, roles, types and priorities are Python **StrEnums** stored as `VARCHAR` with **CHECK constraints** generated
  from the same enums, not native Postgres `ENUM` types. The database still rejects bad values, but adding a value is a
  one-line constraint change instead of `ALTER TYPE`, and native enums store names that Alembic doesn't diff well.
  Typos in code (`Status.OPNE`) fail on import instead of silently never matching.

**Trade-off.** Event payloads are JSONB, so history is flexible but not strongly typed; old events keep the shape they
were written with. The events table grows forever (at scale: partition it by month).

---

## 5. Sync request handling; secondary work through a transactional outbox

**Context.** Notifications are secondary work: they shouldn't slow down or fail the primary action, but they must not
be lost or duplicated.

**Options.**
- Send inline (slow, and the action fails when notifications fail).
- Publish to a broker after commit (a crash between commit and publish loses the message).
- **Transactional outbox:** write a `jobs` row in the same transaction as the change.

**Choice.**
- **API is synchronous** (plain `def` endpoints, sync SQLAlchemy). Every request is a few short queries; FastAPI runs
  sync endpoints on a thread pool, and the correctness lives in Postgres either way. Sync code is simpler to reason
  about and debug. Scale out with more worker processes.
- **Outbox:** a job exists **if and only if** the change committed. `dedupe_key` is unique, so the same logical job is
  queued once.
- **Worker** (`notifications/worker.py`) takes one job per transaction with `FOR UPDATE SKIP LOCKED`, so several workers
  never pick the same job and never wait for each other. The handler runs in a **savepoint**: on failure its partial
  writes are undone, the attempt is counted, and the job is retried with exponential backoff (5 s, 10 s, 20 s … up to
  5 min) until `max_attempts`, then marked `failed` with the error kept.
- **Crash safety without leases:** the row lock is held by the transaction, so if a worker dies mid-job the transaction
  rolls back and the job is simply picked up again. No "stuck in processing" state and no lease timeouts to tune.
- **Handlers are idempotent:** each notification has a unique key derived from the job, so even a job that runs twice
  creates one notification.

**Trade-off.** Holding a transaction for the whole job only suits short, database-only handlers. A slow external call
(email, webhooks) would need a lease-based claim (`processing` + timeout) instead. Delivery is by polling (1 s worker,
15 s browser), not push.

---

## 6. Lists that stay fast as data grows

**Context.** Tens of thousands of active items and an ever-growing history. Nothing may require loading everything.

**Choice.**
- **Everything is filtered, searched and paginated on the server.** The browser only ever holds the pages it shows.
- **Keyset pagination** (`WHERE (updated_at, id) < (cursor)`) instead of `OFFSET`: page 200 costs the same as page 1,
  and items arriving while someone browses don't shift rows into duplicates. The cursor is opaque base64; fetching
  `limit + 1` rows tells us if there's a next page without a `COUNT(*)`.
- **One index per screen.** Partial indexes for the dashboard views ("unassigned urgent", "awaiting approval",
  "overdue") contain only matching rows, so they stay small. A generated `priority_rank` column makes priority sorting
  correct ("critical" > "high", which alphabetical order gets wrong) and indexable.
- **Search** uses a generated `tsvector` column (title weighted above description) with a GIN index and prefix
  matching (`refu` finds "Refund"). I deliberately avoided `ILIKE '%…%'`, which can't use an index.
- Dashboard counts reuse the **same conditions** as the lists, so a badge can never disagree with its list.

Verified with `EXPLAIN ANALYZE` on 5,000 seeded items: list pages and dashboard views are index scans at 0.1–0.2 ms.

**Trade-off.** No "jump to page 47". Search results are ordered by recency, not relevance, to keep cursors stable.

---

## 7. What I chose not to build (and why)

- **Websockets/push:** polling plus versioned writes is correct and far simpler; push is a latency improvement, not a
  correctness one.
- **Sign-up, password reset, SSO, email delivery, attachments:** outside the core problem; seeded users are enough to
  demonstrate roles.
- **A configurable workflow engine:** the workflow is one dictionary (`items/workflow.py: TRANSITIONS`) plus four
  rules. Adding a status means changing an enum, that dictionary, and a CHECK constraint migration.
- **Redis or a message broker:** Postgres already provides what the outbox needs (`SKIP LOCKED`, transactions). One
  less moving part to run and fail.

### Structure

The backend is organised by **feature** (`core`, `auth`, `items`, `notifications`), not by technical layer, and within
items, **reads** (`service_query.py`) are separate from **writes** (`service_command.py`). The rules that matter most
(locking, versions, workflow, history) therefore sit together in one short file. Dependencies point one way:
`core ← auth ← items → notifications`.
