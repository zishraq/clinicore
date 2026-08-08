# 0012 — Authorisation lives at the view boundary; services trust their actor

Status: accepted, 2026-08-08.
Records a decision that has been in force since the MVP without being written
down, which is why it kept reading as an oversight in review.

## Context

Every role check in this codebase is a decorator on a view. `accounts/permissions.py`
holds all four of them — `require_membership`, `role_required`, and the two
partials `clinical_access_required` and `owner_required` — and they are applied
97 times across seven view modules.

Below that line there is nothing. Every service function takes an `actor` and
uses it for attribution: stamping `created_by`, writing `changed_by` on a history
row, naming who voided an invoice or adjusted a batch. Not one of them asks
whether that actor was allowed to be there. A grep for `Role.`,
`can_view_clinical`, `is_owner` or `PermissionDenied` across all seven
`services.py` files returns a single hit, and it is a default value rather than a
check.

Read cold, that looks like the check was forgotten. It was not. It was never put
there, deliberately, and the reason has to be recorded before someone "fixes" it.

## Decision

### Authorisation is decided once, at the view boundary

A request is authorised by the time it reaches a service. The view is where the
`request` exists, where `request.membership` has been resolved by
`ActiveOrganizationMiddleware`, and where a refusal can be a 403 page instead of
an exception with nowhere to go. It is also the only layer that maps one-to-one
onto a URL, which is what the rule in SPEC §6.1 is actually about: a direct URL
hit must be refused rather than merely hidden from the template.

### Services trust their `actor` argument

`actor` is an attribution parameter, not a credential. A service records who did
something; it does not adjudicate whether they could.

### Why not both

The obvious objection is that defence in depth is free. It is not. Duplicating
the role check into services buys a second copy of a rule that must agree with
the first forever, and the two will diverge — not dramatically, but in the way
that matters: someone widens a view's decorator for a real reason and does not
know the service also decides, or tightens the service and a screen that worked
last week starts 403ing from inside a transaction. At that point neither
location is authoritative and every question about who may do what has two
answers.

Worse, the second check is the one nobody tests. The view decorators are
exercised by the STAFF-403 suite on every route. A duplicate check inside a
service is reached only through a view that already permitted the call, so it
sits there passing for years and its first real exercise is the day it disagrees.

One place that decides, and it is the place that can refuse properly.

### Why the boundary is the view and not the model

The org-scoped default manager (ADR 0005) already enforces *tenancy* below the
view, which is a different question and a much narrower one: "is this row in
this organization" is answerable from ambient context and has exactly one right
answer. "May this person do this" depends on the action, and the model layer
does not know what action is being performed. Tenancy is an invariant; authorisation
is a policy. This ADR is about the second.

## Consequences

### What this obliges a future view to do

This is the load-bearing part. Because no layer beneath will catch a mistake:

1. **Every view that reaches a service applies a role decorator, or calls
   `require_membership` explicitly.** There is no default-deny anywhere else. A
   view with no decorator is a public view, whatever its URL suggests.
2. **A new view is not finished until a STAFF request to it has been asserted to
   403.** The existing per-app permission tests are the pattern; adding the route
   without adding the test leaves an unguarded path that the suite reports as
   green, because nothing else in the stack has an opinion.
3. **Anything that calls a service outside a request must decide authorisation
   itself.** Management commands, future Celery tasks and shell scripts hold no
   `request.membership`, so `bootstrap_demo` passing an owner as `actor` is a
   decision it has made, not a check it has passed.
4. **A helper called by more than one view does not inherit the strictest
   caller.** If two views with different decorators share a service call, the
   permissive one defines what is reachable.
5. **HTMX partial endpoints need the decorator too.** They are URLs. A fragment
   route that only ever gets fetched by an authorised page is still directly
   requestable, and this is the shape the rule is easiest to forget in — the
   guard belongs on the view, not on the page that includes it.

### What has to change before this decision stops holding

Two known futures make the view boundary insufficient, and both should reopen
this ADR rather than quietly add checks underneath:

- **A non-HTTP entry point that is not trusted.** An API for a patient-facing
  app, or a webhook, means requests arrive without a membership resolved by this
  middleware. The answer is likely a second boundary of the same kind, not a
  check inside services.
- **`RolePermission` and the custom auth backend** (SPEC §6.1). When permissions
  become data rather than role comparisons, the checks move from decorators to
  `user.has_perm(...)` — but they should move *at the same layer*. The marker
  comment `# MVP: replace with permission layer` is on each one so that swap
  stays mechanical.

### What this does not excuse

Field-level and object-level visibility are still the view's problem. A
practitioner may open an encounter; whether they may open *this* encounter is a
question the decorator does not ask, and tenancy scoping answers only the
cross-organization half of it.

## Related

- [ADR 0005](0005-org-scoped-default-manager.md) — tenancy below the view, and
  why that one *is* enforced at the manager.
- `accounts/permissions.py` — all four decorators, and the marker comment.
