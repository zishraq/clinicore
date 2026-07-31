"""Template context every page needs: the active organization and its palette."""

import re

from django.utils.safestring import mark_safe

from organizations.models import SEED_PALETTE, hex_color_or

__all__ = ['organization']

# Branding is org-editable JSON that gets injected into a <style> block, so it
# is validated rather than trusted: anything that isn't a plain colour token is
# dropped instead of escaped, because escaping inside <style> does not protect.
_TOKEN_RE = re.compile(r'^[a-z][a-z0-9-]*$')


def _palette_css(palette: dict) -> str:
    """Render the palette as CSS custom property declarations.

    Built here rather than in the template because the token names are
    hyphenated (``--cc-primary-dark``) and Django's variable lookup cannot index
    a dict by a key containing a hyphen.
    """
    declarations = ''.join(
        f'--cc-{key}:{hex_color_or(value, SEED_PALETTE[key])};'
        for key, value in palette.items()
        if _TOKEN_RE.match(str(key)) and key in SEED_PALETTE
    )
    return mark_safe(declarations)


def organization(request) -> dict:
    """Expose the request's organization, membership, and colour tokens."""
    active = getattr(request, 'organization', None)
    palette = active.palette if active else dict(SEED_PALETTE)
    return {
        'organization': active,
        'membership': getattr(request, 'membership', None),
        'palette_css': _palette_css(palette),
    }
