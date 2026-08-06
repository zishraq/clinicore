"""Who may remove a patient (SPEC §6.1).

STAFF registers and corrects patients, but soft-deleting one takes an entire
clinical history out of every screen at once, so it sits with the roles that
own that history. Both halves of the convention in
``templates/partials/_sidebar.html`` are covered here: the link is hidden in the
template *and* the view refuses a direct URL.
"""

import pytest
from django.urls import reverse

from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


def test_staff_cannot_open_the_delete_confirmation(client, staff, patient):
    client.force_login(staff)
    assert client.get(reverse('patients:delete', args=[patient.pk])).status_code == 403


def test_staff_cannot_post_a_delete(client, staff, patient):
    client.force_login(staff)
    response = client.post(reverse('patients:delete', args=[patient.pk]))
    assert response.status_code == 403
    patient.refresh_from_db()
    assert not patient.is_deleted


def test_a_practitioner_removes_the_patient_softly(
    client, practitioner, patient, organization
):
    client.force_login(practitioner)
    response = client.post(reverse('patients:delete', args=[patient.pk]))
    assert response.status_code == 302

    patient.refresh_from_db()
    assert patient.is_deleted
    assert patient.deleted_by == practitioner
    # Gone from the list, still on file: clinical records survive a mis-click.
    with organization_context(organization):
        assert not Patient.objects.filter(pk=patient.pk).exists()
    assert Patient.all_objects.filter(pk=patient.pk).exists()


def test_an_owner_may_also_remove_a_patient(client, owner, patient):
    client.force_login(owner)
    assert client.post(reverse('patients:delete', args=[patient.pk])).status_code == 302


def test_the_remove_button_is_hidden_from_staff(client, staff, practitioner, patient):
    """The presentation half. Hiding a link is not access control, but a button
    that only ever 403s is a bug in its own right."""
    detail = reverse('patients:detail', args=[patient.pk])
    link = 'href="{}"'.format(reverse('patients:delete', args=[patient.pk]))

    client.force_login(staff)
    assert link not in client.get(detail).content.decode()

    client.force_login(practitioner)
    assert link in client.get(detail).content.decode()


def test_another_tenants_patient_cannot_be_removed(
    client, practitioner, other_organization
):
    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Someone Else'
        )
    client.force_login(practitioner)
    response = client.post(reverse('patients:delete', args=[theirs.pk]))
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert not theirs.is_deleted
