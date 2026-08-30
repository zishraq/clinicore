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
        # The chamber's own details. Optional because a clinic can be stood up
        # before its letterhead is decided, and every one of them is editable
        # afterwards at Settings → Chambers. They are flags rather than a
        # second command because ``create_organization`` already takes the
        # branch's fields as a dict — this widens what is put in it.
        parser.add_argument('--branch-address', default='', help='Its address.')
        parser.add_argument('--branch-phone', default='', help='Its phone number.')
        parser.add_argument(
            '--branch-hours',
            default='',
            help='When it sees patients, printed in the prescription header.',
        )
        parser.add_argument(
            '--branch-schedule',
            default='',
            help='When it opens, printed in the prescription footer.',
        )
        # A flag rather than a clinic name in the code: this command is the
        # generic one, and the clinic it happens to be standing up next is not
        # a fact the product knows (SPEC §1). Off is a switch, not a decision
        # about the schema — Settings → Features turns it back on and every
        # bill recorded meanwhile is still there.
        parser.add_argument(
            '--no-billing',
            action='store_true',
            help='Start with bills, payments and receipts hidden.',
        )
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
                billing_enabled=not options['no_billing'],
                branch={
                    'name': branch_name,
                    'address': options['branch_address'].strip(),
                    'phone': options['branch_phone'].strip(),
                    'consulting_hours': options['branch_hours'].strip(),
                    'schedule_note': options['branch_schedule'].strip(),
                    # The column defaults to off so that the migration cannot
                    # grow a footer on an existing clinic's prescriptions. A
                    # clinic being stood up is different: it is naming its real
                    # chamber, so that chamber prints.
                    'show_on_prescription': True,
                    'print_order': 0,
                    # The only chamber there is, so it is the one new visits
                    # open on. The migration marks nothing for the clinics
                    # already running — they keep today's behaviour until
                    # somebody ticks the box at Settings → Chambers.
                    'is_default': True,
                },
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
        if options['no_billing']:
            self.stdout.write('  Billing      : off  (Settings → Features turns it on)')
        self.stdout.write(f'  Password     : {password}  (must be changed at sign-in)')
        self.stdout.write("\n  Next: load the clinic's own medicine list —")
        self.stdout.write(f'    python manage.py import_remedies {organization.slug}')
        self.stdout.write(
            '\n  Then sign in and fill in the printed letterhead:\n'
            '    Settings → Prescription  (notice, contact strip, watermark, colour)\n'
            '    Settings → Chambers      (address, hours, schedule, print order)\n'
            '    Your account             (degrees, designation, registration)\n'
        )
