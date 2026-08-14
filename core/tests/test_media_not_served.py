"""MEDIA_URL is routed by nothing, and that is a security property.

Uploaded photographs are clinical records. They are reachable only through
``clinical.views.encounter_photo``, which sits behind login, a role check and
organization scoping. A URL that serves MEDIA_ROOT directly skips all three,
and the usual `if settings.DEBUG: urlpatterns += static(...)` is exactly how
that gets added — it looks like a development convenience and it means
development never exercises the protected view.

This is the canary for someone "fixing" the missing route. See
docs/adr/0014-encounter-photos-served-through-a-view.md.
"""

import pytest
from django.urls import Resolver404, resolve

MEDIA_PATHS = [
    '/media/encounters/1/1/deadbeef.jpg',
    '/media/',
]


@pytest.mark.parametrize('path', MEDIA_PATHS)
def test_no_url_pattern_matches_a_media_path(path):
    with pytest.raises(Resolver404):
        resolve(path)


@pytest.mark.django_db
@pytest.mark.parametrize('path', MEDIA_PATHS)
def test_a_media_path_is_not_found_even_for_a_signed_in_user(client, owner, path):
    """404, not 403: there is no view there to refuse anything."""
    client.force_login(owner)
    assert client.get(path).status_code == 404
