"""Photographs on a visit: who may read one, and what survives being uploaded.

The access tests are the point of the feature. These are clinical records with
no public URL at all, so the guarantees worth asserting are that another
clinic's photograph is not found, that STAFF is refused, and that a signed-out
request never receives bytes — see
docs/adr/0014-encounter-photos-served-through-a-view.md.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from clinical.images import MAX_EDGE, MAX_UPLOAD_BYTES
from clinical.models import Encounter, EncounterPhoto
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


def _image_bytes(size=(80, 60), color=(200, 30, 30), fmt='JPEG', **save_kwargs):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def _upload(name='holiday snap.jpg', **kwargs):
    return SimpleUploadedFile(name, _image_bytes(**kwargs), content_type='image/jpeg')


def _stored(encounter):
    """Rows as written, read without an ambient organization (ADR 0005)."""
    return EncounterPhoto.all_objects.filter(encounter=encounter)


def _encounter(organization, branch, practitioner, *, code='P-0001'):
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code=code, full_name='Rahima Begum'
        )
        return Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint='Persistent cough for two weeks',
        )


def _photo(encounter, *, caption='', actor=None):
    from clinical.images import normalize_image
    from clinical.services import attach_photos

    with organization_context(encounter.organization):
        data = normalize_image(_upload())
        return attach_photos(encounter, [data], actor=actor, caption=caption)[0]


@pytest.fixture
def encounter(organization, branch, practitioner):
    return _encounter(organization, branch, practitioner)


@pytest.fixture
def photo(encounter, practitioner):
    return _photo(encounter, caption='Blood report', actor=practitioner)


# --- Access control --------------------------------------------------------


def test_a_practitioner_is_served_the_image(client, practitioner, photo):
    client.force_login(practitioner)
    response = client.get(reverse('clinical:photo', args=[photo.pk]))

    assert response.status_code == 200
    assert response['Content-Type'] == 'image/jpeg'
    # Inline, so tapping a thumbnail opens the picture rather than downloading it.
    assert 'inline' in response['Content-Disposition']
    # `public` here would let a shared proxy hold a patient's photograph.
    assert 'private' in response['Cache-Control']
    assert b''.join(response.streaming_content).startswith(b'\xff\xd8')


def test_another_organizations_photo_is_not_found(
    client, practitioner, other_organization, make_member
):
    """Cross-tenant is a 404 by construction, not by a remembered filter."""
    with organization_context(other_organization):
        from organizations.models import Branch

        their_branch = Branch.objects.create(
            organization=other_organization, name='Their Chamber', code='THEIRS'
        )
    their_doctor = make_member(other_organization, phone='01799999999')
    theirs = _photo(
        _encounter(other_organization, their_branch, their_doctor, code='X-0001')
    )

    client.force_login(practitioner)
    assert client.get(reverse('clinical:photo', args=[theirs.pk])).status_code == 404
    assert (
        client.post(reverse('clinical:photo_delete', args=[theirs.pk])).status_code
        == 404
    )
    # The refusal must not have been a side effect of deleting it first.
    assert EncounterPhoto.all_objects.filter(pk=theirs.pk).exists()


def test_staff_is_forbidden_from_every_photo_url(client, staff, photo, encounter):
    client.force_login(staff)

    assert client.get(reverse('clinical:photo', args=[photo.pk])).status_code == 403
    assert (
        client.post(
            reverse('clinical:photo_upload', args=[encounter.pk]),
            {'photos': _upload(), 'caption': ''},
        ).status_code
        == 403
    )
    assert (
        client.post(reverse('clinical:photo_delete', args=[photo.pk])).status_code
        == 403
    )
    assert EncounterPhoto.all_objects.filter(pk=photo.pk).exists()


def test_a_signed_out_request_is_redirected_never_served(client, photo):
    response = client.get(reverse('clinical:photo', args=[photo.pk]))

    assert response.status_code == 302
    assert '/login' in response['Location']
    assert not hasattr(response, 'streaming_content')


def test_delete_is_refused_over_get(client, practitioner, photo):
    """A URL that deletes on GET is one prefetch away from deleting itself."""
    client.force_login(practitioner)
    assert (
        client.get(reverse('clinical:photo_delete', args=[photo.pk])).status_code == 405
    )
    assert EncounterPhoto.all_objects.filter(pk=photo.pk).exists()


# --- Storage discipline ----------------------------------------------------


def test_the_uploaded_filename_is_discarded_and_the_org_id_is_in_the_path(photo):
    name = photo.image.name

    assert 'holiday' not in name and ' ' not in name
    assert name.startswith(f'encounters/{photo.organization_id}/{photo.encounter_id}/')
    assert name.endswith('.jpg')


def test_a_file_that_is_not_an_image_is_rejected_on_content(
    client, practitioner, encounter
):
    """The extension says jpeg and the bytes do not. The bytes decide."""
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:photo_upload', args=[encounter.pk]),
        {
            'photos': SimpleUploadedFile(
                'notes.jpg',
                b'#!/bin/sh\necho not a picture\n',
                content_type='image/jpeg',
            ),
            'caption': '',
        },
        follow=True,
    )

    assert _stored(encounter).count() == 0
    assert 'not a picture the app can read' in response.content.decode()


def test_an_oversized_file_is_rejected_before_pillow_sees_it(
    client, practitioner, encounter
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:photo_upload', args=[encounter.pk]),
        {
            'photos': SimpleUploadedFile(
                'huge.jpg', b'\xff\xd8' + b'x' * MAX_UPLOAD_BYTES, 'image/jpeg'
            ),
            'caption': '',
        },
        follow=True,
    )

    assert _stored(encounter).count() == 0
    # The message names the limit, so the fix is obvious without a support call.
    assert '10.0 MB' in response.content.decode()


def test_a_large_photo_is_downscaled_and_re_encoded(client, practitioner, encounter):
    """A phone photograph must not reach the disk at its original size."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:photo_upload', args=[encounter.pk]),
        {
            'photos': SimpleUploadedFile(
                'big.png', _image_bytes(size=(3000, 2000), fmt='PNG'), 'image/png'
            ),
            'caption': '',
        },
    )

    stored = _stored(encounter).get()
    with Image.open(stored.image.open('rb')) as image:
        assert max(image.size) == MAX_EDGE
        assert image.size == (MAX_EDGE, round(MAX_EDGE * 2000 / 3000))
        # A PNG went in; JPEG comes out, which is what makes one content type
        # and one extension honest.
        assert image.format == 'JPEG'


