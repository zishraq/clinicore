"""Render a stored status value with the organization's label for it.

Plain labels come straight from the ``terms`` dict the context processor
supplies. Statuses need a tag because the lookup key is derived from the row's
stored value, which the template language cannot build on its own.
"""

from django import template

from organizations.models import DEFAULT_TERMINOLOGY

register = template.Library()

__all__ = ['register', 'status_label']


@register.simple_tag(takes_context=True)
def status_label(context, status) -> str:
    """Label for a stored status value, e.g. ``DRAFT`` → "Open".

    Stored values are never translated in the database (SPEC §5) — only their
    labels are configurable, so relabelling a clinic is data, not a migration.
    Falls back to the raw value so an unmapped status is visible rather than
    silently blank.
    """
    terms = context.get('terms') or DEFAULT_TERMINOLOGY
    return terms.get(f'status_{str(status).lower()}') or str(status).title()
