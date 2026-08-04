"""Catalog search and quick-add.

Every function takes ``organization`` explicitly (docs/adr/0005). The search
here backs a single autocomplete over both catalogs, so the practitioner types
once and gets medicines and advice together.
"""

from django.db.models import Q

from catalog.models import AdviceTemplate, Product

__all__ = [
    'quick_add_advice',
    'quick_add_product',
    'search_advice',
    'search_catalogs',
    'search_products',
]

#: Enough to choose from without turning the dropdown into a scroll exercise.
SUGGESTION_LIMIT = 8


def search_products(organization, query: str, *, limit: int = SUGGESTION_LIMIT):
    queryset = Product.objects.for_organization(organization).filter(is_active=True)
    query = (query or '').strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(category__icontains=query)
        )
    return queryset[:limit]


def search_advice(organization, query: str, *, limit: int = SUGGESTION_LIMIT):
    queryset = AdviceTemplate.objects.for_organization(organization).filter(
        is_active=True
    )
    query = (query or '').strip()
    if query:
        queryset = queryset.filter(Q(text__icontains=query))
    return queryset[:limit]


def search_catalogs(organization, query: str, *, include_advice: bool = True) -> dict:
    """Both catalogs plus whether the text already exists exactly.

    ``exact_match`` drives the quick-add offer: there is no point offering to
    create something the practitioner can already pick.

    ``include_advice=False`` is the billing case: advice is not a thing anyone
    is charged for, so a bill line searches substances only.
    """
    query = (query or '').strip()
    products = list(search_products(organization, query))
    advice = list(search_advice(organization, query)) if include_advice else []
    exact = bool(query) and (
        any(item.name.lower() == query.lower() for item in products)
        or any(item.text.strip().lower() == query.lower() for item in advice)
    )
    return {
        'query': query,
        'products': products,
        'advice': advice,
        'exact_match': exact,
        'has_results': bool(products or advice),
    }


def quick_add_product(organization, *, actor, name: str) -> Product:
    """Create a medicine from whatever was typed, without leaving the form.

    Reuses an existing entry on an exact name match so a second practitioner
    typing the same thing does not fork the catalog.
    """
    name = (name or '').strip()
    existing = (
        Product.objects.for_organization(organization).filter(name__iexact=name).first()
    )
    if existing is not None:
        return existing
    return Product.objects.create(
        organization=organization, created_by=actor, name=name
    )


def quick_add_advice(organization, *, actor, text: str) -> AdviceTemplate:
    """Same idea for advice."""
    text = (text or '').strip()
    existing = (
        AdviceTemplate.objects.for_organization(organization)
        .filter(text__iexact=text)
        .first()
    )
    if existing is not None:
        return existing
    return AdviceTemplate.objects.create(
        organization=organization, created_by=actor, text=text
    )