def test_exif_orientation_is_applied_rather_than_stored(
    client, practitioner, encounter
):
    """A portrait phone photo is stored sideways without this, and the bytes
    are perfectly valid either way — so only a pixel assertion catches it."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Rotate 90° clockwise for display.
    landscape = _image_bytes(size=(100, 50), exif=exif)

    client.force_login(practitioner)
    client.post(
        reverse('clinical:photo_upload', args=[encounter.pk]),
        {'photos': SimpleUploadedFile('rotated.jpg', landscape, 'image/jpeg')},
    )

    stored = _stored(encounter).get()
    with Image.open(stored.image.open('rb')) as image:
        assert image.size == (50, 100), 'exif_transpose did not rotate the pixels'
        # Dropped, not copied: it carries the GPS position of the clinic.
        assert not image.getexif().get(0x0112)


def test_delete_removes_the_row_and_the_file(client, practitioner, photo):
    from django.core.files.storage import default_storage

    name = photo.image.name
    assert default_storage.exists(name)

    client.force_login(practitioner)
    client.post(reverse('clinical:photo_delete', args=[photo.pk]))

    assert not EncounterPhoto.all_objects.filter(pk=photo.pk).exists()
    assert not default_storage.exists(name)


# --- The two upload surfaces -----------------------------------------------


def test_photos_ride_along_with_the_consultation_form(
    client, practitioner, branch, organization
):
    """A visit being created has no row yet, so the files travel with the POST."""
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0009', full_name='Kamal Hossain'
        )
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        {
            'patient': patient.pk,
            'branch': branch.pk,
            'practitioner': practitioner.pk,
            'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'chief_complaint': 'Rash on both forearms',
            'items-TOTAL_FORMS': '0',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'print_size': 'A5',
            'photos': [_upload('one.jpg'), _upload('two.jpg')],
            'photo_caption': 'Rash, both arms',
        },
    )

    assert response.status_code == 302
    with organization_context(organization):
        encounter = Encounter.objects.get(patient=patient)
        assert encounter.photos.count() == 2
        # One caption covers the batch; a multiple file input has nowhere to
        # put one per file.
        assert {p.caption for p in encounter.photos.all()} == {'Rash, both arms'}


def test_a_rejected_photo_does_not_cost_the_consultation_note(
    client, practitioner, branch, organization
):
    """The whole reason validation lives on the form rather than in the view."""
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0010', full_name='Nusrat Akter'
        )
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        {
            'patient': patient.pk,
            'branch': branch.pk,
            'practitioner': practitioner.pk,
            'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'chief_complaint': 'Joint pain in both knees',
            'items-TOTAL_FORMS': '0',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'print_size': 'A5',
            'photos': SimpleUploadedFile('bad.jpg', b'not an image', 'image/jpeg'),
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'not a picture the app can read' in body
    # The typed note is still in the redisplayed form.
    assert 'Joint pain in both knees' in body
    with organization_context(organization):
        assert not Encounter.objects.filter(patient=patient).exists()


def test_the_visit_page_links_photos_through_the_protected_view(
    client, practitioner, photo, encounter
):
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:encounter_detail', args=[encounter.pk])
    ).content.decode()

    assert reverse('clinical:photo', args=[photo.pk]) in body
    assert 'Blood report' in body
    # Never the raw file path: that URL is served by nothing.
    assert photo.image.name not in body


def test_the_printed_prescription_shows_no_photos(
    client, practitioner, photo, encounter
):
    """Photographs stay on screen — not part of what a patient is handed."""
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[encounter.pk])
    ).content.decode()

    assert reverse('clinical:photo', args=[photo.pk]) not in body
    assert 'Blood report' not in body
