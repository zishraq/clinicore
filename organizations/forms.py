"""Organization settings forms.

SPEC §6.8, one screen per concern: what billing needs, which optional features
the clinic runs, what the printed prescription's letterhead says, and where its
chambers are. Terminology gets its own screen later.
"""

from django import forms

from organizations.models import (
    CONTACT_MAX_LENGTH,
    DEFAULT_TERMINOLOGY,
    PRESCRIBING_FIELDS,
    WATERMARK_MAX_LENGTH,
    Branch,
    Organization,
    clean_suggestions,
    hex_color_or,
)

__all__ = [
    'BillingSettingsForm',
    'BranchForm',
    'FeatureSettingsForm',
    'PrescriptionSettingsForm',
]

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 4}
_CHECKBOX = {'class': 'checkbox'}
_SELECT = {'class': 'select select-bordered w-full'}


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
            'billing_enabled',
            'advice_enabled',
            'temperature_unit',
            *(field.enabled_field for field in PRESCRIBING_FIELDS),
        ]
        widgets = {
            **{name: forms.CheckboxInput(attrs=_CHECKBOX) for name in fields},
            # The one control on this screen that is not a switch. It belongs
            # here rather than on a screen of its own because it is the same
            # kind of answer — how this clinic works — and a settings screen
            # with one dropdown on it is a screen that needs explaining.
            'temperature_unit': forms.Select(attrs=_SELECT),
        }
        labels = {
            # What the clinic gets, not what the column is called.
            'billing_enabled': 'Bill patients and take payments',
            'advice_enabled': 'Prescribe advice',
            'temperature_unit': 'Temperature unit',
            **{
                field.enabled_field: _SWITCH_LABELS[field.key]
                for field in PRESCRIBING_FIELDS
            },
        }
        help_texts = {
            # Said explicitly because the reassurance is the point: an operator
            # who thinks this rewrites history will never touch it, and one who
            # thinks it does nothing will not understand why an old reading
            # reads differently.
            'temperature_unit': (
                'What temperatures are entered and shown in. Readings are '
                'always stored the same way, so changing this converts what '
                'you see rather than altering anything already recorded.'
            ),
            # Said plainly because the switch looks destructive and is not: a
            # clinic that is not ready to put money in the system has to be able
            # to turn this off without wondering what it costs them.
            'billing_enabled': (
                'Turning this off hides bills, payments and receipts everywhere '
                'in the application. Nothing is deleted — whatever has already '
                'been recorded comes back exactly as it was when you turn it '
                'back on.'
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.instance
        # Not required, so this screen keeps one idiom. Every other control here
        # is a checkbox, and an unticked checkbox posts nothing at all — absence
        # is how the screen says "no". A required select would turn one absent
        # key into a refusal to save the *switches the owner did toggle*, which
        # is a worse answer than keeping the unit they already had. The control
        # going missing from the page is caught by asserting that it renders
        # (organizations/tests/test_feature_settings.py), not by validation.
        self.fields['temperature_unit'].required = False
        #: Which fields open a new capability, so the screen can rule between
        #: them (templates/organizations/settings_form.html).
        self.capability_switches = [field.enabled_field for field in PRESCRIBING_FIELDS]
        # Billing leads: it is the largest thing on this screen — a whole app
        # rather than a field on a form — and it is what the clinic came here
        # to change.
        order = ['billing_enabled', 'advice_enabled', 'temperature_unit']
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

    def clean_temperature_unit(self) -> str:
        """Absent means unchanged, never reset to the default.

        Resetting would quietly move a Celsius clinic back to Fahrenheit labels
        the first time anyone saved this screen from a form that did not carry
        the control.
        """
        return (
            self.cleaned_data.get('temperature_unit') or self.instance.temperature_unit
        )

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


class PrescriptionSettingsForm(forms.ModelForm):
    """What the printed prescription says that is not a visit or a chamber.

    Three of the four controls are not model fields. The contact strip is a
    JSON column edited one line at a time — the ``strength_options`` idiom, for
    the same reason: the owner is typing a list and one per line needs no
    explaining. The watermark and the brand colour live inside the ``branding``
    JSON, which has no screen of its own yet; the colour is here because
    without it this whole feature renders in the seed blue and cannot be made to
    match the clinic's own design.
    """

    prescription_contacts = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                **_TEXTAREA,
                'placeholder': 'Facebook: /YourClinic\nWhatsApp / Call: 01700-000000',
            }
        ),
        label='Contact strip',
        help_text=(
            'One per line, printed along the foot of the prescription exactly '
            'as typed. Leave blank for none.'
        ),
    )
    watermark_text = forms.CharField(
        required=False,
        max_length=WATERMARK_MAX_LENGTH,
        widget=forms.TextInput(attrs={**_INPUT, 'placeholder': 'ABC'}),
        label='Watermark',
        help_text=(
            f'A few letters printed faintly behind the sheet, '
            f'{WATERMARK_MAX_LENGTH} characters at most. Blank for none.'
        ),
    )
    # A native colour control. The application draws its own date pickers
    # (ADR 0016) because the native one renders its text in the device's
    # locale; a colour swatch has no such problem, it always posts #rrggbb,
    # and the alternative here is asking a non-developer to type hex.
    primary_color = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'h-10 w-20'}),
        label='Brand colour',
        help_text='Used for the rules, headings and tints on printed documents.',
    )

    class Meta:
        model = Organization
        fields = ['prescription_notice']
        labels = {'prescription_notice': 'Notice to the patient'}
        help_texts = {
            'prescription_notice': (
                'Printed across the foot of every prescription, e.g. a reminder '
                'to bring the sheet to the next visit.'
            )
        }
        widgets = {
            'prescription_notice': forms.Textarea(attrs={**_TEXTAREA, 'rows': 2})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.instance
        self.fields['prescription_contacts'].initial = '\n'.join(organization.contacts)
        self.fields['watermark_text'].initial = organization.watermark_text
        self.fields['primary_color'].initial = organization.primary_color
        self.order_fields(
            [
                'prescription_notice',
                'prescription_contacts',
                'watermark_text',
                'primary_color',
            ]
        )

    def clean_prescription_contacts(self) -> list[str]:
        """One line per contact, cleaned the way the model cleans it on the way out."""
        text = self.cleaned_data.get('prescription_contacts') or ''
        return clean_suggestions(text.splitlines(), max_length=CONTACT_MAX_LENGTH)

    def clean_primary_color(self) -> str:
        """Refuse anything that is not a plain hex colour.

        This value is interpolated into a ``<style>`` block, where escaping does
        not protect you — the same reason ``hex_color_or`` exists. Validating
        here as well means a bad value is a field error rather than a silently
        ignored setting.
        """
        value = (self.cleaned_data.get('primary_color') or '').strip()
        if not value:
            return ''
        if hex_color_or(value, '') != value:
            raise forms.ValidationError('Use a hex colour, for example #336699.')
        return value

    def save(self, commit=True):
        organization = super().save(commit=False)
        organization.prescription_contacts = self.cleaned_data['prescription_contacts']
        branding = dict(organization.branding or {})
        branding['logo_text'] = self.cleaned_data['watermark_text'].strip()
        color = self.cleaned_data['primary_color']
        if color:
            palette = dict(branding.get('palette') or {})
            palette['primary'] = color
            branding['palette'] = palette
        organization.branding = branding
        if commit:
            organization.save()
        return organization


class BranchForm(forms.ModelForm):
    """One chamber: where it is, when it opens, and whether it prints.

    ``code`` is on the form even though it is not letterhead. It is NOT NULL
    with a unique constraint per organization, so a create screen without it
    cannot save a second branch — and it is the handle ``import_patients
    --branch`` takes, so a generated one would be a value the operator has to go
    and look up.
    """

    class Meta:
        model = Branch
        fields = [
            'name',
            'code',
            'address',
            'phone',
            'consulting_hours',
            'schedule_note',
            'show_on_prescription',
            'print_order',
            'is_active',
        ]
        labels = {
            'code': 'Short code',
            'consulting_hours': 'Consulting hours',
            'schedule_note': 'When this chamber is open',
            'show_on_prescription': 'List this chamber on printed prescriptions',
            'print_order': 'Order on the prescription',
            'is_active': 'In use',
        }
        help_texts = {
            'code': 'A short handle for this chamber, used when importing data.',
            'consulting_hours': 'Printed in the header of a visit that happened here.',
            'schedule_note': (
                'Printed in the footer, e.g. every 2nd Friday of the month. '
                'Leave blank for a chamber that opens daily.'
            ),
            'print_order': 'Lower numbers print first.',
            'is_active': (
                'Turn this off instead of deleting. A chamber cannot be removed '
                'once patients or visits are recorded against it.'
            ),
        }
        widgets = {
            'name': forms.TextInput(attrs=_INPUT),
            'code': forms.TextInput(attrs={**_INPUT, 'maxlength': 20}),
            'address': forms.Textarea(attrs={**_TEXTAREA, 'rows': 3}),
            'phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'consulting_hours': forms.TextInput(attrs=_INPUT),
            'schedule_note': forms.TextInput(attrs=_INPUT),
            'show_on_prescription': forms.CheckboxInput(attrs=_CHECKBOX),
            'print_order': forms.NumberInput(
                attrs={**_INPUT, 'type': 'number', 'min': '0'}
            ),
            'is_active': forms.CheckboxInput(attrs=_CHECKBOX),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.instance.organization = organization

    def clean_code(self) -> str:
        """Upper-cased, and unique within this clinic.

        Checked by hand rather than left to the model. The constraint is on
        (organization, code) and ``organization`` is not a form field, so Django
        drops the whole constraint from validation — seeding
        ``instance.organization`` is not enough, because the exclusion is decided
        by which fields the *form* carries. Without this, a repeated code is an
        IntegrityError page instead of a message on the box that caused it.
        """
        code = self.cleaned_data['code'].strip().upper()
        organization_id = self.instance.organization_id
        if organization_id:
            clash = Branch.all_objects.filter(
                organization_id=organization_id, code=code
            )
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError('Another chamber already uses this code.')
        return code
