"""``PatientClinicalProfile`` is absorbed into the case record and removed.

Two fields, one screen, one form, one template, behind exactly the same access
boundary and with exactly the same one-per-patient shape — and §4 of the case
record asks for both of them. Keeping the pair gave the clinic two places to
record an allergy, and the failure mode is the dangerous one: the blank allergy
box on whichever screen the doctor happens to be looking at.

Reversible, and the reverse copies back. See
docs/adr/0020-the-case-record.md §3, including the one honest limitation —
``medical_history`` moves **whole** into §4's catch-all box rather than being
distributed across its eight prompts. Nothing distinguishes "had measles as a
child" from "appendix out in 2019" mechanically, and a guess would corrupt a
clinical record. Same rule that stopped ADR 0015 splitting ``dosage`` apart.
"""

from django.db import migrations

#: Source column on the old model -> destination column on the case record.
#: Asserted against the models by ``patients/tests/test_case_absorption.py``,
#: which is the only review this move gets.
FIELD_MAP = {
    'allergies': 'past_allergies',
    'medical_history': 'past_other_history',
}


def _copy(apps, schema_editor, *, forwards: bool):
    Profile = apps.get_model('patients', 'PatientClinicalProfile')
    CaseRecord = apps.get_model('patients', 'CaseRecord')
    CaseModality = apps.get_model('patients', 'CaseModality')
    # Imported rather than restated: the eight factors are one list, and a
    # migration holding a second copy is a second list that can drift.
    from patients.models import MODALITY_FACTORS

    if not forwards:
        for record in CaseRecord.objects.all().iterator():
            values = {
                source: getattr(record, destination)
                for source, destination in FIELD_MAP.items()
            }
            if not any(values.values()):
                continue
            Profile.objects.update_or_create(
                patient_id=record.patient_id,
                defaults={
                    'organization_id': record.organization_id,
                    **values,
                },
            )
        return

    for profile in Profile.objects.all().iterator():
        values = {
            destination: getattr(profile, source)
            for source, destination in FIELD_MAP.items()
        }
        # A profile with nothing in it is not a case record worth creating: it
        # would put an empty document on a patient's page and make the "Start
        # case record" offer disappear for no reason.
        if not any(values.values()):
            continue
        record, created = CaseRecord.objects.get_or_create(
            patient_id=profile.patient_id,
            defaults={
                'organization_id': profile.organization_id,
                'created_by_id': profile.created_by_id,
                **values,
            },
        )
        if created:
            # A case record's §9 grid is eight seeded rows; a record created
            # here has to be the same shape as one created by the application,
            # or the modality section renders empty on the one patient whose
            # record came from a migration.
            CaseModality.objects.bulk_create(
                [
                    CaseModality(
                        organization_id=profile.organization_id,
                        case_record=record,
                        factor=factor,
                        sort_order=index,
                    )
                    for index, factor in enumerate(MODALITY_FACTORS)
                ]
            )
        else:
            for destination, value in values.items():
                if value and not getattr(record, destination):
                    setattr(record, destination, value)
            record.save()


def forwards(apps, schema_editor):
    _copy(apps, schema_editor, forwards=True)


def backwards(apps, schema_editor):
    _copy(apps, schema_editor, forwards=False)


class Migration(migrations.Migration):
    dependencies = [
        ('patients', '0003_caserecord_casemodality_caseinvestigation_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.DeleteModel(name='PatientClinicalProfile'),
    ]
