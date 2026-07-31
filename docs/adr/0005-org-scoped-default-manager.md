# 0005 — Organization-scoped default manager backed by a contextvar

- **Status:** Accepted (Phase 0)
- **Date:** 2026-07-31
- **Relates to:** SPEC §4 (architecture and conventions), `docs/phase-0-proposal.md` §3(c)

## Context

Tenancy is shared-schema (SPEC §2 rules out schema- and database-per-tenant).
Every business row therefore carries an `organization` FK, and SPEC §4 requires
that *"cross-organization data leakage must be structurally difficult, not a
matter of remembering a filter"*.

The obvious alternative is no ambient state at all: every query writes
`.filter(organization=organization)` explicitly. It is safer in principle and it
is honest about what the ORM is doing. It has one fatal property — a single
forgotten filter is a data breach, and there is no test that catches *"you
forgot"*. Review is the only defence, and review is not a control we can rely on
for a system holding clinical records.

Scoping in the default manager inverts that: the safe behaviour is the default,
and crossing tenants requires typing something explicit that a reviewer can
grep for. The cost is ambient state, which SPEC §0 explicitly says to justify
rather than smuggle in. This ADR is that justification.

## Decision

`OrgOwnedModel.objects` is an `OrgScopedManager` that filters on an active
organization held in a `contextvars.ContextVar`, set in exactly one place per
entry point.

### Why a contextvar rather than an explicit argument everywhere

A contextvar is per-thread *and* per-async-task, and it restores on `reset()`
rather than being globally mutable, which thread-locals are not. It also
survives the ORM's internal re-entry (related managers, `prefetch_related`,
admin changelists) where an explicit argument cannot reach.

Three conventions keep it from becoming action-at-a-distance:

1. **One setter per entry point.** `ActiveOrganizationMiddleware` for requests;
   an explicit `organization_context()` block in a management command, Celery
   task, or test. Nothing else calls into the contextvar.
2. **`services.py` functions still take `organization` as an explicit first
   argument** and never read the ambient value. The contextvar is a safety net
   for the ORM, not an input channel for business logic. Code outside
   `core/context.py` and `core/managers.py` that reads the contextvar is a
   smell.
3. **A parametrized isolation test walks every concrete `OrgOwnedModel`
   subclass** (`core/tests/test_org_scoping.py`) and asserts that a query under
   organization A cannot see, count, filter, or `get()` a row belonging to
   organization B. Adding a model without a builder entry fails the suite, so
   the coverage cannot silently rot.

### Why the public surface is only four functions

`core/context.py` exports `get_active_organization_id`, `organization_context`,
`unscoped`, and `is_scoping_enabled`. An earlier draft also exported
`activate(organization) -> Token` and `deactivate(token)` so the middleware
could set and reset without a `with` block. That was machinery for no gain:
`with organization_context(organization): return self.get_response(request)`
gives the identical release-on-exception guarantee, and every `Token` that never
escapes the module is a way to leak context that no longer exists.

### Why the pk, not the instance

The contextvar stores an integer primary key. Holding a model instance for the
life of a request means holding a row that may be stale by the end of it, and it
would give `core` an import-time dependency on `organizations`. Views that want
the object read `request.organization`, which the middleware sets.

### Why it raises instead of returning an empty queryset

`ActiveOrganizationRequired` is deliberately loud. Silently returning nothing
turns *"we forgot to activate the organization"* into *"the feature quietly does
nothing"* — the harder bug to find and the easier one to ship to production. A
feature that raises on the first request in staging is a fifteen-minute fix; a
feature that shows an empty patient list is a support ticket six weeks later
from someone who assumes their data is gone.

Legitimate cross-tenant callers have two documented ways out:

- `Model.all_objects` — narrower, preferred, for a single query.
- `core.context.unscoped()` — a block, for the Django admin, backup and
  reporting jobs, and data migrations. Explicit and greppable on purpose; every
  use should be justifiable in review.

### Why `base_manager_name` points at the unfiltered manager

`OrgOwnedModel.Meta.base_manager_name = 'all_objects'`. `objects` is declared
first so it becomes `_default_manager` — what the admin, related managers, and
ordinary view code reach for — while `_base_manager` stays unfiltered.

That split is deliberate. Django uses `_base_manager` for forward FK traversal
and `refresh_from_db()`. A filtering base manager would make
`encounter.patient` raise `ActiveOrganizationRequired` or return nothing
depending on ambient state, which the Django documentation warns about
explicitly. Scoping belongs on queries you *initiate*, not on following a
foreign key you were already legitimately handed: if you hold an `Encounter`,
you have already passed the org check that produced it.

`OrgScopedManager.use_in_migrations = False` for the same class of reason —
historical models in migrations must never inherit request-time filtering.

### Why the middleware's release is unconditional

Under Gunicorn a worker process handles thousands of requests in sequence and,
with `gthread` workers, several concurrently. A contextvar left set by request A
is visible to request B on the same thread — so a leak here is a cross-tenant
data leak, not a tidiness problem. The `with` block means there is exactly one
code path and no branch that can skip the reset, including when the resolver
returns `None`. The same reasoning applies to the test suite, where a leaked
value would make results depend on execution order; `conftest.py` asserts the
context is clean before and after every test.

## Consequences

- **Streaming responses are a known caveat.** The context is released when
  `get_response` returns, which for a `StreamingHttpResponse` is *before* the
  body is consumed. Views that stream — the Phase 3 attachment download is the
  one we know about — must resolve their queryset eagerly or re-enter
  `organization_context` inside the generator.
- **Any code path that queries an org-owned model outside a request must open a
  context.** Management commands, Celery tasks, and shell sessions get an
  exception rather than wrong data, which is the intended failure mode.
- **The Django admin needs `unscoped()`** to remain usable across tenants. That
  is a deliberate, reviewed exception, not an oversight.
- **Adding a concrete org-owned model adds a test obligation** — a builder entry
  in the isolation suite. This is the mechanism that keeps the guarantee real.

## Alternatives rejected

- **Explicit `.filter(organization=...)` everywhere.** Safer in principle;
  untestable in practice. Rejected above.
- **Schema-per-tenant.** Ruled out by SPEC §2, and disproportionate at fewer
  than 50 concurrent users forever.
- **Row-level security in Postgres.** Genuinely strong, and it would survive a
  Django-level mistake. Rejected for Phase 0 because it moves the policy into
  DDL and session variables that migrations, tests, and the local sqlite path
  would all have to reproduce. Worth revisiting if the deployment ever hosts
  organizations that do not trust each other operationally.
- **A thread-local.** Broken under async and not restorable. A contextvar is the
  same idea done correctly.