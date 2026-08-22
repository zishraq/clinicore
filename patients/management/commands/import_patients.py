"""Load a clinic's existing patient list from a CSV supplied at run time.

Deliberately not shaped like ``import_remedies``. That command ships its data
file inside the app, because a materia medica index is public-domain reference
data. This one reads **real patient records**, so ``--file`` is required, has no
default, and has no bundled fallback: there is no way to run it against a file
that shipped with the code. See docs/adr/0018-importing-real-patient-data.md.

Run ``--dry-run`` first, every time. It writes nothing.
"""

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.context import organization_context, organization_timezone
from organizations.models import Organization
from organizations.services import active_branches
from patients.models import Patient, Sex
from patients.phone import dial_string
from patients.services import generate_patient_code

#: The header this command accepts. A file without it is refused rather than
#: read positionally: column order that can silently transpose ``sex`` and
#: ``phone`` is a mistake nobody notices until they open a record.
REQUIRED_COLUMNS = ('full_name', 'date_of_birth', 'sex', 'phone')

#: Both the label a person types and the value the model stores. Anything else
#: is recorded as UNKNOWN *and* reported by row — see ``Report.sex_unrecognised``.
SEX_BY_INPUT = {
    'm': Sex.MALE,
    'male': Sex.MALE,
    'f': Sex.FEMALE,
    'female': Sex.FEMALE,
    'o': Sex.OTHER,
    'other': Sex.OTHER,
    'u': Sex.UNKNOWN,
    'unknown': Sex.UNKNOWN,
    'not recorded': Sex.UNKNOWN,
}


def clean_header(name: str) -> str:
    """One column name, stripped of a BOM, whitespace and case.

    An Excel or Google Sheets export is UTF-8 with a byte-order mark, and
    ``﻿full_name`` is not ``full_name``. ``utf-8-sig`` removes it when the
    file opens; this is the belt to that pair of braces, because a file that has
    been through two tools can carry one anywhere.
    """
    return (name or '').lstrip('﻿').strip().lower()


@dataclass
class Row:
    """One validated CSV line, ready to become a patient."""

    line: int
    full_name: str
    date_of_birth: date | None
    sex: str
    phone: str

    @property
    def key(self) -> tuple:
        """What decides whether this person is already on file.

        Exact match on all three: name case-insensitively, date of birth, and
        the phone number with its separators removed. Conservative on purpose —
        two family members sharing one phone differ by name, so both import.
        """
        return (self.full_name.casefold(), self.date_of_birth, dial_string(self.phone))


@dataclass
class Report:
    """Counted outcomes. Every number here is printed."""

    read: int = 0
    created: int = 0
    skipped_existing: int = 0
    skipped_removed: int = 0
    skipped_duplicate: int = 0
    #: Blank and unrecognised are two different facts. Blank is legitimate
    #: absence; "Mael" is a data-entry error someone should look at, and
    #: merging them into one number hides the second inside the first.
    sex_blank: int = 0
    sex_unrecognised: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    extra_columns: list = field(default_factory=list)

    def fail(self, line: int, reason: str) -> None:
        self.failures.append((line, reason))


def read_csv(path: Path, report: Report) -> list[tuple[int, dict]]:
    """Every data row as ``(line number, cells)``, with the header validated.

    ``newline=''`` and the csv module between them handle CRLF and quoted
    fields, including a quoted name containing a comma. Line numbers are what
    the operator sees in Excel: the header is line 1.
    """
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        original = reader.fieldnames or []
        headers = [clean_header(name) for name in original]
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise CommandError(
                f'{path} is missing the column(s): {", ".join(missing)}.\n'
                f'Found: {", ".join(headers) or "(no header row)"}\n'
                f'The first line must name the columns: '
                f'{", ".join(REQUIRED_COLUMNS)}'
            )
        # Extra columns are ignored, not refused. A clinic that added an
        # Address column has not made a mistake worth stopping an import for.
        report.extra_columns = [
            name for name in headers if name and name not in REQUIRED_COLUMNS
        ]

        rows = []
        for line, values in enumerate(reader, start=2):
            cells = {
                cleaned: (values.get(source) or '').strip()
                for source, cleaned in zip(original, headers, strict=False)
            }
            # A trailing blank line — the last thing every spreadsheet export
            # writes — is not a patient and is not a failure.
            if not any(cells.get(column) for column in REQUIRED_COLUMNS):
                continue
            rows.append((line, cells))
    return rows


