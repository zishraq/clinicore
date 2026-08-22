"""Stand up a real clinic: an organization, one branch, one administrator.

Nothing synthetic. This was ``bootstrap_demo --empty`` until the two jobs were
split, because one command that created either a real clinic or fifteen invented
patients depending on a flag put the whole difference in five characters someone
had to remember at a terminal on a live server.

The clinic's medicines are loaded afterwards with ``import_remedies``. They are
never seeded here and there is no way to ask for them: products are referenced
by prescriptions, invoice lines and stock movements, so an invented catalog
cannot simply be deleted later — the delete either fails on a PROTECT or orphans
the record it was prescribed on. Never creating it is the only correct path.
See "Setting up a new clinic" in docs/RUNBOOK.md.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.crypto import get_random_string

from accounts.models import Role, User
from accounts.services import add_member
from core.exceptions import CannotCreateOrganization
from core.services import create_organization


class Command(BaseCommand):
    help = (
        'Create a real clinic: one organization, one branch, one administrator, '
        'and no data. Use bootstrap_demo for synthetic demo data.'
    )

    def add_arguments(self, parser):
        # All five are required and none is defaulted. A defaulted time zone is
        # the one that matters — UTC would be accepted silently and file every
        # late-evening visit under the wrong date (ADR 0011) — and once one flag
        # has to be typed, a uniform "state every fact" reads better at a
        # terminal than a rule about which four are optional.
        parser.add_argument('--name', required=True, help='Clinic name.')
        parser.add_argument(
            '--timezone',
            required=True,
            help='IANA zone the clinic keeps its day in, e.g. Asia/Dhaka.',
        )
        parser.add_argument('--branch', required=True, help='Name of its one branch.')
        parser.add_argument(
            '--admin-phone', required=True, help="The administrator's phone number."
        )
        parser.add_argument(
            '--admin-name', required=True, help="The administrator's full name."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        name = options['name'].strip()
        zone = options['timezone'].strip()
        branch_name = options['branch'].strip()
        phone = options['admin_phone'].strip()
        admin_name = options['admin_name'].strip()

        blank = [
            flag
            for flag, value in (
                ('--name', name),
                ('--timezone', zone),
                ('--branch', branch_name),
                ('--admin-phone', phone),
                ('--admin-name', admin_name),
            )
            if not value
        ]
        if blank:
            raise CommandError(f'{", ".join(blank)} cannot be empty.')

        if User.objects.filter(phone=phone).exists():
            raise CommandError(f'{phone} already has an account.')

        try:
            organization, _ = create_organization(
                name=name,
                timezone_name=zone,
                branch={'name': branch_name},
            )
        except CannotCreateOrganization as error:
            raise CommandError(str(error)) from error

        # Read out over the phone, so no characters that are ambiguous aloud or
        # in handwriting. Temporary by construction: ``add_member`` sets
        # ``must_change_password``, and the middleware makes it stick (ADR 0013).
        password = get_random_string(
            10, allowed_chars='abcdefghjkmnpqrstuvwxyz23456789'
        )
        add_member(
            organization=organization,
            phone=phone,
            full_name=admin_name,
            role=Role.OWNER,
            password=password,
        )

        self.stdout.write(self.style.SUCCESS(f'\n{name} created. No demo data.'))
        self.stdout.write(f'  Slug         : {organization.slug}')
        self.stdout.write(f'  Time zone    : {zone}')
        self.stdout.write(f'  Branch       : {branch_name}')
        self.stdout.write(f'  Administrator: {phone}  {admin_name}')
        self.stdout.write(f'  Password     : {password}  (must be changed at sign-in)')
        self.stdout.write("\n  Next: load the clinic's own medicine list —")
        self.stdout.write(f'    python manage.py import_remedies {organization.slug}\n')
