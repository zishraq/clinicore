"""Standing up a real clinic: ``bootstrap_clinic``.

The gap this closes is that there was no way to create an organization without
also acquiring twenty-five invented medicines, and a real catalog cannot replace
them afterwards — products are referenced by prescriptions, invoice lines and
stock movements, so a delete either fails on a PROTECT or orphans history. The
only correct path is never to seed them, which is what these tests pin down.

It was ``bootstrap_demo --empty`` until the two jobs were split into two
commands. The flag is gone; the demo loader now refuses to run outside
development at all, which is asserted in ``test_bootstrap_demo.py``.
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
    '--name=Karim Homeo Hall',
    '--timezone=Asia/Dhaka',
    '--branch=Main Chamber',
    '--admin-phone=01712345678',
    '--admin-name=Dr Ayesha Karim',
)


def _build(*args):
    out = StringIO()
    call_command('bootstrap_clinic', *args, stdout=out)
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


def test_the_branch_is_named_by_the_operator():
    _build(*REAL_CLINIC)
    organization = Organization.objects.get(slug='karim-homeo-hall')
    with organization_context(organization):
        assert Branch.objects.get().name == 'Main Chamber'


@pytest.mark.parametrize('missing', REAL_CLINIC)
def test_every_fact_it_cannot_invent_is_required(missing):
    """None of the five is defaulted, the time zone least of all (ADR 0011)."""
    args = [arg for arg in REAL_CLINIC if arg != missing]
    with pytest.raises(CommandError, match='required'):
        call_command('bootstrap_clinic', *args, stdout=StringIO())
    assert not Organization.objects.exists()


@pytest.mark.parametrize('flag', ['--name', '--branch', '--admin-name'])
def test_a_flag_given_as_blank_is_refused(flag):
    """argparse is happy with an empty string; a clinic with no name is not."""
    args = [f'{flag}=' if arg.startswith(f'{flag}=') else arg for arg in REAL_CLINIC]
    with pytest.raises(CommandError, match='cannot be empty'):
        call_command('bootstrap_clinic', *args, stdout=StringIO())
    assert not Organization.objects.exists()


def test_a_bad_time_zone_is_refused():
    """ADR 0011: a wrong zone is not a crash, it is a silent fallback to UTC."""
    with pytest.raises(CommandError, match='not an IANA time zone'):
        call_command(
            'bootstrap_clinic',
            '--name=Karim Homeo Hall',
            '--timezone=Dhaka/Asia',
            '--branch=Main Chamber',
            '--admin-phone=01712345678',
            '--admin-name=Dr Ayesha Karim',
            stdout=StringIO(),
        )
    assert not Organization.objects.exists()


def test_an_existing_slug_is_refused():
    """A different administrator, so it is the name colliding and not the phone."""
    _build(*REAL_CLINIC)
    with pytest.raises(CommandError, match='already exists'):
        _build(
            '--name=Karim Homeo Hall',
            '--timezone=Asia/Dhaka',
            '--branch=Second Chamber',
            '--admin-phone=01799999999',
            '--admin-name=Someone Else',
        )
    assert Organization.objects.filter(slug='karim-homeo-hall').count() == 1
    assert not User.objects.filter(phone='01799999999').exists()


def test_an_existing_phone_number_is_refused():
    """``add_member`` would raise on the collision; this reports it as a sentence."""
    _build(*REAL_CLINIC)
    with pytest.raises(CommandError, match='already has an account'):
        _build(
            '--name=Second Clinic',
            '--timezone=Asia/Dhaka',
            '--branch=Main',
            '--admin-phone=01712345678',
            '--admin-name=Someone Else',
        )
    assert not Organization.objects.filter(slug='second-clinic').exists()


def test_it_has_no_reset():
    """``--reset`` belongs to the demo loader and never to a real clinic."""
    with pytest.raises(CommandError, match=r'unrecognized arguments|Unknown option'):
        call_command('bootstrap_clinic', '--reset', *REAL_CLINIC, stdout=StringIO())
    assert not Organization.objects.exists()


def test_the_chamber_it_creates_prints_on_prescriptions():
    """``show_on_prescription`` defaults to off so the migration cannot silently
    grow a footer on an existing clinic. A clinic being stood up is the other
    case: it is naming its real chamber, so that chamber prints."""
    _build(*REAL_CLINIC)
    organization = Organization.objects.get(slug='karim-homeo-hall')
    with organization_context(organization):
        branch = Branch.objects.get()
    assert branch.show_on_prescription is True
    assert branch.print_order == 0


def test_the_chambers_printed_details_can_be_given_at_bootstrap():
    _build(
        *REAL_CLINIC,
        '--branch-address=29 Ruhani Market, Mirpur-2',
        '--branch-phone=01568-316095',
        '--branch-hours=5pm - 9pm',
        '--branch-schedule=Every 2nd Friday of the month',
    )
    organization = Organization.objects.get(slug='karim-homeo-hall')
    with organization_context(organization):
        branch = Branch.objects.get()
    assert branch.address == '29 Ruhani Market, Mirpur-2'
    assert branch.phone == '01568-316095'
    assert branch.consulting_hours == '5pm - 9pm'
    assert branch.schedule_note == 'Every 2nd Friday of the month'


def test_it_says_what_still_has_to_be_filled_in_on_screen():
    """Twelve of the printed prescription's fields have no value until somebody
    types one, and none of them is discoverable from the command line."""
    output = _build(*REAL_CLINIC)
    assert 'Settings \u2192 Prescription' in output
    assert 'Settings \u2192 Chambers' in output
