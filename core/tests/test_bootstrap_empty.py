"""Standing up a real clinic: ``bootstrap_demo --empty``.

The gap this closes is that there was no way to create an organization without
also acquiring twenty-five invented medicines, and a real catalog cannot replace
them afterwards — products are referenced by prescriptions, invoice lines and
stock movements, so a delete either fails on a PROTECT or orphans history. The
only correct path is never to seed them, which is what these tests pin down.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.models import Membership, Role, User
from catalog.models import AdviceTemplate, Product
from clinical.models import Encounter
from core.context import organization_context
from organizations.models import Branch, Organization
from patients.models import Patient

pytestmark = pytest.mark.django_db

REAL_CLINIC = (
    '--empty',
    '--name=Karim Homeo Hall',
    '--timezone=Asia/Dhaka',
    '--admin-phone=01712345678',
    '--admin-name=Dr Ayesha Karim',
)


def _build(*args):
    out = StringIO()
    call_command('bootstrap_demo', *args, stdout=out)
    return out.getvalue()


def test_it_creates_the_clinic_and_nothing_else():
    _build(*REAL_CLINIC)
    organization = Organization.objects.get(slug='karim-homeo-hall')
    assert organization.name == 'Karim Homeo Hall'
    assert organization.timezone == 'Asia/Dhaka'

    with organization_context(organization):
        assert Branch.objects.count() == 1
        # The whole point: no synthetic catalog to have to remove later.
        assert Product.objects.count() == 0
        assert AdviceTemplate.objects.count() == 0
        assert Patient.objects.count() == 0
        assert Encounter.objects.count() == 0


def test_it_creates_one_administrator_who_must_change_the_password():
    output = _build(*REAL_CLINIC)
    user = User.objects.get(phone='01712345678')
    assert user.full_name == 'Dr Ayesha Karim'
    assert user.must_change_password is True

    membership = Membership.objects.get(user=user)
    assert membership.role == Role.OWNER
    assert membership.is_active is True

    # Read out over the phone, so it has to be printed once and only once.
    assert 'Password' in output
    assert user.check_password(_password_from(output))


def _password_from(output: str) -> str:
    line = next(line for line in output.splitlines() if 'Password' in line)
    return line.split(':', 1)[1].split('(')[0].strip()


def test_it_names_the_next_step():
    """A clinic with no medicines is not finished, so the command says so."""
    output = _build(*REAL_CLINIC)
    assert 'import_remedies karim-homeo-hall' in output


def test_the_branch_can_be_named():
    _build(*REAL_CLINIC, '--branch=Mirpur Chamber')
    organization = Organization.objects.get(slug='karim-homeo-hall')
    with organization_context(organization):
        assert Branch.objects.get().name == 'Mirpur Chamber'


def test_the_demo_path_is_untouched():
    """Demo data stays synthetic and stays the default."""
    call_command('bootstrap_demo', stdout=StringIO())
    demo = Organization.objects.get(slug='demo-clinic')
    with organization_context(demo):
        assert Product.objects.count() == 25


@pytest.mark.parametrize(
    'missing',
    [
        '--name=Karim Homeo Hall',
        '--admin-phone=01712345678',
        '--admin-name=Dr Ayesha Karim',
    ],
)
def test_the_three_facts_it_cannot_invent_are_required(missing):
    args = ['--empty'] + [arg for arg in REAL_CLINIC[1:] if arg != missing]
    with pytest.raises(CommandError, match='--empty needs'):
        call_command('bootstrap_demo', *args, stdout=StringIO())
    assert not Organization.objects.exclude(slug='demo-clinic').exists()


def test_a_bad_time_zone_is_refused():
    """ADR 0011: a wrong zone is not a crash, it is a silent fallback to UTC."""
    with pytest.raises(CommandError, match='not an IANA time zone'):
        call_command(
            'bootstrap_demo',
            '--empty',
            '--name=Karim Homeo Hall',
            '--timezone=Dhaka/Asia',
            '--admin-phone=01712345678',
            '--admin-name=Dr Ayesha Karim',
            stdout=StringIO(),
        )
    assert not Organization.objects.exists()


def test_an_existing_slug_is_refused():
    _build(*REAL_CLINIC)
    with pytest.raises(CommandError, match='already exists'):
        _build(*REAL_CLINIC)
    assert Organization.objects.filter(slug='karim-homeo-hall').count() == 1


def test_an_existing_phone_number_is_refused():
    """``add_member`` would raise on the collision; this reports it as a sentence."""
    _build(*REAL_CLINIC)
    with pytest.raises(CommandError, match='already has an account'):
        _build(
            '--empty',
            '--name=Second Clinic',
            '--admin-phone=01712345678',
            '--admin-name=Someone Else',
        )
    assert not Organization.objects.filter(slug='second-clinic').exists()


def test_reset_cannot_be_combined_with_empty():
    """--reset deletes the demo organization and has nothing to say about a real one."""
    with pytest.raises(CommandError, match='only ever applies to the demo'):
        call_command('bootstrap_demo', '--reset', *REAL_CLINIC, stdout=StringIO())
