"""Organization settings forms.

SPEC §6.8, one screen per concern: what billing needs, and which optional
features the clinic runs. Branding, terminology, and branches get their own
screens later.
"""

from django import forms

from organizations.models import (
    DEFAULT_TERMINOLOGY,
    STRENGTH_MAX_LENGTH,
    Organization,
)

__all__ = ['BillingSettingsForm', 'FeatureSettingsForm']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 4}
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

    The strength capability needs three controls rather than one, because the
    column is named for what it measures and not for one specialty's word for
    it: the clinic decides whether to record it, what to call it, and which
    values to suggest. See docs/adr/0015-prescribed-strength.md.
    """

    # Not a model field: it is one entry in ``Organization.terminology``. There
    # is no terminology screen yet, and a field labelled "Strength" at a clinic
    # that says "Potency" is the feature not working.
    strength_label = forms.CharField(
        required=False,
        max_length=40,
        widget=forms.TextInput(attrs={**_INPUT, 'placeholder': 'Potency'}),
        label='What this clinic calls it',
        help_text='Shown wherever the field appears. Leave blank for “Strength”.',
    )
    # A textarea rather than a JSON box: the owner is typing a list, and one per
    # line is the only format that needs no explaining.
    strength_options = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={**_TEXTAREA, 'placeholder': '30C\n200C\n1M'}),
        label='Usual values',
        help_text=(
            'One per line, offered as suggestions. '
            'Anything else can still be typed in. Leave blank for none.'
        ),
    )

    class Meta:
        model = Organization
        fields = ['advice_enabled', 'strength_enabled']
        widgets = {
            'advice_enabled': forms.CheckboxInput(attrs=_CHECKBOX),
            'strength_enabled': forms.CheckboxInput(attrs=_CHECKBOX),
        }
        labels = {
            'advice_enabled': 'Prescribe advice',
            'strength_enabled': 'Record how strong each medicine is',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.instance
        # The stored override, not ``terms['strength']``: the map fills the
        # default in, and prefilling "Strength" would turn "not set" into an
        # override the moment the form is saved for any other reason.
        self.fields['strength_label'].initial = (organization.terminology or {}).get(
            'strength', ''
        )
        self.fields['strength_options'].initial = '\n'.join(organization.strengths)

    def clean_strength_options(self) -> list[str]:
        """One value per line, cleaned the same way ``Organization.strengths`` is."""
        lines = self.cleaned_data['strength_options'].splitlines()
        seen, cleaned = set(), []
        for line in lines:
            value = line.strip()[:STRENGTH_MAX_LENGTH]
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                cleaned.append(value)
        return cleaned

    def save(self, commit=True):
        organization = super().save(commit=False)
        organization.strength_options = self.cleaned_data['strength_options']
        label = self.cleaned_data['strength_label'].strip()
        terminology = dict(organization.terminology or {})
        # Blank clears the override rather than storing '', which ``terms``
        # would drop anyway — leaving a dead key behind in the JSON.
        if label and label != DEFAULT_TERMINOLOGY['strength']:
            terminology['strength'] = label
        else:
            terminology.pop('strength', None)
        organization.terminology = terminology
        if commit:
            organization.save()
        return organization
