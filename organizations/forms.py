"""Organization settings forms.

SPEC §6.8, one screen per concern: what billing needs, and which optional
features the clinic runs. Branding, terminology, and branches get their own
screens later.
"""

from django import forms

from organizations.models import (
    DEFAULT_TERMINOLOGY,
    PRESCRIBING_FIELDS,
    Organization,
    clean_suggestions,
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


#: The sentence beside each switch. Copy rather than a derivation: a switch has
#: to say what the clinic *gets*, and "Record the pack size" is not that
#: sentence — it names the column back at them.
_SWITCH_LABELS = {
    'strength': 'Record how strong each medicine is',
    'pack_size': 'Record how much of each medicine goes home',
    'preparation': 'Record what each medicine is dispensed as',
}

#: Examples, per capability: what to type in the label box, and in the values
#: box. Deliberately ordinary words — the specialty ones are what the clinic
#: types in (SPEC §1).
_PLACEHOLDERS = {
    'strength': ('Potency', '30C\n200C\n1M'),
    'pack_size': ('Quantity', '10 tablets\n100 ml'),
    'preparation': ('Type', 'Tablet\nLiquid\nCream'),
}


class FeatureSettingsForm(forms.ModelForm):
    """Which optional capabilities this clinic runs.

    A capability the owner cannot reach is not a product feature, so this is
    what makes ``advice_enabled`` real for the second clinic that buys this —
    turning advice back on must not need a developer or a shell (A3).

    Each optional prescribing field needs three controls rather than one,
    because the column is named for what it measures and not for one specialty's
    word for it: the clinic decides whether to record it, what to call it, and
    which values to suggest. The three are built by looping over
    ``PRESCRIBING_FIELDS`` rather than written out per field — the third one is
    what made the copy-and-paste version untenable. See
    docs/adr/0015-prescribed-strength.md and docs/adr/0017-dispensing-details.md.
    """

    class Meta:
        model = Organization
        fields = [
            'advice_enabled',
            *(field.enabled_field for field in PRESCRIBING_FIELDS),
        ]
        widgets = {name: forms.CheckboxInput(attrs=_CHECKBOX) for name in fields}
        labels = {
            'advice_enabled': 'Prescribe advice',
            **{
                field.enabled_field: _SWITCH_LABELS[field.key]
                for field in PRESCRIBING_FIELDS
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.instance
        #: Which fields open a new capability, so the screen can rule between
        #: them (templates/organizations/settings_form.html).
        self.capability_switches = [field.enabled_field for field in PRESCRIBING_FIELDS]
        order = ['advice_enabled']
        for field in PRESCRIBING_FIELDS:
            term = DEFAULT_TERMINOLOGY[field.key]
            label_placeholder, options_placeholder = _PLACEHOLDERS[field.key]
            label_name = f'{field.key}_label'
            # Not model fields: one is an entry in ``Organization.terminology``,
            # and the other is a textarea over a JSON column. There is no
            # terminology screen yet, and a field labelled "Strength" at a clinic
            # that says "Potency" is the feature not working.
            self.fields[label_name] = forms.CharField(
                required=False,
                max_length=40,
                widget=forms.TextInput(
                    attrs={**_INPUT, 'placeholder': label_placeholder}
                ),
                label=f'{term} — what this clinic calls it',
                help_text=f'Shown wherever the field appears. '
                f'Leave blank for “{term}”.',
                # The stored override, not ``terms[key]``: the map fills the
                # default in, and prefilling it would turn "not set" into an
                # override the moment the form is saved for any other reason.
                initial=(organization.terminology or {}).get(field.key, ''),
            )
            # A textarea rather than a JSON box: the owner is typing a list, and
            # one per line is the only format that needs no explaining.
            self.fields[field.options_field] = forms.CharField(
                required=False,
                widget=forms.Textarea(
                    attrs={**_TEXTAREA, 'placeholder': options_placeholder}
                ),
                label=f'{term} — usual values',
                help_text=(
                    'One per line, offered as suggestions. '
                    'Anything else can still be typed in. Leave blank for none.'
                ),
                initial='\n'.join(organization.suggestions(field.key)),
            )
            order += [field.enabled_field, label_name, field.options_field]
        self.order_fields(order)

    def clean(self) -> dict:
        """One value per line, cleaned the way the model cleans it on the way out."""
        cleaned = super().clean()
        for field in PRESCRIBING_FIELDS:
            text = cleaned.get(field.options_field) or ''
            cleaned[field.options_field] = clean_suggestions(text.splitlines())
        return cleaned

    def save(self, commit=True):
        organization = super().save(commit=False)
        terminology = dict(organization.terminology or {})
        for field in PRESCRIBING_FIELDS:
            setattr(
                organization,
                field.options_field,
                self.cleaned_data[field.options_field],
            )
            label = self.cleaned_data[f'{field.key}_label'].strip()
            # Blank clears the override rather than storing '', which ``terms``
            # would drop anyway — leaving a dead key behind in the JSON.
            if label and label != DEFAULT_TERMINOLOGY[field.key]:
                terminology[field.key] = label
            else:
                terminology.pop(field.key, None)
        organization.terminology = terminology
        if commit:
            organization.save()
        return organization
