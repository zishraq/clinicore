"""Loading a clinic's own medicine list (``import_remedies``).

The command's whole claim is that running it twice is safe, and it makes that
claim by relying on ``product_name_unique_per_org`` rather than by reading the
file carefully. So the interesting cases are the ones a file-trusting importer
would get wrong: the same name twice in one file, and a name that differs only
in case from something already in the catalog.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from catalog.management.commands.import_remedies import (
    DEFAULT_FILE,
    parse_remedy_file,
)
from catalog.models import Product
from core.context import organization_context

pytestmark = pytest.mark.django_db


def _import(*args, **kwargs):
    out = StringIO()
    call_command('import_remedies', *args, stdout=out, **kwargs)
    return out.getvalue()


def test_the_shipped_file_holds_the_clinics_list():
    """333 remedies, and the file travels inside the app rather than at the root."""
    names = parse_remedy_file(DEFAULT_FILE)
    assert len(names) == 333
    assert names[0] == 'Abies canadensis'
    assert names[-1] == 'Zingiber officinale'
    # The single-letter section headers are not remedies.
    assert not [name for name in names if len(name) == 1]
    # Nor is the full stop that ends each section.
    assert not [name for name in names if name.endswith('.')]


def test_it_loads_the_whole_list(organization):
    output = _import(organization.slug)
    with organization_context(organization):
        assert Product.objects.count() == 333
        assert Product.objects.filter(name='Arsenicum album').exists()
    assert 'Created  : 333' in output


def test_the_defaults_are_sellable_and_untracked(organization):
    _import(organization.slug)
    with organization_context(organization):
        remedy = Product.objects.get(name='Belladonna')
        assert remedy.is_sellable is True
        assert remedy.is_stock_tracked is False
        assert remedy.is_active is True


def test_running_it_twice_creates_nothing_new(organization):
    _import(organization.slug)
    output = _import(organization.slug)
    with organization_context(organization):
        assert Product.objects.count() == 333
    assert 'Created  : 0' in output
    assert 'Skipped  : 333' in output


def test_a_name_already_in_the_catalog_is_skipped_whatever_its_case(organization):
    """The constraint is case-insensitive, and the count has to agree with it."""
    with organization_context(organization):
        Product.objects.create(organization=organization, name='belladonna')

    output = _import(organization.slug)
    with organization_context(organization):
        assert Product.objects.filter(name__iexact='belladonna').count() == 1
    assert 'Created  : 332' in output
    assert 'Skipped  : 1' in output


def test_a_repeated_name_inside_the_file_is_skipped_too(organization, tmp_path):
    """A file-trusting importer would insert this twice and hit the constraint."""
    path = tmp_path / 'list.txt'
    path.write_text('A\nAconitum napellus, Arnica montana, aconitum napellus.\n')

    output = _import(organization.slug, file=path)
    with organization_context(organization):
        assert Product.objects.count() == 2
    assert 'Created  : 2' in output
    assert 'Skipped  : 1' in output


def test_it_imports_into_one_organization_only(organization, other_organization):
    _import(organization.slug)
    with organization_context(other_organization):
        assert Product.objects.count() == 0


def test_an_unknown_organization_is_refused(db):
    with pytest.raises(CommandError, match='No organization with slug'):
        _import('no-such-clinic')


def test_a_missing_file_is_refused(organization, tmp_path):
    with pytest.raises(CommandError, match='No such file'):
        _import(organization.slug, file=tmp_path / 'absent.txt')


def test_an_empty_file_is_refused(organization, tmp_path):
    """Reporting "created 0" for a file of section headers would read as success."""
    path = tmp_path / 'empty.txt'
    path.write_text('A\n\nB\n\n')
    with pytest.raises(CommandError, match='holds no medicine names'):
        _import(organization.slug, file=path)
