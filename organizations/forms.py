"""Organization settings forms.

The first slice of SPEC §6.8: only what billing needs to be configurable on day
one. Branding, terminology, and branches get their own screens later.
"""

from django import forms

from organizations.models import Organization

__all__ = ['BillingSettingsForm']

_INPUT = {'class': 'input input-bordered w-full'}


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
