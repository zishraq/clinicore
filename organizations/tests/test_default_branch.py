"""Which chamber a new visit opens on, and who decides it.

It used to be "whichever branch sorts first by name", which nothing on any
screen said and no test pinned. The clinic added two Bengali-named chambers one
evening and the default moved onto one that opens on the second Friday of the
month — a rename, or a new chamber, silently changing where visits are recorded.

So the clinic states it. ``Branch.is_default`` is the answer, the name is only
the last tiebreak, and the constraint keeps the answer singular.
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from core.context import organization_context
from organizations.forms import BranchForm
from organizations.models import Branch
from organizations.services import default_branch

pytestmark = pytest.mark.django_db


def _make(organization, name, code, **fields):
    with organization_context(organization):
        return Branch.objects.create(
            organization=organization, name=name, code=code, **fields
        )


def _fields(**overrides):
    """A whole chamber form. Absent checkboxes post nothing, so they are absent."""
    data = {
        'name': 'Kushtia Chamber',
        'code': 'KUS',
        'address': '',
        'phone': '',
        'consulting_hours': '',
        'schedule_note': '',
        'print_order': '1',
        'is_active': 'on',
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


# --- The constraint ---------------------------------------------------------


def test_the_database_refuses_a_second_default(organization, branch):
    """The partial unique index, not the form, is what makes this true.

    Every writer goes through it — a management command, a shell, a future
    import — so the invariant cannot be lost by adding a second screen that
    sets the flag.
    """
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])

    with pytest.raises(IntegrityError), transaction.atomic():
        _make(organization, 'Kushtia Chamber', 'KUS', is_default=True)


def test_a_second_chamber_that_is_not_the_default_is_fine(organization, branch):
    """The index is conditional on ``is_default=True``.

    Without that condition it would allow one default *and one non-default* per
    clinic, which is the opposite of what is wanted — and a clinic could not
    have two chambers at all.
    """
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])

    second = _make(organization, 'Kushtia Chamber', 'KUS')

    assert second.pk

    with organization_context(organization):
        assert Branch.objects.count() == 2


def test_each_clinic_gets_its_own_default(organization, branch, other_organization):
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])

    theirs = _make(other_organization, 'Theirs', 'THR', is_default=True)

    assert theirs.is_default is True


# --- Ticking it -------------------------------------------------------------


def test_ticking_a_new_default_releases_the_old_one(
    client, owner, organization, branch
):
    """The clinic never unticks first.

    Being made to clear the previous default before setting the next one is a
    step that exists only to serve the constraint, and a clinic that gets it
    half-done ends up with no default at all.
    """
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])
    kushtia = _make(organization, 'Kushtia Chamber', 'KUS')

    client.force_login(owner)
    response = client.post(
        reverse('organizations:branch_update', args=[kushtia.pk]),
        _fields(is_default='on'),
    )

    assert response.status_code == 302
    branch.refresh_from_db()
    kushtia.refresh_from_db()
    assert kushtia.is_default is True
    assert branch.is_default is False


def test_the_default_survives_an_edit_that_does_not_touch_it(
    client, owner, organization, branch
):
    """Editing the chamber that already holds it must not hand it away."""
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])

    client.force_login(owner)
    response = client.post(
        reverse('organizations:branch_update', args=[branch.pk]),
        _fields(name='Main Chamber', code='MAIN', is_default='on'),
    )

    assert response.status_code == 302
    branch.refresh_from_db()
    assert branch.is_default is True


def test_a_clash_is_a_field_error_not_a_database_crash(
    client, owner, organization, branch, monkeypatch
):
    """A clash that reaches the database still comes back as a sentence.

    The constraint is on (organization, is_default) and ``organization`` is not
    a form field, so Django drops it from validation entirely — the trap in
    docs/MVP-NOTES.md. The form normally releases the default it found, so the
    only way to reach the database with a clash is for one to appear after it
    looked: two administrators ticking the box at once. That race is simulated
    here by making the form find nothing to release.
    """
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])
    kushtia = _make(organization, 'Kushtia Chamber', 'KUS')

    def blind(self):
        self.defaults_to_clear = []
        return self.cleaned_data['is_default']

    monkeypatch.setattr(BranchForm, 'clean_is_default', blind)

    client.force_login(owner)
    response = client.post(
        reverse('organizations:branch_update', args=[kushtia.pk]),
        _fields(is_default='on'),
    )

    assert response.status_code == 200
    assert response.context['form'].errors['is_default']
    branch.refresh_from_db()
    kushtia.refresh_from_db()
    assert branch.is_default is True
    assert kushtia.is_default is False


# --- Reading it back --------------------------------------------------------


def test_the_marked_chamber_wins_however_the_names_sort(organization):
    """The bug this column exists for, in one assertion.

    'Kushtia' sorts before 'Mirpur', so the old rule handed the default to a
    chamber that opens on the second Friday of the month.
    """
    _make(organization, 'Kushtia Chamber', 'KUS')
    mirpur = _make(organization, 'Mirpur Chamber', 'MIR', is_default=True)

    assert default_branch(organization) == mirpur


def test_with_nothing_marked_the_lowest_print_order_wins(organization):
    """Not the name. A rename must not move where visits are recorded."""
    _make(organization, 'Alpha Chamber', 'ALP', print_order=3)
    beta = _make(organization, 'Zebra Chamber', 'ZEB', print_order=1)

    assert default_branch(organization) == beta


def test_name_is_the_last_tiebreak(organization):
    """It still decides between chambers the clinic has said nothing about."""
    _make(organization, 'Zebra Chamber', 'ZEB', print_order=1)
    alpha = _make(organization, 'Alpha Chamber', 'ALP', print_order=1)

    assert default_branch(organization) == alpha


def test_a_deactivated_default_falls_back(organization):
    """``is_active`` is the off switch, so a closed chamber is not offered."""
    closed = _make(organization, 'Kushtia Chamber', 'KUS', is_default=True)
    open_one = _make(organization, 'Mirpur Chamber', 'MIR', print_order=2)
    closed.is_active = False
    closed.save(update_fields=['is_active', 'updated_at'])

    assert default_branch(organization) == open_one


def test_a_clinic_with_no_chambers_gets_nothing(organization):
    assert default_branch(organization) is None


def test_the_chamber_list_says_which_one_it_is(client, owner, branch):
    branch.is_default = True
    branch.save(update_fields=['is_default', 'updated_at'])

    client.force_login(owner)
    body = client.get(reverse('organizations:branch_list')).content.decode()

    assert 'Default' in body
