"""Tenant root and its physical locations."""

import re
from decimal import Decimal
from typing import NamedTuple

from django.db import models
from django.utils.text import slugify

from core.models import OrgOwnedModel, TimeStampedModel
from core.temperature import TemperatureUnit, symbol

__all__ = [
    'CONTACT_MAX_LENGTH',
    'DEFAULT_TERMINOLOGY',
    'PRESCRIBING_FIELDS',
    'WATERMARK_MAX_LENGTH',
    'Branch',
    'Organization',
    'PrescribingField',
    'clean_suggestions',
    'default_branding',
    'default_terminology',
    'hex_color_or',
]

# Branding is org-editable JSON that ends up inside a <style> block, so colours
# are validated rather than escaped — escaping does not protect inside CSS.
_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def hex_color_or(value, fallback: str) -> str:
    """Return ``value`` if it is a plain hex colour, else ``fallback``."""
    return str(value) if _COLOR_RE.match(str(value)) else fallback


# SPEC §7 seed palette. Copied onto every new Organization so rebranding is a
# settings edit rather than a rebuild; base.html emits these as CSS variables.
SEED_PALETTE = {
    'primary': '#176BCE',
    'primary-dark': '#124E96',
    'accent': '#16B8C8',
    'accent-light': '#DFF8FA',
    'surface-alt': '#EEF7FF',
    'background': '#F7FAFC',
    'surface': '#FFFFFF',
    'text': '#1E293B',
    'text-muted': '#64748B',
    'success': '#16A34A',
    'warning': '#D97706',
    'danger': '#DC2626',
}


def default_branding() -> dict:
    """Default value for ``Organization.branding`` (callable, so it migrates)."""
    return {'palette': dict(SEED_PALETTE), 'logo_text': '', 'letterhead': ''}


