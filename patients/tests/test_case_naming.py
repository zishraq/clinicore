"""The clinic's vocabulary is data. This is what makes that real.

ADR 0020 §1 picked generic column names plus the terminology map over accepting
specialty words in the schema, and named the enforcement: a hardcoded
"Repertorization", "Rubric", "Miasm" or "Remedy" in a template or a form is a
bug, exactly like a hardcoded "Potency" (ADR 0015) or ``get_role_display``
(ADR 0013). Without a test that is an aspiration; with one it is mechanical.

Shaped after ``core/tests/test_date_inputs.py``, which stops a native date input
creeping back in the same way.
"""

import ast
import re
from pathlib import Path

import pytest
from django.urls import reverse

from core.context import organization_context
from organizations.models import DEFAULT_TERMINOLOGY
from patients.models import (
    CaseAnalysisEntry,
    CaseComplaint,
    CaseInvestigation,
    CaseModality,
    CaseRecord,
    Patient,
)

ROOT = Path(__file__).resolve().parents[2]

#: Words that belong to one tradition and must never be written into a screen.
#: The clinic's own words for these concepts live in ``Organization.terminology``
#: and reach the page through ``terms``.
SPECIALTY_WORDS = ('repertoriz', 'repertoris', 'rubric', 'miasm', 'potency')

#: The new keys this feature adds, with the default each must fall back to.
#: Every one has to be in ``DEFAULT_TERMINOLOGY``: ``Organization.terms`` drops
#: overrides for unknown keys, so a missing default means the clinic's word is
#: accepted by the settings screen and then silently ignored. That is the
#: ``role_developer`` lesson (ADR 0019), and it applies thirteen times here.
NEW_KEYS = {
    'case_record': 'Case record',
    'case_record_plural': 'Case records',
    'complaint': 'Complaint',
    'complaint_plural': 'Complaints',
    'modality': 'Modality',
    'modality_plural': 'Modalities',
    'investigation': 'Investigation',
    'investigation_plural': 'Investigations',
    'case_analysis': 'Case analysis',
    'finding': 'Finding',
    'grade': 'Grade',
    'candidate': 'Candidate',
    'constitutional_assessment': 'Constitutional assessment',
}


#: The one deliberate exception, stated rather than quietly excluded. The
#: Features screen shows an *example* of what a clinic might rename "Strength"
#: to, in the box where the clinic types its own word. That is the opposite of
#: hardcoding — it teaches the owner the field is theirs to name — but it is
#: still a specialty word in a form module, so it is named here on purpose.
#: (Its own comment in organizations/forms.py calls the placeholders
#: "deliberately ordinary words", which this one is not; worth a look.)
ALLOWED = {('organizations/forms.py', 'potency')}


def _renderable_text(path: Path) -> str:
    """What this file would actually put on a screen, with commentary removed.

    A grep over raw lines cannot tell a rule from a note *about* the rule — this
    module's own docstring names all five words — so comments, docstrings and
    Django comment blocks come out first. What is left is the string literals a
    template or a form would render.
    """
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.html':
        text = re.sub(
            r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', text, flags=re.S
        )
        return re.sub(r'\{#.*?#\}', '', text, flags=re.S)
    module = ast.parse(text)
    docstrings = {
        node.body[0].value
        for node in ast.walk(module)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return '\n'.join(
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    )


def _searchable_files():
    """Templates and form modules — where a user-facing word would be written."""
    yield from (ROOT / 'templates').rglob('*.html')
    for app in sorted(ROOT.iterdir()):
        if app.is_dir() and not app.name.startswith(('.', '_')):
            yield from sorted(app.glob('*forms.py'))


@pytest.mark.parametrize('word', SPECIALTY_WORDS)
def test_no_template_or_form_hardcodes_a_specialty_word(word):
    offenders = []
    for path in _searchable_files():
        relative = str(path.relative_to(ROOT))
        if (relative, word) in ALLOWED:
            continue
        if word in _renderable_text(path).lower():
            offenders.append(relative)

    assert not offenders, (
        f'"{word}" is one clinic\'s word for a thing, not the product\'s:\n'
        + '\n'.join(sorted(offenders))
        + '\n\nRoute it through Organization.terminology instead.'
    )


@pytest.mark.parametrize(('key', 'default'), sorted(NEW_KEYS.items()))
def test_every_new_key_has_a_default(key, default):
    assert DEFAULT_TERMINOLOGY.get(key) == default


@pytest.mark.django_db
@pytest.mark.parametrize('key', sorted(NEW_KEYS))
def test_an_override_for_each_key_survives(organization, key):
    """The half a missing default breaks, asserted per key rather than in bulk."""
    organization.terminology = {**organization.terminology, key: 'Clinic Word'}
    organization.save(update_fields=['terminology', 'updated_at'])

    assert organization.terms[key] == 'Clinic Word'


@pytest.mark.django_db
def test_the_clinics_words_reach_the_page(client, organization, branch, practitioner):
    """End to end: an override in the column shows up on the case record.

    This is the assertion that makes generic column naming a real feature rather
    than a naming convention — the doctor's own vocabulary has to reach his
    screen without a developer.
    """
    organization.case_record_enabled = True
    organization.terminology = {
        **organization.terminology,
        'case_record': 'Case study',
        'case_analysis': 'Repertorization',
        'finding': 'Rubric',
        'candidate': 'Remedy',
        'constitutional_assessment': 'Miasmatic assessment',
    }
    organization.save(
        update_fields=['case_record_enabled', 'terminology', 'updated_at']
    )
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
    client.force_login(practitioner)

    from django.urls import reverse

    body = client.get(
        reverse('patients:case_record', args=[patient.pk])
    ).content.decode()

    for word in (
        'Case study',
        'Repertorization',
        'Rubric',
        'Remedy',
        'Miasmatic assessment',
    ):
        assert word in body, f'{word} did not reach the page'


def test_no_case_record_field_name_is_a_specialty_word():
    """The half the grep above cannot see: a column, not a label."""
    offenders = []
    for model in (
        CaseRecord,
        CaseComplaint,
        CaseModality,
        CaseInvestigation,
        CaseAnalysisEntry,
    ):
        for field in model._meta.get_fields():
            if any(word in field.name.lower() for word in SPECIALTY_WORDS):
                offenders.append(f'{model.__name__}.{field.name}')

    assert not offenders, f'specialty words in the schema: {offenders}'


def test_the_url_carries_no_specialty_word():
    """A URL is as permanent as a column name, and harder to change."""
    for url in (
        reverse('patients:case_record', args=[1]),
        reverse('patients:case_record_row', args=['analysis']),
    ):
        assert not re.search('|'.join(SPECIALTY_WORDS), url.lower()), url
