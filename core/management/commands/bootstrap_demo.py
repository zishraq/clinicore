"""Load a synthetic demo organization.

Everything this creates is invented: names, phone numbers, complaints, and
medicines. No real patient data or clinic identity is ever committed (SPEC §8).

It refuses to run with ``DJANGO_DEBUG`` off, which is to say it cannot run on a
server at all. The medicines and patients it invents cannot be deleted
afterwards — products and patients are referenced by prescriptions, invoice
lines and stock movements, so a delete either fails on a PROTECT or orphans the
record — so a real clinic acquiring them has no way back. Standing a real clinic
up is ``bootstrap_clinic``, which is a separate command for exactly that reason.
"""

import random
from datetime import time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, Role, User
from billing.models import Invoice, InvoiceItem, LineType, PaymentMethod
from billing.models import Payment as PaymentModel
from billing.services import next_invoice_number, record_payment
from catalog.models import AdviceTemplate, Product
from clinical.models import (
    Encounter,
    EncounterPhoto,
    EncounterStatus,
    ItemType,
    Prescription,
    PrescriptionItem,
)
from clinical.services import delete_photo
from core.context import organization_context, organization_timezone
from core.models import DocumentSequence
from core.services import create_organization
from inventory import services as inventory
from inventory.models import (
    GoodsReceipt,
    GoodsReceiptItem,
    StockBatch,
    StockMovement,
)
from organizations.models import Branch as BranchModel
from organizations.models import Organization
from patients.models import Patient, PatientClinicalProfile, Sex
from scheduling import services as scheduling
from scheduling.models import Appointment, AppointmentStatus, DayPart

DEMO_SLUG = 'demo-clinic'
DEMO_PASSWORD = 'clinicore-demo'
DEMO_CONSULTATION_FEE = Decimal('500.00')

FIRST_NAMES = [
    'Rahima',
    'Kamal',
    'Nusrat',
    'Imran',
    'Shirin',
    'Tanvir',
    'Farida',
    'Sabbir',
    'Anika',
    'Jahangir',
    'Ruma',
    'Mahfuz',
    'Sultana',
    'Rafiq',
    'Nazma',
    'Alamgir',
]
LAST_NAMES = [
    'Begum',
    'Hossain',
    'Akter',
    'Chowdhury',
    'Rahman',
    'Islam',
    'Khatun',
    'Ali',
]
COMPLAINTS = [
    'Persistent dry cough for two weeks',
    'Intermittent headache, worse in the afternoon',
    'Joint pain in both knees',
    'Recurrent acidity after meals',
    'Skin rash on both forearms',
    'Difficulty sleeping for a month',
    'Lower back pain after lifting',
]
ASSESSMENTS = [
    'Likely viral upper respiratory infection',
    'Tension-type headache',
    'Early osteoarthritis',
    'Gastro-oesophageal reflux',
    'Contact dermatitis',
    'Situational insomnia',
    'Mechanical lower back strain',
]
# Generic names only — never a real brand (SPEC §8).
MEDICINES = [
    ('Paracetamol 500mg', 'Analgesic', 'Tablet'),
    ('Ibuprofen 400mg', 'Analgesic', 'Tablet'),
    ('Amoxicillin 500mg', 'Antibiotic', 'Capsule'),
    ('Azithromycin 250mg', 'Antibiotic', 'Tablet'),
    ('Cefixime 200mg', 'Antibiotic', 'Tablet'),
    ('Metronidazole 400mg', 'Antibiotic', 'Tablet'),
    ('Omeprazole 20mg', 'Antacid', 'Capsule'),
    ('Esomeprazole 20mg', 'Antacid', 'Tablet'),
    ('Ranitidine 150mg', 'Antacid', 'Tablet'),
    ('Cetirizine 10mg', 'Antihistamine', 'Tablet'),
    ('Loratadine 10mg', 'Antihistamine', 'Tablet'),
    ('Montelukast 10mg', 'Respiratory', 'Tablet'),
    ('Salbutamol inhaler', 'Respiratory', 'Puff'),
    ('Ambroxol syrup', 'Respiratory', 'ml'),
    ('Dextromethorphan syrup', 'Respiratory', 'ml'),
    ('Metformin 500mg', 'Antidiabetic', 'Tablet'),
    ('Glimepiride 2mg', 'Antidiabetic', 'Tablet'),
    ('Amlodipine 5mg', 'Antihypertensive', 'Tablet'),
    ('Losartan 50mg', 'Antihypertensive', 'Tablet'),
    ('Atorvastatin 10mg', 'Lipid lowering', 'Tablet'),
    ('Calcium + Vitamin D3', 'Supplement', 'Tablet'),
    ('Ferrous sulphate 200mg', 'Supplement', 'Tablet'),
    ('Vitamin B complex', 'Supplement', 'Tablet'),
    ('Zinc sulphate 20mg', 'Supplement', 'Tablet'),
    ('Oral rehydration salts', 'Supplement', 'Sachet'),
]