# SPEC §5 terminology map. Every user-facing word for a domain concept comes
# from here, so a clinic that says "Consultation" or "Appointment" is relabelled
# by editing data — stored values, field names, and URLs never move.
# ``status_*`` keys are looked up as ``status_<stored value lowercased>``, and
# ``role_*`` keys as ``role_<stored role lowercased>``.
DEFAULT_TERMINOLOGY = {
    'encounter': 'Visit',
    'encounter_plural': 'Visits',
    # "Draft" is the only status word a clinician is shown. Open/Completed was
    # two words for a distinction they do not make: a visit is either still
    # being written or it is a visit. A finished one carries no badge at all,
    # so these two labels reach a screen only on the history page.
    'status_draft': 'Draft',
    'status_finalized': 'Finished',
    # A locked record that was later corrected. Deliberately the same label as
    # FINALIZED: staff see two states, and "last edited" on the detail page
    # carries the fact that a correction happened.
    'status_amended': 'Finished',
    'amend': 'Edit',
    # How strong the preparation is: "500mg" to a general practice, "30C" to a
    # classical homeopath, "1:10" to someone dispensing a dilution. One slot,
    # named for what it measures rather than for one specialty's word for it —
    # the clinic that prescribes potencies maps this key to "Potency" and every
    # label in the application follows. See docs/adr/0015-prescribed-strength.md.
    'strength': 'Strength',
    # The physical preparation handed over: globules, a liquid, a tablet, a
    # cream. Named for what it is rather than for one specialty's word for it,
    # exactly like `strength` above — this clinic maps it to "Type". Not
    # `Product.unit`, which is the noun a stock count is measured in and is one
    # value per catalog row; the same remedy goes out as globules for one
    # patient and liquid for the next. See docs/adr/0017-dispensing-details.md.
    'preparation': 'Preparation',
    # How much of it goes home: "2D", "1 ounce", "strip of 10". A string, never
    # a number — `quantity` on a bill line and on a stock movement is a Decimal
    # that arithmetic is done on, and these are container sizes in units
    # nothing else in the system knows. This clinic maps it to "Quantity".
    'pack_size': 'Pack size',
    # The verb for leaving draft. Composed with `encounter` at the call site
    # ("Finish visit") so relabelling the record relabels the button.
    'finish': 'Finish',
    # The printed prescription's letterhead. The design the clinic supplied
    # writes these two chips and the consulting-hours line in Bengali, so they
    # go through the map like every other user-facing word — the rest of that
    # sheet's chrome ("Name:", "Diagnosis", "Doctor's Signature") is English in
    # the clinic's own design and needs no key.
    'registration_number': 'Reg. no.',
    # Labels every phone number the prescription prints: the practitioner's
    # chip in the header and each chamber's in the footer. One key because it
    # is one word — a clinic printing in Bengali writes one label in both
    # places.
    'printed_phone': 'Mobile',
    'consulting_hours': 'Consulting hours',
    # Photographs on a visit. One model covers the patient and the documents
    # they bring in, so there is one word rather than "photo" and "document" —
    # a clinic that mostly photographs referral letters maps these to
    # "Document" and the whole feature relabels. See clinical.EncounterPhoto.
    'photo': 'Photo',
    'photo_plural': 'Photos',
    # Scheduling. One day list covers booked patients and walk-ins alike, so
    # there is one word for the row rather than "appointment" and "queue entry".
    'appointment': 'Appointment',
    'appointment_plural': 'Appointments',
    'walk_in': 'Walk-in',
    # Appointment states. Derived from the row's timestamps, never stored, but
    # they still reach the UI as labels and so still go through the map.
    # "Expected" and "Waiting" rather than "Booked" and "Arrived": the day list
    # is one list with a status filter, and these are the words that read
    # correctly both as a row's badge and as the filter's option.
    'status_booked': 'Expected',
    'status_arrived': 'Waiting',
    'status_seen': 'Seen',
    'status_no_show': 'No show',
    'status_cancelled': 'Cancelled',
    # Billing. "Bill" by default because that is what a patient is handed and
    # what a practitioner says; an organization that invoices corporate clients
    # maps these back to "Invoice" without a migration.
    'invoice': 'Bill',
    'invoice_plural': 'Bills',
    'payment': 'Payment',
    'payment_plural': 'Payments',
    'consultation_fee': 'Consultation fee',
    # What a bill is made of. "Lines" is accounting's word for it; the person
    # reading the bill is looking at what they are being charged for.
    'invoice_line_plural': 'Charges',
    # One row of any of the three formsets — a prescription item, a charge, a
    # delivered product. "Line" is the word a developer uses for a row in a
    # form; the clinic is adding an item to a list.
    'line_item': 'Item',
    'line_item_plural': 'Items',
    # Payment states are derived from the payments received, never stored, but
    # they still reach the UI as labels and so still go through the map.
    'status_unpaid': 'Unpaid',
    'status_partially_paid': 'Part paid',
    'status_paid': 'Paid',
    'status_void': 'Void',
    # Inventory. A clinic that calls a delivery a "purchase order" or a lot a
    # "batch number" relabels here rather than in a template.
    'stock': 'Stock',
    'batch': 'Batch',
    'batch_plural': 'Batches',
    'goods_receipt': 'Goods receipt',
    'goods_receipt_plural': 'Goods receipts',
    'adjustment': 'Adjustment',
    # Stored movement types, rendered through {% status_label %} like any other
    # stored value.
    # Roles. The stored values are OWNER / PRACTITIONER / STAFF and do not
    # move; only these labels reach a screen, via {% role_label %}.
    # "Administrator" rather than "Owner": the job is adding people and setting
    # the clinic's defaults, and "owner" claims more authority than that — it
    # reads as the person who owns the practice, who may well not be the person
    # holding the account.
    'role_owner': 'Administrator',
    'role_practitioner': 'Practitioner',
    'role_staff': 'Staff',
    # Administers the system without treating anybody, so it is never offered as
    # the treating practitioner (ADR 0019). A clinic that would rather call this
    # "Technician" or "IT" overrides the label; the stored value never moves.
    'role_developer': 'Developer',
    # The people who work here. "User" is a software word; a clinic has a team.
    'member': 'Team member',
    'member_plural': 'Team',
    'status_purchase': 'Received',
    'status_sale': 'Sold',
    'status_dispense': 'Dispensed',
    'status_adjustment': 'Adjusted',
    'status_return': 'Returned',
    'status_wastage': 'Written off',
    # The case record (ADR 0020). One structured clinical document per patient,
    # taken from the clinic's paper form. Every one of these names something the
    # doctor already has a word for, and the words differ by tradition — a
    # classical homeopath's "Repertorization" is a physician's ranked
    # differential and an Ayurvedic practitioner's own analysis. The columns are
    # named for what they record; these are what the screens say.
    'case_record': 'Case record',
    'case_record_plural': 'Case records',
    'complaint': 'Complaint',
    'complaint_plural': 'Complaints',
    # What makes a complaint better or worse. The paper form's own word, and
    # generic across traditions.
    'modality': 'Modality',
    'modality_plural': 'Modalities',
    'investigation': 'Investigation',
    'investigation_plural': 'Investigations',
    # The worked shortlist: findings, and the treatments they point to, scored.
    # This clinic maps it to "Repertorization" and the three words below to
    # "Rubric", "Grade" and "Remedy".
    'case_analysis': 'Case analysis',
    'finding': 'Finding',
    'grade': 'Grade',
    'candidate': 'Candidate',
    # "What underlying pattern does this case belong to", as opposed to what
    # disease it is. The neutral word is on the paper form itself, which reads
    # "Miasmatic / constitutional assessment".
    'constitutional_assessment': 'Constitutional assessment',
}

