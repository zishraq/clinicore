"""Every page in the application loads. That is all this asserts, and it is new.

The rest of the suite tests specific behaviours very thoroughly and none of it
tests that a given URL simply responds — so a view could 500 for everyone,
forever, and stay green. This walks the URLconf itself rather than a hand-kept
list, so a view added tomorrow is covered tomorrow rather than whenever somebody
remembers.

**A 5xx is the only failure.** 200, 302, 403, 404, 405 are all legitimate
answers: a role check refusing, a POST-only route rejecting a GET, a redirect to
login. What is never legitimate is an unhandled exception.

Both a populated and an empty organization are walked. Empty-state crashes are
their own class of bug — a template that indexes ``[0]`` or a view that averages
an empty queryset only fails on the day a clinic first opens the screen, which
is the worst possible day.
"""

import pytest
from django.core.management import call_command
from django.urls import NoReverseMatch, get_resolver, reverse

from accounts.models import Membership, Role, User
from core.context import organization_context
from organizations.models import Organization

pytestmark = pytest.mark.django_db

#: Django's own admin. It is for the developer, not the customer (SPEC §6.1),
#: it contributes several hundred patterns, and ``core.admin`` already has its
#: own tests for the org-scoped changelist.
EXCLUDED_NAMESPACES = ('admin',)

#: Walked with a GET, so a view that logs the client out would silently make
#: every later request in the loop anonymous and turn this into a test of the
#: login redirect. Excluded rather than reordered, because "logout works" is not
#: what this file is for.
EXCLUDED_NAMES = frozenset({'accounts:logout'})


def _named_patterns(resolver=None, prefix=''):
    """Every named URL pattern, with the names of the arguments it needs."""
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        if hasattr(pattern, 'url_patterns'):
            namespace = f'{pattern.namespace}:' if pattern.namespace else ''
            yield from _named_patterns(pattern, prefix + namespace)
        elif pattern.name:
            name = prefix + pattern.name
            if name.startswith(EXCLUDED_NAMESPACES) or name in EXCLUDED_NAMES:
                continue
            yield name, sorted(pattern.pattern.regex.groupindex)


def _first_pk(model, organization, **filters):
    """The pk of any row of ``model`` belonging to ``organization``."""
    manager = getattr(model, 'all_objects', model.objects)
    row = manager.filter(organization=organization, **filters).order_by('pk').first()
    return row.pk if row else None


def _argument_sources() -> dict:
    """Where each URL argument's value comes from, keyed by URL name.

    Declared rather than guessed, because ``pk`` means a different model in
    almost every namespace and a wrong guess produces a 404 that looks like a
    pass. Each entry is a callable taking the organization and returning a dict
    of reverse() kwargs, or None when the fixture data cannot supply one.

    ``test_every_parameterised_url_declares_its_arguments`` fails when a new
    view lands without an entry here, which is what keeps the coverage honest.
    """
    from billing.models import Invoice, Payment
    from catalog.models import AdviceTemplate, Product
    from clinical.models import Encounter, EncounterPhoto
    from inventory.models import GoodsReceipt, StockBatch
    from patients.models import Patient
    from scheduling.models import Appointment

    def by(model, **filters):
        def resolve(organization):
            pk = _first_pk(model, organization, **filters)
            return {'pk': pk} if pk else None

        return resolve

    def payment(organization):
        row = (
            Payment.all_objects.filter(organization=organization).order_by('pk').first()
        )
        return {'pk': row.invoice_id, 'payment_pk': row.pk} if row else None

    def switch(organization):
        return {'organization_id': organization.pk}

    def membership(organization):
        row = (
            Membership.objects.filter(organization=organization).order_by('pk').first()
        )
        return {'pk': row.pk} if row else None

    return {
        'accounts:member_reset_password': membership,
        'accounts:member_toggle_active': membership,
        'accounts:member_update': membership,
        'accounts:switch_organization': switch,
        'billing:invoice_detail': by(Invoice),
        'billing:invoice_update': by(Invoice),
        'billing:invoice_void': by(Invoice),
        'billing:payment_create': by(Invoice),
        'billing:payment_void': payment,
        'billing:receipt_print': by(Invoice),
        'catalog:advice_toggle_active': by(AdviceTemplate),
        'catalog:advice_update': by(AdviceTemplate),
        'catalog:product_toggle_active': by(Product),
        'catalog:product_update': by(Product),
        'clinical:encounter_detail': by(Encounter),
        'clinical:encounter_finalize': by(Encounter),
        'clinical:encounter_history': by(Encounter),
        'clinical:encounter_update': by(Encounter),
        'clinical:photo': by(EncounterPhoto),
        'clinical:photo_delete': by(EncounterPhoto),
        # Takes the *encounter* it is uploading to, not a photo.
        'clinical:photo_upload': by(Encounter),
        'clinical:prescription_print': by(Encounter),
        'inventory:adjustment_create': by(StockBatch),
        'inventory:product_stock': by(Product, is_stock_tracked=True),
        'inventory:receipt_detail': by(GoodsReceipt),
        'patients:clinical_profile': by(Patient),
        'patients:delete': by(Patient),
        'patients:detail': by(Patient),
        'patients:update': by(Patient),
        'scheduling:cancel': by(Appointment),
        'scheduling:mark_arrived': by(Appointment),
        'scheduling:no_show': by(Appointment),
    }


