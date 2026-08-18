"""Load a clinic's medicine list into the catalog from a plain text file.

The file that ships with this command is a classical homeopathic materia medica,
which is seed data rather than code — nothing here knows what a remedy is, and
pointing ``--file`` at a list of tablets loads that instead (SPEC §1).

Idempotent by relying on the database rather than on the file: every row goes in
behind ``product_name_unique_per_org``, so a second run, a file with the same
name twice, and a name that differs only in case are all the same skip. Checking
first with a query would be both slower and a lie under concurrency.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from catalog.models import Product
from core.context import organization_context
from organizations.models import Organization

#: Ships inside the app, not at the repository root: it is data the command
#: needs at runtime, so it travels with the code that reads it.
DEFAULT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'remedies.txt'


def parse_remedy_file(path: Path) -> list[str]:
    """Names from a comma-separated, alphabetically sectioned list.

    The file is a materia medica index: a single letter on its own line starts a
    section and is not a remedy, entries are comma separated and may run over
    several lines, and the last entry of a section carries a full stop.
    """
    names = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or (len(line) == 1 and line.isalpha()):
            continue
        for entry in line.split(','):
            entry = entry.strip().rstrip('.').strip()
            if entry:
                names.append(entry)
    return names


class Command(BaseCommand):
    help = "Import a medicine list into one organization's catalog."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='Slug of the organization to import into.')
        parser.add_argument(
            '--file',
            type=Path,
            default=DEFAULT_FILE,
            help=f'Text file to read. Defaults to {DEFAULT_FILE.name} in this app.',
        )

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options['slug']).first()
        if organization is None:
            raise CommandError(
                f'No organization with slug {options["slug"]!r}. '
                'Create it first — see the runbook, "Setting up a new clinic".'
            )
        path: Path = options['file']
        if not path.is_file():
            raise CommandError(f'No such file: {path}')

        names = parse_remedy_file(path)
        if not names:
            raise CommandError(f'{path} holds no medicine names.')

        created = skipped = 0
        with organization_context(organization):
            for name in names:
                try:
                    # A savepoint per row: an IntegrityError poisons the
                    # enclosing transaction, so the duplicate has to be rolled
                    # back to something before the next insert is attempted.
                    with transaction.atomic():
                        Product.objects.create(
                            organization=organization,
                            name=name,
                            is_sellable=True,
                            is_stock_tracked=False,
                        )
                except IntegrityError:
                    skipped += 1
                else:
                    created += 1

        self.stdout.write(
            self.style.SUCCESS(f'\n{organization.name}: catalog import finished.')
        )
        self.stdout.write(f'  Read     : {len(names)} from {path}')
        self.stdout.write(f'  Created  : {created}')
        self.stdout.write(f'  Skipped  : {skipped} (already in the catalog)')