#: Longest an override may be. These are chrome — a nav item, a badge, a button.
_TERM_MAX_LENGTH = 40

#: Longest a suggested value may be, and the width of the columns storing one.
#: Shared by every optional prescribing field so they stay one shape.
PRESCRIBING_MAX_LENGTH = 40

#: Longest one line of the printed prescription's contact strip may be. Four of
#: them share a page width, so this is a layout limit rather than storage.
CONTACT_MAX_LENGTH = 60

#: Longest the watermark may be. It renders very large inside a fixed circle.
WATERMARK_MAX_LENGTH = 8


def clean_suggestions(values, *, max_length: int = PRESCRIBING_MAX_LENGTH) -> list[str]:
    """Suggested values, cleaned. One rule, used on the way in and on the way out.

    Blanks and case-insensitive duplicates are dropped, values are truncated,
    and the original order is kept — these are offered in the order the clinic
    thinks of them, not alphabetically.

    ``max_length`` is a parameter because the second caller is the printed
    prescription's contact bar, whose lines ("WhatsApp / Call: 01700-000000")
    are longer than a potency. The rule is the same; only the width differs.
    """
    seen, cleaned = set(), []
    for value in values or []:
        text = str(value).strip()[:max_length]
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            cleaned.append(text)
    return cleaned


def default_terminology() -> dict:
    """Default value for ``Organization.terminology`` (callable, so it migrates)."""
    return dict(DEFAULT_TERMINOLOGY)


class PrescribingField(NamedTuple):
    """One optional field on a prescription row.

    Each is a whole capability rather than a column: a switch that decides
    whether the clinic records it, the clinic's own word for it (a
    ``terminology`` key of the same name), and the values it usually takes.
    Naming the three consistently is what lets the settings screen, the form
    and the row template loop instead of carrying a copy each — a fourth field
    is an entry here plus its two columns.

    All three are **closed lists**: the clinic's configured values are the only
    values, so every one of them is a ``<select>``. Strength was the last free
    text field and stopped being one on 2026-08-22, when the clinic was asked
    directly and confirmed its nine potencies are the complete range. An
    unlisted value is added in Settings, not typed into a visit. See the
    amendments to docs/adr/0015-prescribed-strength.md and
    docs/adr/0017-dispensing-details.md.
    """

    key: str

    @property
    def enabled_field(self) -> str:
        return f'{self.key}_enabled'

    @property
    def options_field(self) -> str:
        return f'{self.key}_options'


#: In the order they appear on a prescription row, after the item itself.
PRESCRIBING_FIELDS = (
    PrescribingField('strength'),
    PrescribingField('pack_size'),
    PrescribingField('preparation'),
)


