"""Freeze a name onto items that predate ``name_snapshot``.

Rows written before the catalog existed are all free text, so their snapshot is
their typed name. Historical rows get the same treatment: an old revision that
renders with a blank name is a corrupted audit trail, not a cosmetic gap.
"""

from django.db import migrations, models


def backfill(apps, schema_editor):
    for label in ['PrescriptionItem', 'HistoricalPrescriptionItem']:
        model = apps.get_model('clinical', label)
        # Inside migrations the live model's manager is the unfiltered one
        # (OrgScopedManager.use_in_migrations is False — see ADR 0005), so this
        # correctly touches every organization's rows.
        model.objects.filter(name_snapshot='').update(
            name_snapshot=models.F('free_text_name')
        )


def noop(apps, schema_editor):
    """Nothing to undo: the column itself is dropped by the reverse of 0003."""


class Migration(migrations.Migration):
    dependencies = [
        ('clinical', '0003_historicalprescriptionitem_advice_template_and_more'),
    ]

    operations = [migrations.RunPython(backfill, noop)]