def parse_row(line: int, cells: dict, report: Report) -> Row | None:
    """Validate one row, or record why it cannot be imported."""
    full_name = cells['full_name']
    if not full_name:
        report.fail(line, 'full_name is blank')
        return None

    date_of_birth = None
    written = cells['date_of_birth']
    if written:
        try:
            # Strict ISO, and no format sniffing: "01/02/1998" is two different
            # days on two continents, and a guess writes a birth date nobody
            # re-checks. A clinic exporting d/m/Y gets an explicit option.
            date_of_birth = date.fromisoformat(written)
        except ValueError:
            report.fail(line, f'date_of_birth {written!r} is not YYYY-MM-DD')
            return None
        if date_of_birth > timezone.localdate():
            report.fail(line, f'date_of_birth {written!r} is in the future')
            return None

    written = cells['sex']
    if not written:
        sex = Sex.UNKNOWN
        report.sex_blank += 1
    else:
        sex = SEX_BY_INPUT.get(written.casefold(), Sex.UNKNOWN)
        if written.casefold() not in SEX_BY_INPUT:
            # Imported rather than refused: sex is visible and editable on the
            # patient screen, and one odd cell should not cost the other three
            # correct fields. Reported by row so it can be corrected.
            report.sex_unrecognised.append((line, written))

    return Row(
        line=line,
        full_name=full_name,
        date_of_birth=date_of_birth,
        sex=sex,
        phone=cells['phone'],
    )


def patients_on_file(organization) -> dict[tuple, bool]:
    """Every patient's dedupe key mapped to "is this one still alive".

    Read through ``all_objects``, so a soft-deleted patient is found and the
    row is skipped rather than silently resurrected. One query, so a dry run
    costs the same as a real one.
    """
    on_file: dict[tuple, bool] = {}
    rows = Patient.all_objects.filter(organization=organization).values_list(
        'full_name', 'date_of_birth', 'phone', 'deleted_at'
    )
    for full_name, date_of_birth, phone, deleted_at in rows:
        key = (full_name.casefold(), date_of_birth, dial_string(phone))
        alive = deleted_at is None
        # A live match wins: the removed twin is not the interesting one.
        if alive or key not in on_file:
            on_file[key] = alive
    return on_file


def resolve_branch(organization, code: str):
    """The branch to register these patients at.

    Refused rather than guessed when a multi-branch clinic gives no answer.
    Several hundred patients filed at the wrong chamber — or at none — is a
    data-quality problem that surfaces months later as an empty filter.
    """
    branches = active_branches(organization)
    if code:
        branch = branches.filter(code__iexact=code).first()
        if branch is None:
            available = ', '.join(branches.values_list('code', flat=True)) or 'none'
            raise CommandError(
                f'No active branch with code {code!r} at {organization.name}. '
                f'Available: {available}'
            )
        return branch

    options = list(branches)
    if not options:
        raise CommandError(
            f'{organization.name} has no active branch to register patients at.'
        )
    if len(options) > 1:
        codes = ', '.join(branch.code for branch in options)
        raise CommandError(
            f'{organization.name} has more than one branch, so --branch is '
            f'required. Choose one of: {codes}'
        )
    return options[0]