class Organization(TimeStampedModel):
    """The tenant. Not org-owned itself, so it keeps a plain manager."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True)
    currency = models.CharField(max_length=3, default='BDT')
    timezone = models.CharField(max_length=64, default='UTC')
    # Prefills the consultation line on a new bill. Money is Decimal everywhere,
    # never float (SPEC §4).
    default_consultation_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Prefilled on the consultation line of a new bill.',
    )
    branding = models.JSONField(default=default_branding, blank=True)
    terminology = models.JSONField(default=default_terminology, blank=True)
    # A capability switch, deliberately a column rather than a terminology key:
    # ``terminology`` names things that exist, this decides whether they exist
    # at all. Turning it off hides the feature, never the data — advice already
    # recorded stays readable on the visits that carry it (A3).
    advice_enabled = models.BooleanField(
        default=True,
        help_text='Offer structured advice alongside medicines when prescribing.',
    )
    # The second capability switch, and off by default where advice is on: a
    # general practice writes the strength into the medicine's name
    # ("Paracetamol 500mg") and would find the column redundant, while a
    # classical homeopath cannot prescribe without it. Off hides the field
    # everywhere it is entered; it never hides strengths already recorded.
    strength_enabled = models.BooleanField(
        default=False,
        help_text='Record how strong each prescribed preparation is.',
    )
    # The usual values, offered as a datalist. Data rather than a code constant:
    # a list of potencies in code would be homeopathy in the schema (SPEC §1),
    # and the clinic next door dispensing dilutions wants different words.
    # Empty is meaningful — the field stays, as free text with no suggestions.
    strength_options = models.JSONField(
        default=list,
        blank=True,
        help_text='Suggested values, offered but never enforced.',
    )
    # The other two optional prescribing fields, same shape as strength above
    # and each its own switch. One capability per field on purpose: a clinic
    # that records what it hands over in ounces does not necessarily record a
    # potency, and "I turned on Type and a Quantity column appeared" is a screen
    # that needs explaining. See docs/adr/0017-dispensing-details.md.
    pack_size_enabled = models.BooleanField(
        default=False,
        help_text='Record how much of each medicine goes home with the patient.',
    )
    pack_size_options = models.JSONField(
        default=list,
        blank=True,
        help_text='Suggested values, offered but never enforced.',
    )
    preparation_enabled = models.BooleanField(
        default=False,
        help_text='Record the physical preparation each medicine is dispensed as.',
    )
    preparation_options = models.JSONField(
        default=list,
        blank=True,
        help_text='Suggested values, offered but never enforced.',
    )
    # The line under the prescription's ℞ area — "bring this with you next
    # time". Printed and read by a patient, so it is a column rather than a
    # `branding` key (docs/adr/0017-dispensing-details.md states the rule).
    prescription_notice = models.TextField(
        blank=True,
        help_text='Printed across the foot of every prescription.',
    )
    # The contact strip along the bottom of the prescription: one line each,
    # printed verbatim and left to right. A list of whole lines rather than
    # label/value pairs, because the clinic's own design styles the two halves
    # identically — splitting them would buy a data shape and a filter and
    # change nothing on paper.
    prescription_contacts = models.JSONField(
        default=list,
        blank=True,
        help_text='One per line, printed along the foot of the prescription.',
    )
    # A capability switch like ``advice_enabled``, one size larger: it hides a
    # whole app rather than half a form. Default True, so nothing changes for a
    # clinic that never touches it — the clinic that is not ready to put money
    # in the system turns it off. Off hides the feature and never the data:
    # every invoice, payment and stock movement stays exactly where it is, and
    # turning it back on restores the lot (A3's rule, and the point of the
    # switch existing rather than the feature being removed).
    billing_enabled = models.BooleanField(
        default=True,
        help_text='Raise bills, take payments, and print receipts.',
    )
    # A capability switch like ``advice_enabled`` and ``strength_enabled``, off
    # by default: a general practice seeing a patient for fifteen minutes does
    # not take a seventy-box constitutional history. It gates **creation and the
    # offer only** — a patient who already has a record still shows the card,
    # still opens it, and can still have a typo fixed. Turning a switch off must
    # not hide a clinical record any more than it may erase one (A3's rule).
    case_record_enabled = models.BooleanField(
        default=False,
        help_text='Take a full structured case history for each patient.',
    )
    # Which unit this clinic *works in*. Presentation only, and deliberately
    # so: temperature is stored in Fahrenheit in one column whatever this says,
    # so flipping the switch relabels the box and converts what is typed into
    # it — it never reinterprets a reading already on file. See
    # core/temperature.py, which amends docs/adr/0020-the-case-record.md.
    temperature_unit = models.CharField(
        max_length=1,
        choices=TemperatureUnit.choices,
        default=TemperatureUnit.FAHRENHEIT,
        help_text='The unit temperatures are entered and shown in.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:60]
        super().save(*args, **kwargs)

    @property
    def temperature_symbol(self) -> str:
        """``°F`` or ``°C``, for every label that names the unit."""
        return symbol(self.temperature_unit)

    @property
    def palette(self) -> dict:
        return {**SEED_PALETTE, **(self.branding or {}).get('palette', {})}

    @property
    def terms(self) -> dict:
        """User-facing labels: the defaults, overlaid with this org's overrides.

        Unknown keys are dropped and values are trimmed, so a typo in the JSON
        cannot leave a template rendering nothing.
        """
        overrides = {
            key: str(value).strip()[:_TERM_MAX_LENGTH]
            for key, value in (self.terminology or {}).items()
            if key in DEFAULT_TERMINOLOGY and str(value).strip()
        }
        return {**DEFAULT_TERMINOLOGY, **overrides}

    def suggestions(self, key: str) -> list[str]:
        """One optional field's suggested values, cleaned.

        A datalist cannot be allowed to break, and these are org-editable JSON
        columns that can hold whatever a settings screen or a loader put there.
        """
        return clean_suggestions(getattr(self, f'{key}_options', None))

    @property
    def strengths(self) -> list[str]:
        """Suggested strengths. The catalog's product form offers these too."""
        return self.suggestions('strength')

    @property
    def contacts(self) -> list[str]:
        """The prescription's contact lines, cleaned on the way out.

        Org-editable JSON reaches a printed document here, so it is cleaned at
        the point of use like ``suggestions`` — a settings screen or a loader
        can put anything in the column.
        """
        return clean_suggestions(
            self.prescription_contacts, max_length=CONTACT_MAX_LENGTH
        )

    @property
    def watermark_text(self) -> str:
        """The mark printed faintly behind the prescription, or '' for none.

        Capped hard: it renders at 140px inside a fixed circle, so a clinic that
        types its whole name gets a ruined sheet rather than a small one.
        """
        return str((self.branding or {}).get('logo_text', '')).strip()[
            :WATERMARK_MAX_LENGTH
        ]

    @property
    def primary_color(self) -> str:
        """Brand colour, safe to interpolate into CSS."""
        return hex_color_or(self.palette.get('primary'), SEED_PALETTE['primary'])

    @property
    def primary_dark_color(self) -> str:
        """Darker brand tone, safe to interpolate into CSS.

        A fallback only: the print stylesheet derives this from ``primary`` with
        ``color-mix`` so that setting one colour is enough, and falls back to
        this where that is unsupported.
        """
        return hex_color_or(
            self.palette.get('primary-dark'), SEED_PALETTE['primary-dark']
        )

    @property
    def primary_tint_color(self) -> str:
        """Very light brand tone behind bars and chips. Same fallback role."""
        return hex_color_or(
            self.palette.get('surface-alt'), SEED_PALETTE['surface-alt']
        )

    @property
    def letterhead(self) -> str:
        """Free-text address block printed under the clinic name."""
        return (self.branding or {}).get('letterhead', '')


