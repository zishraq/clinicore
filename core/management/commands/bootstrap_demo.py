"""Load a synthetic demo organization.

Everything here is invented: names, phone numbers, complaints, and medicines.
No real patient data or clinic identity is ever committed (SPEC §8).
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, Role, User
from clinical.models import Encounter, EncounterStatus, Prescription, PrescriptionItem
from core.context import organization_context
from organizations.models import Branch as BranchModel
from organizations.models import Organization
from patients.models import Patient, PatientClinicalProfile, Sex

DEMO_SLUG = 'demo-clinic'
DEMO_PASSWORD = 'clinicore-demo'

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
ITEMS = [
    ('Ambroxol syrup', '10 ml', 'Twice daily', '5 days', 'After meals'),
    ('Paracetamol 500mg', '1 tablet', 'Three times daily', '3 days', 'After food'),
    ('Omeprazole 20mg', '1 capsule', 'Once daily', '14 days', 'Before breakfast'),
    ('Cetirizine 10mg', '1 tablet', 'At night', '7 days', ''),
    ('Calcium + Vitamin D3', '1 tablet', 'Once daily', '30 days', 'With water'),
]


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

            patients = self._patients(organization, branch, owner)
            self._encounters(organization, branch, practitioner, patients)

        self.stdout.write(self.style.SUCCESS('\nDemo data ready.'))
        self.stdout.write(f'  Organization : {organization.name}')
        self.stdout.write(f'  Patients     : {len(patients)}')
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
            PrescriptionItem.all_objects.filter(organization=organization).delete()
            Prescription.all_objects.filter(organization=organization).delete()
            Encounter.all_objects.filter(organization=organization).delete()
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

    def _encounters(self, organization, branch, practitioner, patients):
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
            for order, item in enumerate(random.sample(ITEMS, random.randint(1, 3))):
                name, dosage, frequency, duration, instructions = item
                PrescriptionItem.objects.create(
                    organization=organization,
                    created_by=practitioner,
                    prescription=prescription,
                    free_text_name=name,
                    dosage=dosage,
                    frequency=frequency,
                    duration=duration,
                    instructions=instructions,
                    sort_order=order,
                )
