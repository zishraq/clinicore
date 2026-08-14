"""Login, team administration, and the account holder's own details.

No public signup and no role selector on the login form (SPEC §6.1); roles are
assigned by an administrator on the team screen.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from accounts.models import Role, User
from organizations.models import DEFAULT_TERMINOLOGY

__all__ = [
    'MemberCreateForm',
    'MemberUpdateForm',
    'PhoneLoginForm',
    'ProfileForm',
    'TemporaryPasswordForm',
]

_INPUT = {'class': 'input input-bordered w-full'}
_SELECT = {'class': 'select select-bordered w-full'}


class PhoneLoginForm(AuthenticationForm):
    """AuthenticationForm with the username field presented as a phone number."""

    username = forms.CharField(
        label='Phone number',
        widget=forms.TextInput(
            attrs={
                'autofocus': True,
                'autocomplete': 'tel',
                'inputmode': 'tel',
                'placeholder': '01700000000',
                'class': 'input input-bordered w-full',
            }
        ),
    )
    password = forms.CharField(
        label='Password',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'current-password',
                'class': 'input input-bordered w-full',
                # Alpine toggles this between password and text; see login.html.
                'x-bind:type': "show ? 'text' : 'password'",
            }
        ),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': 'That phone number and password do not match an account.',
    }


def _role_choices(terms: dict) -> list[tuple[str, str]]:
    """Role dropdown, labelled from the organization's terminology map.

    Three fixed roles and a dropdown, deliberately: SPEC §6.1 asks for a
    data-driven permission matrix, and a screen for editing one is configuration
    a clinic of five people will get wrong rather than a feature they want.
    """
    return [
        (role.value, terms.get(f'role_{role.value.lower()}', role.label))
        for role in Role
    ]


def _temporary_password_field() -> forms.CharField:
    """A password an administrator types and then reads out loud.

    A factory rather than a shared base class: the two forms that need it are a
    ``ModelForm`` and a plain ``Form``, and mixing a ``Form`` into a
    ``ModelForm`` to save ten lines produces an MRO nobody wants to reason about
    the next time one of them grows a field.
    """
    return forms.CharField(
        label='Temporary password',
        strip=False,
        # The account holder is about to replace it, but a temporary password is
        # a live credential on a clinical system for as long as it stands, so it
        # goes through AUTH_PASSWORD_VALIDATORS like any other.
        validators=[validate_password],
        widget=forms.PasswordInput(attrs={**_INPUT, 'autocomplete': 'new-password'}),
        help_text=(
            'Write this down and read it out to them. They will have to choose '
            'their own password when they next sign in.'
        ),
    )


class _MemberFormBase(forms.ModelForm):
    """The fields an administrator maintains for somebody else.

    Both subclasses edit ``User`` and carry ``role`` alongside, because a
    membership's role and its user's details are one thing on screen even though
    they are two rows underneath.
    """

    # STAFF, not the first choice in the enum. A dropdown that opens on
    # "Administrator" hands out the most powerful role to anybody who does not
    # read it, and the common case by a distance is adding a receptionist.
    role = forms.ChoiceField(
        choices=[], initial=Role.STAFF, widget=forms.Select(attrs=_SELECT)
    )

    class Meta:
        model = User
        fields = ['full_name', 'phone', 'email']
        labels = {
            'full_name': 'Name',
            'phone': 'Phone number',
            'email': 'Email (optional)',
        }
        help_texts = {
            'phone': 'This is what they sign in with.',
            # Stated plainly, because the absence is deliberate and invisible:
            # nothing here sends mail. See docs/adr/0013.
            'email': 'For your records only — the system never sends email.',
        }
        widgets = {
            'full_name': forms.TextInput(attrs={**_INPUT, 'autofocus': True}),
            'phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'email': forms.EmailInput(attrs=_INPUT),
        }

    def __init__(self, *args, terms: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = _role_choices(terms or DEFAULT_TERMINOLOGY)

    def clean_phone(self):
        """A collision is a form error, never an IntegrityError.

        ``phone`` is ``USERNAME_FIELD`` and unique across the whole deployment,
        so the number may belong to somebody at another clinic. The message says
        that the number is taken and nothing else — naming the account would
        leak a name across tenants to anyone willing to guess numbers.
        """
        phone = (self.cleaned_data.get('phone') or '').strip()
        taken = User.objects.filter(phone=phone)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError(
                'That phone number is already in use. Check the number, or ask '
                'for help if you think this person already has an account.'
            )
        return phone


class MemberCreateForm(_MemberFormBase):
    """Add somebody to the team: details, role, and their first password."""

    password = _temporary_password_field()

    field_order = ['full_name', 'phone', 'role', 'email', 'password']


class MemberUpdateForm(_MemberFormBase):
    """Edit somebody's details and role. The password is a separate screen.

    ``editing_self`` disables the role field, which is the whole guard against
    the unrecoverable state: an administrator who demotes themselves in an
    organization where they are the only one leaves it with no way back in short
    of shell access. Disabling is not decoration — Django ignores submitted data
    for a disabled field and takes the initial value, so a hand-built POST
    cannot change it either.

    Nothing weaker is needed. The only account that could remove the *last*
    administrator is that administrator, so refusing self-demotion means an
    organization always has at least one.
    """

    field_order = ['full_name', 'phone', 'role', 'email']

    def __init__(self, *args, editing_self: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing_self = editing_self
        if editing_self:
            role = self.fields['role']
            role.disabled = True
            role.help_text = 'Only another administrator can change your role.'


class TemporaryPasswordForm(forms.Form):
    """An administrator resetting somebody else's forgotten password."""

    password = _temporary_password_field()


class ProfileForm(forms.ModelForm):
    """Your own name and email. Not your phone — that is what you sign in with.

    Changing the sign-in identifier is an administrator's job on the team
    screen, where a collision with another account can be reported honestly.
    """

    class Meta:
        model = User
        fields = ['full_name', 'email']
        labels = {'full_name': 'Name', 'email': 'Email (optional)'}
        help_texts = {'email': 'For your records only — the system never sends email.'}
        widgets = {
            'full_name': forms.TextInput(attrs=_INPUT),
            'email': forms.EmailInput(attrs=_INPUT),
        }
