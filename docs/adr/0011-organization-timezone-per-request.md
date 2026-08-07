# 0011 — The organization's timezone is activated per request

Status: accepted, 2026-08-07.
Supersedes the "Known defects" entry in `docs/MVP-NOTES.md`, which now records
this as fixed.

## Context

`Organization.timezone` has existed since the first migration. `bootstrap_demo`
writes `Asia/Dhaka` to it. Nothing read it: `settings.TIME_ZONE` was `'UTC'`,
`USE_TZ` was on, and there was no `timezone.activate` anywhere in the project.

So every aware datetime was rendered in UTC — six hours behind this clinic — and
the two `datetime-local` widgets (`Encounter.occurred_at`,
`GoodsReceipt.received_at`) were defaulted from `timezone.localtime()`, which is
UTC for the same reason.

Found by opening the visit form from the new day list and noticing it defaulted
to 02:06 PM while the browser clock read 20:07.

**This wrote wrong data, and the mechanism is the opposite way round from how it
looks.** Worth stating precisely, because the obvious reading is wrong:

- **Accepting the default was safe.** Render and parse both used UTC, so the
  value round-tripped. A visit saved by accepting `14:06` stored `14:06+00:00`,
  which is 20:06 Dhaka — the correct instant.
- **Correcting the field was what corrupted it.** A doctor who sees "02:06 PM"
  at eight in the evening fixes it to `20:06`. That was parsed as UTC and stored
  as `20:06+00:00` = **02:06 the following day** in Dhaka: six hours out, and on
  the wrong date.

The corruption was therefore triggered by a careful user correcting something
plainly wrong on screen, from entirely correct input, and a clinic that never
looked at the clock kept good data by doing nothing. That is a data-integrity
defect, not a display nicety, which is why it was fixed before further features.

## Decision

### Activate the organization's timezone in the middleware, beside the org context

`ActiveOrganizationMiddleware` now enters `organization_timezone(organization)`
alongside `organization_context(organization)`. The two answer one question —
which clinic is this, and therefore what time is it there — so they get one
lifecycle. Splitting them is what allowed the zone to sit on the row, unread,
for the whole MVP.

`core.context.organization_timezone` wraps `django.utils.timezone.override`,
which already saves and restores in a `finally`. That is the same
reset-in-finally discipline `organization_context` uses, and for the same
reason: a leaked zone would give the next request the previous clinic's clock,
which on a shared server is the timezone equivalent of a cross-tenant leak.

Passing `None` deactivates for the block rather than leaving the previous value,
so an unauthenticated request gets a known-clean UTC instead of whatever the
last request on that thread left behind.

### `TIME_ZONE` stays `'UTC'` and `USE_TZ` stays on

Storage does not move. Every datetime is still stored in UTC; what moved is
presentation — `localtime`, `localdate`, the template `date` filter, and
therefore every form default that reads from them.

This is why the fix is small. Django's `date` filter converts aware datetimes to
the *active* zone, so activating it once fixes every display in the project
without touching a template. Converting at each call site would have been the
same fix applied thirty times, with thirty chances to miss one.

### Two separate context managers, not one

`organization_context` takes an Organization *or a bare pk* — scoping a query
only needs the pk, and callers that have one must not be forced into a query.
Reading a zone name needs the row. They stay separate rather than making the
cheap one expensive, with the rule that anything activating one activates the
other. `bootstrap_demo` is the second caller, and it needs the clock for a real
reason: it books "today", and between midnight and 06:00 in Dhaka a UTC "today"
would seed a day the day list does not open on.

### An unusable zone name falls back, loudly

`Organization.timezone` is a plain char column that loaders and the admin can
write, and it is not validated by the database. A typo resolves to UTC — which
is what storage uses anyway — and logs a warning naming the organization and the
bad value. One tenant's bad row is not a reason to 500 their clinic, or anyone
else's once this is multi-tenant in production.

A model-level validator was considered and **not** added: it would mean a
migration, the field is not editable through any form today, and the standing
rule is to propose schema changes rather than fold them into a fix. Worth doing
when the organization settings screen grows a timezone field.

## Consequences

- **"Today" is now the clinic's today.** `scheduling.views` and
  `scheduling.services.walk_in` read `timezone.localdate()`, so the day list
  opens on, and walk-ins are filed under, the receptionist's date. For the six
  hours after midnight in Dhaka these differ from the server's date; before this
  change the night shift would have seen yesterday's list with today's bookings
  invisible.
- **Invoice numbers are prefixed with the clinic's year** (`core.services`), so
  a bill raised at 00:30 on 1 January in Dhaka now belongs to the new year
  rather than the old one.
- **A test suite that only uses UTC organizations proves nothing here.** The
  default `Organization.timezone` is `'UTC'`, and under UTC storage, display and
  "today" all agree — the defect is invisible. `core/tests/
  test_organization_timezone.py` therefore runs against `Asia/Dhaka` throughout,
  and pins `timezone.now` to 19:30 UTC (01:30 next day in Dhaka) so the two
  calendars genuinely disagree. Any future work on this must keep a non-UTC
  organization in the picture.
- **Both `datetime-local` widgets are covered**, not one and an assumption:
  `occurred_at` and `received_at` each have a round-trip test asserting the
  rendered string comes back as the correct UTC instant.
- Management commands, Celery tasks and tests are still outside the middleware.
  They get UTC unless they enter `organization_timezone` explicitly, the same
  caveat `organization_context` has carried since ADR 0005.
- Existing rows written before this change are unaffected by it. Any datetime a
  user hand-edited under the old behaviour is six hours out; there is no
  migration, because there is no way to tell those apart from ones that were
  correct.
