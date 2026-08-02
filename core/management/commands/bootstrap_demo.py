"""Load a synthetic demo organization.

Everything here is invented: names, phone numbers, complaints, and medicines.
No real patient data or clinic identity is ever committed (SPEC §8).
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, Role, User
from billing.models import Invoice, InvoiceItem, LineType, PaymentMethod
from billing.models import Payment as PaymentModel
from billing.services import next_invoice_number, record_payment
from catalog.models import AdviceTemplate, Product
from clinical.models import (
    Encounter,
    EncounterStatus,
    ItemType,
    Prescription,
    PrescriptionItem,
)
from core.context import organization_context
from core.models import DocumentSequence
from organizations.models import Branch as BranchModel
from organizations.models import Organization
from patients.models import Patient, PatientClinicalProfile, Sex

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
    help = 'Create a synthetic demo organization with users, patients, encounters.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete the existing demo organization first.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260731)

        if options['reset']:
            self._reset()

        organization, created = Organization.objects.get_or_create(
            slug=DEMO_SLUG,
            defaults={
                'name': 'Demo Family Clinic',
                'currency': 'BDT',
                'timezone': 'Asia/Dhaka',
                'default_consultation_fee': DEMO_CONSULTATION_FEE,
            },
        )
        if not created:
            self.stdout.write(
                self.style.WARNING(
                    'Demo organization already exists; re-run with --reset to rebuild.'
                )
            )
            return

        organization.branding = {
            **organization.branding,
            'letterhead': '12 Example Road, Dhaka 1207\nTel 09-600-000000',
        }
        organization.save(update_fields=['branding', 'updated_at'])

        with organization_context(organization):
            branch = BranchModel.objects.create(
                organization=organization,
                name='Main Chamber',
                code='MAIN',
                address='12 Example Road, Dhaka 1207',
                phone='09-600-000000',
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
            invoices = self._invoices(
                organization, practitioner, patients, encounters, catalog
            )

        self.stdout.write(self.style.SUCCESS('\nDemo data ready.'))
        self.stdout.write(f'  Organization : {organization.name}')
        self.stdout.write(f'  Patients     : {len(patients)}')
        self.stdout.write(f'  Medicines    : {len(catalog["products"])}')
        self.stdout.write(f'  Advice       : {len(catalog["advice"])}')
        self.stdout.write(f'  Bills        : {len(invoices)} (unpaid, part paid, paid)')
        self.stdout.write('\n  Sign in with any of these (password below):')
        self.stdout.write('    01711000001  Owner        Dr Ayesha Karim')
        self.stdout.write('    01711000002  Practitioner Dr Sabbir Ahmed')
        self.stdout.write('    01711000003  Staff        Nadia Sultana')
        self.stdout.write(f'\n  Password: {DEMO_PASSWORD}\n')

    def _reset(self):
        organization = Organization.objects.filter(slug=DEMO_SLUG).first()
        if organization is None:
            return
        with organization_context(organization):
            # Payments and lines first: both PROTECT what they point at.
            PaymentModel.all_objects.filter(organization=organization).delete()
            InvoiceItem.all_objects.filter(organization=organization).delete()
            Invoice.all_objects.filter(organization=organization).delete()
            DocumentSequence.all_objects.filter(organization=organization).delete()
            PrescriptionItem.all_objects.filter(organization=organization).delete()
            Prescription.all_objects.filter(organization=organization).delete()
            Encounter.all_objects.filter(organization=organization).delete()
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

    def _invoices(self, organization, practitioner, patients, encounters, catalog):
        """A few bills in each state, so unpaid / part paid / paid are all visible.

        Payments go through the service rather than the model, so the demo data
        exercises the same overpayment guard the UI does.
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
                InvoiceItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    invoice=invoice,
                    line_type=LineType.PRODUCT,
                    product=product,
                    quantity=random.choice([1, 2, 5, 10]),
                    unit_price=product.sale_price,
                    sort_order=order,
                )
                order += 1

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