ADVICE = [
    ('Drink 2 to 3 litres of water through the day.', 'DIET', 'Daily', 'Ongoing'),
    ('Avoid fried and oily food until symptoms settle.', 'DIET', 'Daily', '2 weeks'),
    ('Cut added sugar and sweetened drinks.', 'DIET', 'Daily', 'Ongoing'),
    (
        'Eat smaller meals more often, and not within two hours of bed.',
        'DIET',
        'Daily',
        '1 month',
    ),
    ('Walk briskly for 30 minutes.', 'EXERCISE', 'Five days a week', '3 months'),
    (
        'Gentle stretching for the lower back, morning and evening.',
        'EXERCISE',
        'Twice daily',
        '1 month',
    ),
    (
        'Sleep seven to eight hours, at a consistent time.',
        'SLEEP',
        'Nightly',
        'Ongoing',
    ),
    ('Raise the head of the bed by about 15cm.', 'SLEEP', 'Nightly', '1 month'),
    ('Stop smoking; ask about support if it helps.', 'LIFESTYLE', 'Daily', 'Ongoing'),
    (
        'Keep a symptom diary and bring it to the next visit.',
        'OTHER',
        'Daily',
        'Until follow-up',
    ),
]

DOSAGES = ['1 tablet', '2 tablets', '1 capsule', '5 ml', '10 ml', '1 puff']
FREQUENCIES = ['Once daily', 'Twice daily', 'Three times daily', 'At night']
DURATIONS = ['3 days', '5 days', '7 days', '14 days', '1 month']
TIMINGS = ['After meals', 'Before meals', 'With water', '']