def _walk(client, organization, user, *, label) -> list[str]:
    """GET every reachable URL as ``user``; return a report line per 5xx."""
    sources = _argument_sources()
    failures = []
    # Without this the test client re-raises a view's exception, so the walk
    # dies on the first broken page and reports one traceback. Turning it into
    # a 500 response is what lets a single run list *every* page that is down,
    # which is the whole point of walking them.
    client.raise_request_exception = False
    for name, arguments in sorted(_named_patterns()):
        with organization_context(organization):
            kwargs = sources[name](organization) if arguments else {}
        if kwargs is None:
            # No row of that kind exists — expected on the empty organization.
            continue
        try:
            url = reverse(name, kwargs=kwargs)
        except NoReverseMatch as error:  # pragma: no cover - a broken entry above
            failures.append(f'{label}: {name} could not be reversed ({error})')
            continue
        # Re-established per URL: a view that rotates the session or signs the
        # user out would otherwise quietly make the rest of the walk anonymous.
        client.force_login(user)
        response = client.get(url)
        if response.status_code >= 500:
            failures.append(f'{label}: GET {url} ({name}) -> {response.status_code}')
    return failures


@pytest.fixture
def populated(db):
    """A whole clinic, built by the loader that already builds one.

    Using ``bootstrap_demo`` rather than hand-rolled fixtures is deliberate: it
    is maintained alongside the models, so this walk keeps seeing realistic rows
    for new tables without a second seeding path to keep in step.
    """
    from django.core.files.base import ContentFile

    from clinical.models import Encounter, EncounterPhoto

    call_command('bootstrap_demo', stdout=_Discard())
    organization = Organization.objects.get(slug='demo-clinic')
    # The loader seeds no photographs on purpose (no binaries in the repo), so
    # the two photo URLs would be skipped for want of a row. One staged here
    # keeps them in the walk.
    with organization_context(organization):
        photo = EncounterPhoto(
            organization=organization, encounter=Encounter.objects.first()
        )
        photo.image.save('probe.jpg', ContentFile(b'not a real jpeg'), save=False)
        photo.save()
    return organization


@pytest.fixture
def empty(organization, branch):
    """An organization with a branch and nothing else in it."""
    return organization


class _Discard:
    def write(self, *args, **kwargs):
        pass

    def flush(self):
        pass


ROLES = [Role.OWNER, Role.PRACTITIONER, Role.STAFF]


@pytest.mark.parametrize('role', ROLES)
def test_no_page_raises_on_a_populated_clinic(client, populated, role):
    user = User.objects.get(phone=f'0171100000{ROLES.index(role) + 1}')
    assert Membership.objects.get(user=user, organization=populated).role == role

    failures = _walk(client, populated, user, label=role)

    assert not failures, 'views raised a 500:\n' + '\n'.join(failures)


@pytest.mark.parametrize('role', ROLES)
def test_no_page_raises_on_a_brand_new_clinic(client, empty, make_member, role):
    """Day one, before anything has been entered.

    Nothing here has a patient, a visit, a bill or a shelf, so this is the walk
    that catches an empty state nobody rendered — the class of bug whose first
    victim is always a real clinic's first morning.
    """
    user = make_member(empty, role=role, phone='01755500001')

    failures = _walk(client, empty, user, label=f'{role} (empty clinic)')

    assert not failures, 'views raised a 500:\n' + '\n'.join(failures)


def test_every_parameterised_url_declares_its_arguments():
    """The guard that keeps this file honest as the app grows.

    Discovery is automatic, but an argument-taking URL needs a row to point at,
    and nothing can infer which model. A new view with a ``<int:pk>`` fails here
    on the day it is added rather than being skipped in silence.
    """
    sources = _argument_sources()
    undeclared = sorted(
        name
        for name, arguments in _named_patterns()
        if arguments and name not in sources
    )

    assert not undeclared, (
        'these URLs take arguments and have no entry in _argument_sources():\n'
        + '\n'.join(undeclared)
        + '\n\nAdd one so the smoke walk can reach them.'
    )


def test_the_walk_actually_reaches_most_of_the_application(client, populated):
    """A walk that silently stopped reaching pages would pass forever.

    Without this, a bug in argument resolution (everything returning None) or in
    pattern discovery turns the tests above into an expensive no-op.
    """
    user = User.objects.get(phone='01711000001')
    sources = _argument_sources()
    reached = 0
    for name, arguments in _named_patterns():
        with organization_context(populated):
            kwargs = sources[name](populated) if arguments else {}
        if kwargs is None:
            continue
        client.force_login(user)
        if client.get(reverse(name, kwargs=kwargs)).status_code == 200:
            reached += 1

    assert reached >= 40, f'only {reached} URLs returned 200; the walk is not walking'
