"""Chambers, edited by the clinic rather than in the Django admin.

The schedule note ("every 2nd Friday of the month") is the most volatile field
on the printed prescription — it is an arrangement, and arrangements move. The
admin is for the developer, not the customer (SPEC §6.1), and the alternative
was a Django superuser account for a non-developer on a public box.
"""

import pytest
from django.urls import reverse

from core.context import organization_context
from organizations.models import Branch
from organizations.services import prescription_branches

pytestmark = pytest.mark.django_db


def _fields(**overrides):
    data = {
        'name': 'Kushtia Chamber',
        'code': 'KUS',
        'address': 'Fultala Mor, Chourhas, Kushtia',
        'phone': '01700-000000',
        'consulting_hours': '',
        'schedule_note': 'Every 2nd Friday of the month',
        'print_order': '1',
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def test_an_administrator_can_add_a_chamber(client, owner, organization):
    client.force_login(owner)
    response = client.post(
        reverse('organizations:branch_create'),
        _fields(show_on_prescription='on', is_active='on'),
    )
    assert response.status_code == 302

    with organization_context(organization):
        branch = Branch.objects.get(code='KUS')
    assert branch.schedule_note == 'Every 2nd Friday of the month'
    assert branch.show_on_prescription is True
    assert branch.print_order == 1


def test_editing_a_chamber_redisplays_what_was_saved(client, owner, branch):
    client.force_login(owner)
    client.post(
        reverse('organizations:branch_update', args=[branch.pk]),
        _fields(
            name='Main Chamber',
            code='MAIN',
            consulting_hours='5pm - 9pm',
            schedule_note='',
            is_active='on',
        ),
    )
    branch.refresh_from_db()
    assert branch.consulting_hours == '5pm - 9pm'

    response = client.get(reverse('organizations:branch_update', args=[branch.pk]))
    assert '5pm - 9pm' in response.content.decode()


def test_a_new_chamber_does_not_print_unless_it_is_ticked(client, owner, organization):
    """The column defaults to off so the migration cannot silently grow a footer
    on an existing clinic's prescriptions."""
    client.force_login(owner)
    client.post(reverse('organizations:branch_create'), _fields(is_active='on'))

    with organization_context(organization):
        assert Branch.objects.get(code='KUS').show_on_prescription is False
        assert not prescription_branches(organization).exists()


def test_a_duplicate_code_is_a_field_error_not_a_database_crash(
    client, owner, organization, branch
):
    """The unique constraint is on (organization, code) and ``organization`` is
    not on the form, so without the instance being seeded Django skips the check
    entirely and the save raises IntegrityError."""
    client.force_login(owner)
    response = client.post(
        reverse('organizations:branch_create'),
        _fields(name='Another', code=branch.code, is_active='on'),
    )
    assert response.status_code == 200
    assert response.context['form'].errors


def test_the_same_code_is_free_in_another_organization(
    client, owner, organization, branch, other_organization
):
    with organization_context(other_organization):
        Branch.objects.create(
            organization=other_organization, name='Theirs', code=branch.code
        )
    client.force_login(owner)
    response = client.get(reverse('organizations:branch_list'))
    names = [row.name for row in response.context['branches']]
    assert names == ['Main Chamber']


def test_another_clinics_chamber_is_a_404(client, owner, other_organization):
    with organization_context(other_organization):
        theirs = Branch.objects.create(
            organization=other_organization, name='Theirs', code='THR'
        )
    client.force_login(owner)
    response = client.get(reverse('organizations:branch_update', args=[theirs.pk]))
    assert response.status_code == 404


def test_the_list_keeps_a_deactivated_chamber_reachable(client, owner, branch):
    """There is no delete — Patient.registered_branch is PROTECT — so ``is_active``
    is the off switch, and an off chamber has to stay listed to be turned back on."""
    branch.is_active = False
    branch.save(update_fields=['is_active', 'updated_at'])

    client.force_login(owner)
    response = client.get(reverse('organizations:branch_list'))
    assert list(response.context['branches']) == [branch]


def test_there_is_no_delete_route(client, owner, branch):
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse('organizations:branch_delete', args=[branch.pk])


def test_the_footer_order_is_print_order_then_name(organization):
    with organization_context(organization):
        for name, code, order in (
            ('Zebra', 'Z', 1),
            ('Alpha', 'A', 2),
            ('Beta', 'B', 1),
        ):
            Branch.objects.create(
                organization=organization,
                name=name,
                code=code,
                print_order=order,
                show_on_prescription=True,
            )
    listed = [row.name for row in prescription_branches(organization)]
    assert listed == ['Beta', 'Zebra', 'Alpha']


def test_the_visits_own_chamber_is_left_out_of_the_footer(organization, branch):
    """It is already named in full at the top of the sheet; printing it twice is
    noise. The clinic's design is the evidence — three chambers, one header, two
    in the footer."""
    branch.show_on_prescription = True
    branch.save(update_fields=['show_on_prescription', 'updated_at'])

    assert list(prescription_branches(organization)) == [branch]
    assert not prescription_branches(organization, exclude_pk=branch.pk).exists()


@pytest.mark.parametrize('name', ['branch_list', 'branch_create'])
def test_staff_cannot_reach_the_chamber_screens(client, staff, name):
    client.force_login(staff)
    assert client.get(reverse(f'organizations:{name}')).status_code == 403


def test_staff_cannot_reach_a_chamber_by_direct_url(client, staff, branch):
    client.force_login(staff)
    response = client.get(reverse('organizations:branch_update', args=[branch.pk]))
    assert response.status_code == 403
