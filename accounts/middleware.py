"""Hold a signed-in account on the password screen until it picks its own.

There is no email-based reset here (docs/adr/0013-user-management-without-email.md),
so a forgotten password is fixed by an administrator typing a temporary one and
reading it out. That temporary password is known to at least two people and was
probably said aloud in a waiting room, which is only acceptable if it cannot
survive the session it was issued for. This is what ends it.
"""

from django.shortcuts import redirect
from django.urls import reverse

__all__ = ['ForcePasswordChangeMiddleware']


class ForcePasswordChangeMiddleware:
    """Redirect to the password screen while ``must_change_password`` is set.

    Placed after ``ActiveOrganizationMiddleware`` so the page it redirects to
    still renders inside the organization's context and clock. WhiteNoise sits
    far above it, so static files never reach this.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt: frozenset[str] | None = None

    def exempt_paths(self) -> frozenset[str]:
        """URLs that must stay reachable, or the redirect is a trap.

        Resolved on first use rather than at import: middleware is constructed
        while the URLconf may still be loading.
        """
        if self._exempt is None:
            self._exempt = frozenset(
                {
                    # The destination itself, or the redirect loops.
                    reverse('accounts:password_change'),
                    # Signing out has to work from anywhere. Somebody who cannot
                    # remember the temporary password they were just given needs
                    # to get off this screen and ask again.
                    reverse('accounts:logout'),
                    reverse('accounts:login'),
                    reverse('core:healthz'),
                }
            )
        return self._exempt

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            user is not None
            and user.is_authenticated
            and user.must_change_password
            and request.path not in self.exempt_paths()
        ):
            # No message is added here: this runs on every request the person
            # makes until they comply, and the messages framework would stack a
            # dozen identical banners. The page says why it is showing.
            return redirect('accounts:password_change')
        return self.get_response(request)
