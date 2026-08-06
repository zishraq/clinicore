"""Organization settings forms.

SPEC §6.8, one screen per concern: what billing needs, and which optional
features the clinic runs. Branding, terminology, and branches get their own
screens later.
"""

from django import forms

from organizations.models import Organization

__all__ = ['BillingSettingsForm', 'FeatureSettingsForm']

_INPUT = {'class': 'input input-bordered w-full'}
_CHECKBOX = {'class': 'checkbox'}


class BillingSettingsForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['currency', 'default_consultation_fee']
        widgets = {
            'currency': forms.TextInput(
                attrs={**_INPUT, 'maxlength': 3, 'placeholder': 'BDT'}
            ),
            'default_consultation_fee': forms.NumberInput(
                attrs={**_INPUT, 'type': 'number', 'step': '0.01', 'min': '0'}
            ),
        }

    def clean_currency(self) -> str:
        return self.cleaned_data['currency'].strip().upper()


class FeatureSettingsForm(forms.ModelForm):
    """Which optional capabilities this clinic runs.

    A capability the owner cannot reach is not a product feature, so this is
    what makes ``advice_enabled`` real for the second clinic that buys this —
    turning advice back on must not need a developer or a shell (A3).
    """

    class Meta:
        model = Organization
        fields = ['advice_enabled']
        widgets = {'advice_enabled': forms.CheckboxInput(attrs=_CHECKBOX)}
        labels = {'advice_enabled': 'Prescribe advice'}