class Branch(OrgOwnedModel):
    """A physical location — chamber, clinic room, second site."""

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    # When this chamber sees patients — printed in the prescription's header
    # for the branch the visit happened at. On the branch rather than the
    # organization because a clinic with three chambers keeps three different
    # sets of hours, and the header must follow the encounter.
    consulting_hours = models.CharField(
        max_length=120,
        blank=True,
        help_text='Printed in the header when a visit happens here.',
    )
    # How often this chamber is open at all — "every 2nd Friday". Printed as a
    # chip in the prescription's footer, whole and verbatim: styling part of it
    # would mean either markup in the column or a parser for the ordinal.
    schedule_note = models.CharField(
        max_length=200,
        blank=True,
        help_text='Printed in the footer, e.g. every 2nd Friday of the month.',
    )
    # Defaults to off so the migration cannot silently grow a footer on an
    # existing clinic's prescriptions. ``bootstrap_clinic`` turns it on for the
    # branch it creates, because a clinic being stood up is naming a real
    # chamber it wants printed.
    show_on_prescription = models.BooleanField(
        default=False,
        help_text='List this chamber in the footer of printed prescriptions.',
    )
    print_order = models.PositiveSmallIntegerField(
        default=0, help_text='Lower numbers print first.'
    )
    # Which chamber a new visit and a new appointment open on. A column rather
    # than "whichever sorts first", which is what this was: adding two chambers
    # with Bengali names moved the default onto one that opens on the second
    # Friday of the month, and nothing said so. The clinic states it instead of
    # the collation deciding — see ``services.default_branch``.
    is_default = models.BooleanField(
        default=False,
        help_text='Preselected on a new visit.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        # Deliberately still by name: this ordering feeds every branch dropdown
        # in the application, and ``print_order`` is a fact about one printed
        # document. The prescription's own order comes from
        # ``services.prescription_branches``.
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'], name='branch_code_unique_per_org'
            ),
            # Partial, so it constrains the *true* rows only — without the
            # condition this would allow one default and one non-default per
            # clinic, which is the opposite of what is wanted. The clearing
            # that keeps this satisfiable lives in ``BranchForm.save``.
            models.UniqueConstraint(
                fields=['organization', 'is_default'],
                condition=models.Q(is_default=True),
                name='branch_one_default_per_org',
            ),
        ]

    def __str__(self) -> str:
        return self.name