class Command(BaseCommand):
    help = (
        'Create a synthetic demo organization with users, patients, encounters. '
        'Development only. Use bootstrap_clinic to set up a real clinic.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete the existing demo organization first.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            # The guard is the posture, not a flag: there is no --force. What
            # this loader invents cannot be deleted once a prescription, bill or
            # stock movement points at it, so the only safe answer on a machine
            # that is not a development one is no.
            raise CommandError(
                'bootstrap_demo creates invented patients and medicines that '
                'cannot be deleted afterwards, so it refuses to run outside '
                'development. DJANGO_DEBUG is off. To set up a real clinic, '
                'use bootstrap_clinic.'
            )

        random.seed(20260731)

        if options['reset']:
            self._reset()

        if Organization.objects.filter(slug=DEMO_SLUG).exists():
            self.stdout.write(
                self.style.WARNING(
                    'Demo organization already exists; re-run with --reset to rebuild.'
                )
            )
            return

        # The same call ``bootstrap_clinic`` makes, with the demo's own fields
        # on top: one organization, one first branch, in the organization's own
        # scope. Nothing about creating a clinic is duplicated between the two
        # commands.
        organization, branch = create_organization(
            name='Demo Family Clinic',
            slug=DEMO_SLUG,
            timezone_name='Asia/Dhaka',
            currency='BDT',
            default_consultation_fee=DEMO_CONSULTATION_FEE,
            # This clinic prescribes medicines only. The capability ships on by
            # default; the seed is what turns it off (A3).
            advice_enabled=False,
            branch={
                'name': 'Main Chamber',
                'code': 'MAIN',
                'address': '12 Example Road, Dhaka 1207',
                'phone': '09-600-000000',
            },
        )
        organization.branding = {
            **organization.branding,
            'letterhead': '12 Example Road, Dhaka 1207\nTel 09-600-000000',
        }
        organization.save(update_fields=['branding', 'updated_at'])

        # The clock as well as the scoping: this loader books "today", and
        # outside a request nothing else would put it on the clinic's calendar.
        # Between 00:00 and 06:00 in Dhaka the server's date is still yesterday,
        # so a UTC "today" would seed a day the day list does not open on.
        with (
            organization_context(organization),
            organization_timezone(organization),
        ):
            # A second branch, with its own shelf and nothing else. Two of them
            # is what makes the branch field appear on a bill and what makes
            # "which shelf does this line come off" a real question rather than
            # a foregone one — the single-branch demo could not exercise it.
            second_branch = BranchModel.objects.create(
                organization=organization,
                name='Uttara Chamber',
                code='UTT',
                address='45 Example Avenue, Uttara, Dhaka 1230',
                phone='09-600-000001',
            )
            owner = self._member(
                organization, Role.OWNER, '01711000001', 'Dr Ayesha Karim'
            )
            practitioner = self._member(
                organization, Role.PRACTITIONER, '01711000002', 'Dr Sabbir Ahmed'
            )
            self._member(organization, Role.STAFF, '01711000003', 'Nadia Sultana')

            catalog = self._catalogs(organization, owner)
            patients = self._patients(organization, branch, owner)
            encounters = self._encounters(
                organization, branch, practitioner, patients, catalog
            )
            # Before the bills: issuing one is what takes stock off the shelf,
            # so there has to be a shelf first.
            receipts = self._stock(organization, branch, owner, catalog['products'])
            receipts += self._second_shelf(
                organization, second_branch, owner, catalog['products']
            )
            appointments, todays_visits = self._appointments(
                organization, branch, practitioner, patients
            )
            # Only the first of today's visits joins the billing plan. The
            # second stays unbilled on purpose: "no bill yet" is the other
            # thing the day list's payment column has to be able to say.
            invoices = self._invoices(
                organization,
                branch,
                practitioner,
                patients,
                todays_visits[:1] + encounters,
                catalog,
            )

        self.stdout.write(self.style.SUCCESS('\nDemo data ready.'))
        self.stdout.write(f'  Organization : {organization.name}')
        self.stdout.write('  Branches     : 2 (Main Chamber, Uttara Chamber)')
        self.stdout.write(f'  Patients     : {len(patients)}')
        self.stdout.write(f'  Medicines    : {len(catalog["products"])}')
        self.stdout.write(f'  Advice       : {len(catalog["advice"])}')
        self.stdout.write(
            f'  Deliveries   : {len(receipts)} '
            '(some short-dated, so the alerts have something to show)'
        )
        self.stdout.write(f'  Bills        : {len(invoices)} (unpaid, part paid, paid)')
        self.stdout.write(
            f"  Today's list : {len(appointments)} "
            '(expected, waiting, seen, no-show, cancelled)'
        )
        self.stdout.write('\n  Sign in with any of these (password below):')
        self.stdout.write('    01711000001  Administrator Dr Ayesha Karim')
        self.stdout.write('    01711000002  Practitioner  Dr Sabbir Ahmed')
        self.stdout.write('    01711000003  Staff         Nadia Sultana')
        self.stdout.write(f'\n  Password: {DEMO_PASSWORD}\n')

    def _reset(self):
        organization = Organization.objects.filter(slug=DEMO_SLUG).first()
        if organization is None:
            return
        with organization_context(organization):
            # The ledger first: a movement PROTECTs the batch, the receipt line
            # and the invoice line it points at, so nothing below can go until
            # the movements have. These are queryset deletes, which do not go
            # through ``StockMovement.delete`` — the append-only guard is about
            # the application never rewriting history, not about tearing down a
            # synthetic organization (docs/adr/0009-ledger-based-stock.md).
            StockMovement.all_objects.filter(organization=organization).delete()
            GoodsReceiptItem.all_objects.filter(organization=organization).delete()
            GoodsReceipt.all_objects.filter(organization=organization).delete()
            # Payments and lines next: both PROTECT what they point at.
            PaymentModel.all_objects.filter(organization=organization).delete()
            InvoiceItem.all_objects.filter(organization=organization).delete()
            Invoice.all_objects.filter(organization=organization).delete()
            # Batches only now: a bill line that used the batch override still
            # PROTECTs the lot it named, long after its movements have gone.
            StockBatch.all_objects.filter(organization=organization).delete()
            DocumentSequence.all_objects.filter(organization=organization).delete()
            PrescriptionItem.all_objects.filter(organization=organization).delete()
            Prescription.all_objects.filter(organization=organization).delete()
            # Row by row, and before the encounters that CASCADE them. The
            # loader seeds no photographs — no binaries in this repository — but
            # one uploaded by hand while looking at the demo would otherwise be
            # cascaded away as a row and left behind as a file, because Django
            # has not deleted files with models since 1.3. A queryset delete
            # here would do exactly that; ``delete_photo`` removes both.
            for photo in EncounterPhoto.all_objects.filter(organization=organization):
                delete_photo(photo)
            Encounter.all_objects.filter(organization=organization).delete()
            # After the encounters (whose FK to an appointment is SET_NULL) and
            # before the patients and branches an appointment PROTECTs. Nothing
            # here generates one yet, so only a hand-staged row catches this —
            # core/tests/test_bootstrap_demo.py stages one.
            Appointment.all_objects.filter(organization=organization).delete()
            # After the items that PROTECT them.
            Product.all_objects.filter(organization=organization).delete()
            AdviceTemplate.all_objects.filter(organization=organization).delete()
            PatientClinicalProfile.all_objects.filter(
                organization=organization
            ).delete()
            Patient.all_objects.filter(organization=organization).delete()
            BranchModel.all_objects.filter(organization=organization).delete()
        Membership.objects.filter(organization=organization).delete()
        User.objects.filter(phone__startswith='0171100').delete()
        organization.delete()
        self.stdout.write(self.style.WARNING('Removed the previous demo data.'))

    def _member(self, organization, role, phone, full_name) -> User:
        user = User.objects.create_user(
            phone=phone, password=DEMO_PASSWORD, full_name=full_name
        )
        Membership.objects.create(user=user, organization=organization, role=role)
        return user

    def _patients(self, organization, branch, actor) -> list[Patient]:
        patients = []
        for index in range(1, 16):
            name = f'{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}'
            has_dob = random.random() < 0.6
            patient = Patient.objects.create(
                organization=organization,
                created_by=actor,
                code=f'P-{index:04d}',
                full_name=name,
                sex=random.choice([Sex.FEMALE, Sex.MALE]),
                phone=f'017{random.randint(10000000, 99999999)}',
                date_of_birth=(
                    timezone.localdate() - timedelta(days=random.randint(6000, 25000))
                    if has_dob
                    else None
                ),
                approx_age_years=None if has_dob else random.randint(18, 78),
                address=(
                    f'House {random.randint(1, 90)}, '
                    f'Road {random.randint(1, 20)}, Dhaka'
                ),
                registered_branch=branch,
            )
            PatientClinicalProfile.objects.create(
                organization=organization,
                created_by=actor,
                patient=patient,
                medical_history=random.choice(
                    [
                        'No significant past history.',
                        'Hypertension, controlled on medication since 2021.',
                        'Type 2 diabetes, diet controlled.',
                        'Childhood asthma, no recent episodes.',
                    ]
                ),
                allergies=random.choice(['None known', 'Penicillin', 'Dust, pollen']),
            )
            patients.append(patient)
        return patients

    def _catalogs(self, organization, actor) -> dict:
        products = [
            Product.objects.create(
                organization=organization,
                created_by=actor,
                name=name,
                category=category,
                unit=unit,
                sku=f'M{index:03d}',
                # Round-ish prices, generated from the position in the list so a
                # rebuild produces the same catalog.
                sale_price=Decimal(f'{8 + (index * 7) % 45}.00'),
                is_sellable=category != 'Supplement',
                # A clinic dispenses what it sells, so the sellable half of the
                # catalog is what the ledger follows. Supplements stay untracked
                # on purpose: the demo should show both kinds of line on a bill.
                is_stock_tracked=category != 'Supplement',
                # Zero means no alert, so a few products deliberately have none
                # — that is the state a clinic starts in.
                reorder_level=(
                    Decimal('0') if index % 4 == 0 else Decimal(f'{10 + index % 3 * 5}')
                ),
            )
            for index, (name, category, unit) in enumerate(MEDICINES, start=1)
        ]
        advice = [
            AdviceTemplate.objects.create(
                organization=organization,
                created_by=actor,
                text=text,
                category=category,
                default_frequency=frequency,
                default_duration=duration,
            )
            for text, category, frequency, duration in ADVICE
        ]
        return {'products': products, 'advice': advice}

    def _stock(self, organization, branch, actor, products) -> list:
        """Book the shelf in, deliberately including the three alert states.

        A demo where every batch is healthy never shows the dashboard alerts
        that SPEC §6.5 asks for, so the deliveries below are shaped to leave
        some products under their reorder level, some batches expiring inside
        the horizon, and some already past date. Positions in the catalog list
        pick which, so a rebuild produces the same shelf.

        Booked through ``receive_stock`` rather than by writing batches
        directly: the demo should exercise the same path the goods receipt
        screen does, numbered receipts and PURCHASE movements included.
        """
        tracked = [product for product in products if product.is_stock_tracked]
        today = timezone.localdate()
        receipts = []

        # The bulk of the shelf: a year of headroom, comfortably stocked.
        healthy = [
            {
                'product': product,
                'quantity': Decimal(str(40 + (index * 13) % 60)),
                'cost_price': (product.sale_price * Decimal('0.6')).quantize(
                    Decimal('0.01')
                ),
                'lot_number': f'L{index:03d}A',
                'expiry_date': today + timedelta(days=300 + (index * 11) % 200),
            }
            for index, product in enumerate(tracked, start=1)
            if index % 5 != 0
        ]
        receipts.append(
            inventory.receive_stock(
                organization,
                branch=branch,
                actor=actor,
                lines=healthy,
                supplier='Example Pharma Distributors',
                reference='INV-2026-0431',
                received_at=timezone.now() - timedelta(days=45),
                notes='Opening stock.',
            )
        )

        # Every fifth product comes in short, so it sits at or under its
        # reorder level the moment the shelf is counted.
        short = [
            {
                'product': product,
                'quantity': max(product.reorder_level - Decimal('2'), Decimal('1')),
                'cost_price': (product.sale_price * Decimal('0.6')).quantize(
                    Decimal('0.01')
                ),
                'lot_number': f'L{index:03d}B',
                'expiry_date': today + timedelta(days=250),
            }
            for index, product in enumerate(tracked, start=1)
            if index % 5 == 0
        ]
        if short:
            receipts.append(
                inventory.receive_stock(
                    organization,
                    branch=branch,
                    actor=actor,
                    lines=short,
                    supplier='Example Pharma Distributors',
                    reference='INV-2026-0508',
                    received_at=timezone.now() - timedelta(days=20),
                    notes='Part delivery — the rest was back-ordered.',
                )
            )

        # An old delivery that never got used up: two lots on their way out and
        # two already gone. This is what the dashboard alerts are for.
        ageing = []
        for offset, product in enumerate(tracked[:4]):
            days = (12, 25, -6, -40)[offset]
            ageing.append(
                {
                    'product': product,
                    'quantity': Decimal(str(6 + offset * 3)),
                    'cost_price': (product.sale_price * Decimal('0.6')).quantize(
                        Decimal('0.01')
                    ),
                    'lot_number': f'OLD{offset + 1:02d}',
                    'expiry_date': today + timedelta(days=days),
                }
            )
        if ageing:
            receipts.append(
                inventory.receive_stock(
                    organization,
                    branch=branch,
                    actor=actor,
                    lines=ageing,
                    supplier='Metro Medical Supply',
                    reference='INV-2025-1187',
                    received_at=timezone.now() - timedelta(days=310),
                    notes='Short-dated stock taken at a discount.',
                )
            )
        return receipts

    def _second_shelf(self, organization, branch, actor, products) -> list:
        """A smaller, separate shelf at the second branch.

        Deliberately a different subset of the catalog, in its own lot series
        (``U…``), and at different quantities: the point of a second branch in
        the demo is that "is this the right shelf?" has a visible answer. A lot
        held here must never be offered on a bill raised at the other one.
        """
        tracked = [product for product in products if product.is_stock_tracked]
        today = timezone.localdate()
        lines = [
            {
                'product': product,
                'quantity': Decimal(str(15 + (index * 7) % 25)),
                'cost_price': (product.sale_price * Decimal('0.6')).quantize(
                    Decimal('0.01')
                ),
                'lot_number': f'U{index:03d}',
                'expiry_date': today + timedelta(days=280 + (index * 9) % 150),
            }
            # Every other tracked product, so the two shelves overlap without
            # matching — some products are stocked at both, some at one.
            for index, product in enumerate(tracked, start=1)
            if index % 2 == 0
        ]
        if not lines:
            return []
        return [
            inventory.receive_stock(
                organization,
                branch=branch,
                actor=actor,
                lines=lines,
                supplier='Uttara Pharma Supply',
                reference='UPS-2026-0077',
                received_at=timezone.now() - timedelta(days=30),
                notes='Opening stock for the Uttara chamber.',
            )
        ]

    def _encounters(self, organization, branch, practitioner, patients, catalog):
        encounters = []
        for patient in random.sample(patients, 8):
            index = random.randrange(len(COMPLAINTS))
            occurred_at = timezone.now() - timedelta(
                days=random.randint(0, 21), hours=random.randint(0, 8)
            )
            encounter = Encounter.objects.create(
                organization=organization,
                created_by=practitioner,
                patient=patient,
                practitioner=practitioner,
                branch=branch,
                occurred_at=occurred_at,
                chief_complaint=COMPLAINTS[index],
                examination='Vitals stable. No acute distress.',
                assessment=ASSESSMENTS[index],
                plan='Symptomatic treatment. Review if no improvement.',
                follow_up_date=(occurred_at + timedelta(days=14)).date(),
                status=(
                    EncounterStatus.FINALIZED
                    if random.random() < 0.7
                    else EncounterStatus.DRAFT
                ),
            )
            if encounter.status == EncounterStatus.FINALIZED:
                encounter.finalized_at = occurred_at
                encounter.save(update_fields=['finalized_at'])

            prescription = Prescription.objects.create(
                organization=organization,
                created_by=practitioner,
                encounter=encounter,
                general_instructions='Plenty of fluids. Return if symptoms worsen.',
                issued_at=(
                    occurred_at
                    if encounter.status == EncounterStatus.FINALIZED
                    else None
                ),
            )
            order = 0
            for product in random.sample(catalog['products'], random.randint(1, 3)):
                PrescriptionItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    prescription=prescription,
                    item_type=ItemType.MEDICATION,
                    product=product,
                    dosage=random.choice(DOSAGES),
                    frequency=random.choice(FREQUENCIES),
                    duration=random.choice(DURATIONS),
                    instructions=random.choice(TIMINGS),
                    sort_order=order,
                )
                order += 1
            # Most, not all, encounters carry advice — an advice-only or
            # medicine-only prescription must both look right in print.
            for advice in random.sample(catalog['advice'], random.randint(0, 2)):
                PrescriptionItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    prescription=prescription,
                    item_type=ItemType.ADVICE,
                    advice_template=advice,
                    frequency=advice.default_frequency,
                    duration=advice.default_duration,
                    sort_order=order,
                )
                order += 1
            encounters.append(encounter)
        return encounters

    def _appointments(self, organization, branch, practitioner, patients):
        """Today's list, with all five states on it.

        Built through ``scheduling.services`` rather than by writing rows, so
        the demo cannot hold a combination the application would refuse — a seen
        row with no visit behind it, or a cancellation with no reason. Every row
        is dated today because the day list opens on today, and a first screen
        with nothing on it demonstrates nothing.

        Returns the rows and the visits the seen ones produced; the caller
        decides which of those get billed, because "no bill yet" is a state that
        column has to be able to say.
        """
        today = timezone.localdate()
        chosen = random.sample(patients, 9)
        rows = []

        def booked(patient, **when):
            return scheduling.book(
                organization,
                actor=practitioner,
                patient=patient,
                branch=branch,
                scheduled_date=today,
                practitioner=practitioner,
                note=random.choice(COMPLAINTS),
                **when,
            )

        # Expected: two committed times, and one "morning" — the vaguer answer
        # this clinic actually gives, stored as itself rather than rounded up
        # into a time nobody agreed to (docs/adr/0010).
        rows.append(booked(chosen[0], scheduled_time=time(10, 0)))
        rows.append(booked(chosen[1], scheduled_time=time(11, 30)))
        rows.append(booked(chosen[2], day_part=DayPart.MORNING))

        # Waiting: one who booked and turned up, one who simply walked in.
        arrived = booked(chosen[3], scheduled_time=time(9, 30))
        rows.append(
            scheduling.transition(
                arrived, to=AppointmentStatus.ARRIVED, actor=practitioner
            )
        )
        rows.append(
            scheduling.walk_in(
                organization,
                actor=practitioner,
                patient=chosen[4],
                branch=branch,
                practitioner=practitioner,
                note='Fever since last night',
            )
        )

        # Seen: arrived, then consumed by a visit being written against the row.
        visits = []
        for patient, hours_ago in ((chosen[5], 2), (chosen[6], 1)):
            row = booked(patient, scheduled_time=time(8, 30))
            row = scheduling.transition(
                row, to=AppointmentStatus.ARRIVED, actor=practitioner
            )
            occurred_at = timezone.now() - timedelta(hours=hours_ago)
            index = random.randrange(len(COMPLAINTS))
            visit = Encounter.objects.create(
                organization=organization,
                created_by=practitioner,
                patient=patient,
                practitioner=practitioner,
                branch=branch,
                occurred_at=occurred_at,
                chief_complaint=COMPLAINTS[index],
                examination='Vitals stable. No acute distress.',
                assessment=ASSESSMENTS[index],
                plan='Symptomatic treatment. Review if no improvement.',
                status=EncounterStatus.FINALIZED,
                finalized_at=occurred_at,
            )
            rows.append(
                scheduling.transition(
                    row,
                    to=AppointmentStatus.SEEN,
                    actor=practitioner,
                    encounter=visit,
                )
            )
            visits.append(visit)

        # The two endings that are decisions rather than timestamps. NO_SHOW is
        # not terminal — this one can still be marked arrived if they turn up.
        no_show = booked(chosen[7], scheduled_time=time(9, 0))
        rows.append(
            scheduling.transition(
                no_show, to=AppointmentStatus.NO_SHOW, actor=practitioner
            )
        )
        cancelled = booked(chosen[8], day_part=DayPart.AFTERNOON)
        rows.append(
            scheduling.transition(
                cancelled,
                to=AppointmentStatus.CANCELLED,
                actor=practitioner,
                reason='Rang to cancel — travelling',
            )
        )
        return rows, visits

    def _invoices(
        self, organization, branch, practitioner, patients, encounters, catalog
    ):
        """A few bills in each state, so unpaid / part paid / paid are all visible.

        Payments go through the service rather than the model, so the demo data
        exercises the same overpayment guard the UI does. So does the stock:
        issuing a bill is what takes a product off the shelf, and a demo whose
        bills sell tracked products without moving any would contradict the
        feature it exists to show (docs/adr/0009-ledger-based-stock.md).
        """
        billable = [
            encounter
            for encounter in encounters
            if encounter.status == EncounterStatus.FINALIZED
        ]
        invoices = []
        # (encounter or None, how much of the bill has been paid)
        plan = [
            (billable[0] if billable else None, Decimal('0')),
            (billable[1] if len(billable) > 1 else None, Decimal('0.4')),
            (billable[2] if len(billable) > 2 else None, Decimal('1')),
            # A counter sale: products, no consultation behind it.
            (None, Decimal('1')),
            (None, Decimal('0')),
        ]
        for encounter, paid_fraction in plan:
            patient = encounter.patient if encounter else random.choice(patients)
            invoice = Invoice.objects.create(
                organization=organization,
                created_by=practitioner,
                patient=patient,
                encounter=encounter,
                branch=branch,
                currency=organization.currency,
                number=next_invoice_number(organization),
                issued_at=encounter.occurred_at if encounter else timezone.now(),
            )
            order = 0
            if encounter is not None:
                InvoiceItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    invoice=invoice,
                    line_type=LineType.CONSULTATION,
                    name_snapshot=organization.terms['consultation_fee'],
                    quantity=1,
                    unit_price=DEMO_CONSULTATION_FEE,
                    sort_order=order,
                )
                order += 1
            sellable = [
                product for product in catalog['products'] if product.is_sellable
            ]
            for product in random.sample(sellable, random.randint(1, 3)):
                quantity = Decimal(str(random.choice([1, 2, 5, 10])))
                if product.is_stock_tracked:
                    # Never bill more than the shelf holds. Some products are
                    # deliberately short so the reorder alert has something to
                    # say, and a demo build must not die on one of them.
                    usable = inventory.on_hand(
                        organization,
                        product=product,
                        branch=branch,
                        usable_only=True,
                    )
                    quantity = min(quantity, usable)
                    if quantity <= 0:
                        continue
                InvoiceItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    invoice=invoice,
                    line_type=LineType.PRODUCT,
                    product=product,
                    quantity=quantity,
                    unit_price=product.sale_price,
                    sort_order=order,
                )
                order += 1

            # The bill is issued, so the stock leaves now — through the same
            # service the counter uses, FEFO and all.
            inventory.post_sale_movements(
                organization, invoice=invoice, actor=practitioner
            )

            invoice.refresh_from_db()
            due = invoice.amount_due
            amount = (due * paid_fraction).quantize(Decimal('0.01'))
            if amount > 0:
                record_payment(
                    organization,
                    invoice=invoice,
                    actor=practitioner,
                    amount=amount,
                    method=random.choice(
                        [PaymentMethod.CASH, PaymentMethod.MOBILE, PaymentMethod.CARD]
                    ),
                    received_at=invoice.issued_at,
                )
            invoices.append(invoice)
        return invoices
