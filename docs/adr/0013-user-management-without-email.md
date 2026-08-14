# 0013 — User management without email, and why "Owner" became "Administrator"

Status: accepted, 2026-08-09.

## Context

Until now there was no way to add a user from inside the application at all.
`bootstrap_demo` created the three demo accounts, which is exactly why the gap
stayed invisible for the whole MVP: every screen was reached by an account that
already existed. A clinic taking delivery of this could not hire a receptionist
without a developer at a shell, which makes handover impossible and made this
the last blocker before a real deployment.

## Decision: no password reset by email, and no SMTP dependency

A self-hosted box has no mail sending. Adding one means an SMTP account, a
sender domain, SPF and DKIM records, and a delivery failure mode that is silent
by construction — the user clicks "reset", sees "check your email", and nothing
arrives. That is strictly worse than no feature, because the person now believes
recovery is in progress and stops asking for help.

The three facts that make this an easy call here:

- **Email is optional and unverified.** `User.email` is `blank=True` and nothing
  ever confirms it. A reset link sent to an unverified address is an account
  takeover primitive, not a recovery path.
- **Phone is the identifier**, and this deployment has no SMS gateway either.
- **Everyone is in the same building.** The clinic is five people who see each
  other daily. The recovery path that actually gets used is walking to the desk
  and asking.

So recovery is: an administrator types a temporary password on the team screen,
reads it out, and `User.must_change_password` forces the account holder to
replace it before they can reach anything else. `ForcePasswordChangeMiddleware`
is what enforces that, and it exempts exactly three URLs — the password screen
itself, logout, and login — because a redirect that catches its own destination
is a trap with no exit.

This is recorded as a decision rather than an omission. If a deployment later
has real mail, `PasswordResetView` is additive and nothing here has to move.

## Decision: `OWNER` keeps its stored value; "Administrator" is a label

"Owner" claimed more authority than the job has. It reads as the person who owns
the practice, who is often not whoever holds the account — and the account's
actual powers are adding people, setting the consultation fee, and turning
features on.

Renaming the stored value would mean a data migration plus touching every
`Role.OWNER` in the codebase, both `role_required` call sites, and every test —
for a change of wording. That is exactly the work `Organization.terminology`
exists to avoid (SPEC §5). So `role_owner` / `role_practitioner` / `role_staff`
joined the map, `{% role_label %}` renders them, and the column still stores
`OWNER`. A clinic that wants "Manager" or "Clinic lead" now changes data.

`Role.OWNER`'s enum label also became "Administrator", because that is the
fallback the Django admin and any organization-less render will show, and two
different words for one role is worse than either word.

## Decision: deactivation is on `Membership`, not on `User`

A practitioner working at two clinics on one deployment holds one account with
two memberships — that is why `User` is deliberately not organization-scoped.
Withdrawing access at one clinic must therefore not touch their login at the
other, so `set_membership_active` flips `Membership.is_active` and never
`User.is_active`.

Nothing is ever hard-deleted. Visits, bills and stock movements carry the user
as `created_by` and `actor`, and that attribution has to outlive the person
leaving. A delete would either destroy it or fail on a `PROTECT`, and both are
worse than an inactive row.

The consequence a deactivated account meets is `core/no_organization.html`, a
403 that says access is gone and who to ask — reachable in normal operation now
that deactivation is a button rather than a shell command.

## Decision: the self-guard is the whole guard

An administrator cannot demote or deactivate themselves. That single rule is
sufficient to keep at least one active administrator in every organization,
without counting anything: the only account that could remove the *last*
administrator is that administrator, and it is refused. A "last administrator"
check would be a second, weaker way of saying the same thing, and one that has
to hold a lock to be correct.

The demotion guard is `disabled=True` on the role field rather than a
`clean_role` refusal. Django ignores submitted data for a disabled field and
takes the initial value, so it is enforced server-side against a hand-built POST
while also being the plainest possible thing on screen — the dropdown is greyed
out and says why.

## Decision: an existing phone number is refused, not joined

`phone` is `USERNAME_FIELD` and unique across the whole deployment, so an
administrator adding somebody may collide with an account at another clinic.
Attaching that existing account to a second organization is technically what the
schema wants and is deliberately *not* reachable from the app: the administrator
typing the number would learn the name behind it, which is a cross-tenant leak
dressed up as a convenience. The form reports that the number is in use and
names nothing.

## Consequences

- Losing the only administrator's password needs shell access
  (`manage.py changepassword`). Documented, and acceptable at this scale.
- A temporary password is a live credential until it is replaced, so it goes
  through `AUTH_PASSWORD_VALIDATORS` like any other.
- `Membership` has no branch FK, so there is still no per-branch access control
  (SPEC §5). The role dropdown is the whole permission model, which is the
  deliberate position — see
  [ADR 0012](0012-authorisation-at-the-view-boundary.md).
