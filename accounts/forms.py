"""Login form. No public signup and no role selector (SPEC §6.1)."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm

__all__ = ['PhoneLoginForm']


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