class Command(BaseCommand):
    help = (
        "Import a clinic's existing patients from a CSV. "
        'Run with --dry-run first: it writes nothing.'
    )

    def add_arguments(self, parser):
        parser.add_argument('slug', help='Slug of the organization to import into.')
        parser.add_argument(
            '--file',
            type=Path,
            required=True,
            help=(
                'CSV to read. Required, with no default: this command reads real '
                'patient data and must never find a file that shipped with the code.'
            ),
        )
        parser.add_argument(
            '--branch',
            default='',
            help=(
                'Code of the branch to register these patients at. Optional only '
                'where the organization has exactly one active branch.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would happen and write nothing.',
        )

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options['slug']).first()
        if organization is None:
            raise CommandError(
                f'No organization with slug {options["slug"]!r}. '
                'Create it first — see the runbook, "Setting up a new clinic".'
            )
        # Coerced rather than trusted: argparse applies ``type=Path``, but
        # ``call_command`` hands the value through untouched and a string here
        # fails on the first attribute access.
        path = Path(options['file'])
        if not path.is_file():
            raise CommandError(f'No such file: {path}')

        dry_run = options['dry_run']
        report = Report()
        raw_rows = read_csv(path, report)

        with organization_context(organization), organization_timezone(organization):
            branch = resolve_branch(organization, options['branch'].strip())
            # Named before anything happens, so a mistyped slug is caught at the
            # dry run rather than after several hundred rows.
            self._announce(organization, branch, path, dry_run)

            rows = []
            for line, cells in raw_rows:
                report.read += 1
                row = parse_row(line, cells, report)
                if row is not None:
                    rows.append(row)

            on_file = patients_on_file(organization)
            pending: dict[tuple, int] = {}
            to_create = []
            for row in rows:
                key = row.key
                if key in on_file:
                    if on_file[key]:
                        report.skipped_existing += 1
                    else:
                        report.skipped_removed += 1
                elif key in pending:
                    report.skipped_duplicate += 1
                else:
                    pending[key] = row.line
                    to_create.append(row)

            if dry_run:
                report.created = len(to_create)
            else:
                self._create(organization, branch, to_create, report)

        self._print_report(report, path, dry_run)

    def _announce(self, organization, branch, path, dry_run: bool) -> None:
        existing = Patient.all_objects.filter(organization=organization).count()
        heading = 'DRY RUN — nothing will be written' if dry_run else 'Importing'
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{heading}'))
        self.stdout.write(f'  Clinic   : {organization.name} ({organization.slug})')
        self.stdout.write(f'  Patients : {existing} already on file')
        self.stdout.write(f'  Branch   : {branch.name} ({branch.code})')
        self.stdout.write(f'  File     : {path}\n')

    def _create(self, organization, branch, rows: list[Row], report: Report) -> None:
        """One transaction for the run, one savepoint per row.

        The import is a single operational act: a crash halfway through leaves
        nothing to reconcile. The savepoints are what stop one bad row aborting
        the rest — an ``IntegrityError`` poisons the enclosing transaction, so
        it has to be rolled back to a point before the next insert.
        """
        with transaction.atomic():
            for row in rows:
                try:
                    with transaction.atomic():
                        Patient.objects.create(
                            organization=organization,
                            # Allocated from the locked counter, inside the
                            # transaction that writes the row, exactly as the
                            # registration form does it.
                            code=generate_patient_code(organization),
                            full_name=row.full_name,
                            date_of_birth=row.date_of_birth,
                            sex=row.sex,
                            phone=row.phone,
                            registered_branch=branch,
                        )
                except IntegrityError as error:
                    report.fail(row.line, f'refused by the database: {error}')
                else:
                    report.created += 1

    def _print_report(self, report: Report, path: Path, dry_run: bool) -> None:
        write = self.stdout.write
        if report.extra_columns:
            write(
                self.style.WARNING(
                    f'\nIgnored extra column(s): {", ".join(report.extra_columns)}'
                )
            )

        if report.sex_unrecognised:
            write(self.style.WARNING('\nSex not recognised — recorded as unknown:'))
            for line, value in report.sex_unrecognised:
                write(f'  row {line}: {value!r}')
            write('  Fix these on the patient screen, or in the file and re-run.')

        if report.failures:
            write(self.style.ERROR('\nNot imported:'))
            for line, reason in report.failures:
                write(f'  row {line}: {reason}')

        verb = 'would be created' if dry_run else 'created'
        write(self.style.SUCCESS(f'\n{"Dry run" if dry_run else "Import"} finished.'))
        write(f'  Read              : {report.read} data rows from {path}')
        write(f'  {verb.capitalize():<18}: {report.created}')
        write(f'  Already on file   : {report.skipped_existing}')
        write(f'  Removed patients  : {report.skipped_removed} (left alone)')
        write(f'  Repeated in file  : {report.skipped_duplicate}')
        write(f'  Sex blank         : {report.sex_blank} (recorded as unknown)')
        write(f'  Sex unrecognised  : {len(report.sex_unrecognised)} (see above)')
        write(f'  Failed            : {len(report.failures)}')
        if dry_run:
            write('\nNothing was written. Re-run without --dry-run to import.')
        else:
            write(
                self.style.WARNING(
                    '\nDelete the file from this server now — it holds patient data.'
                )
            )
