"""Render a stored status value with the organization's label for it.

Plain labels come straight from the ``terms`` dict the context processor
supplies. Statuses need a tag because the lookup key is derived from the row's
stored value, which the template language cannot build on its own.
"""

from django import template

from organizations.models import DEFAULT_TERMINOLOGY

register = template.Library()

__all__ = ['register', 'role_label', 'status_label']


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


@register.simple_tag(takes_context=True)
def role_label(context, role) -> str:
    """Label for a stored role, e.g. ``OWNER`` → "Administrator".

    Not ``get_role_display``: that reads the label off the enum, which is a
    code-level default. Roles are a user-facing word for a domain concept, so
    they come from the organization's map like every other one (SPEC §5).
    """
    terms = context.get('terms') or DEFAULT_TERMINOLOGY
    return terms.get(f'role_{str(role).lower()}') or str(role).title()
