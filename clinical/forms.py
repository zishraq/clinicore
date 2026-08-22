"""Encounter, prescription, and prescription-item forms.

Optimized for speed of entry (SPEC §6.4): the whole consultation is one page —
notes, prescription instructions, and item rows — submitted once.
"""

from django import forms
from django.db.models import Q

from accounts.models import User
from accounts.services import prescribing_users
from catalog.models import AdviceTemplate, Product
from clinical.images import ImageRejected, normalize_image
from clinical.models import Encounter, ItemType, Prescription, PrescriptionItem
from core.forms import closed_choices, date_widget, org_scoped_formfield
from organizations.models import DEFAULT_TERMINOLOGY, PRESCRIBING_FIELDS
from organizations.services import active_branches
from patients.models import Patient

__all__ = [
    'EncounterForm',
    'PhotoUploadForm',
    'PrescriptionForm',
    'PrescriptionItemFormSet',
]

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}
_FILE = {'class': 'file-input file-input-bordered w-full'}

#: Per submission. Django's DATA_UPLOAD_MAX_NUMBER_FILES (100) is the global
#: backstop; this is the number a person could plausibly mean to send at once,
#: and refusing 60 with a sentence beats accepting them and timing out.
MAX_FILES_PER_UPLOAD = 10


class MultipleFileInput(forms.ClearableFileInput):
    """``<input multiple>``. Django's stock widget refuses to render one."""

    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Several photographs at once, cleaned to a list of stored-ready JPEGs.

    Validation lives here rather than in the view because a rejected file must
    come back as a field error on a redisplayed form — with the consultation
    note still typed into it. A doctor losing the note because a photograph was
    12 MB is a worse bug than the one being prevented.
    """

    widget = MultipleFileInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('required', False)
        kwargs.setdefault(
            'widget',
            # No `capture` attribute, deliberately. It forces the camera and
            # removes the gallery, which breaks a normal flow: a photograph
            # taken of a referral letter while the doctor is with someone else,
            # attached afterwards. accept="image/*" still offers the camera in
            # the Android picker.
            MultipleFileInput(attrs={**_FILE, 'accept': 'image/*'}),
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        # An empty file input posts a single empty value; drop those before
        # counting, or "no photos" reads as one unreadable file.
        files = [item for item in files if item]
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise forms.ValidationError(
                f'{len(files)} photos at once is too many — '
                f'{MAX_FILES_PER_UPLOAD} is the limit per upload.'
            )
        normalized = []
        for item in files:
            super().clean(item, initial)
            try:
                normalized.append(normalize_image(item))
            except ImageRejected as rejected:
                raise forms.ValidationError(str(rejected)) from None
        return normalized


class PhotoUploadForm(forms.Form):
    """The standalone upload on the visit detail page.

    The same two fields also live on ``EncounterForm``, because a visit being
    written up has no saved encounter to attach a photograph to yet — there,
    they ride along with the one big POST and are attached after it saves.
    """

    photos = MultipleImageField()
    caption = forms.CharField(
        required=False,
        max_length=140,
        widget=forms.TextInput(
            attrs={**_INPUT, 'placeholder': 'Blood report, X-ray, rash on left arm…'}
        ),
    )


def _practitioner_choices(organization, instance):
    """Who may be picked, plus whoever this visit already names.

    The second half is not cosmetic. A ``ModelChoiceField`` whose stored value
    is outside its queryset renders with nothing selected and then refuses the
    save with "Select a valid choice", so narrowing the list by role would make
    every visit recorded by somebody who has since stopped treating patients
    unamendable — the record locked by a change to a *different* row. Same
    guard, same reason, as the closed prescribing lists (ADR 0017).
    """
    eligible = prescribing_users(organization)
    current = getattr(instance, 'practitioner_id', None)
    if current is None:
        return eligible
    return User.objects.filter(
        Q(pk__in=eligible.values('pk')) | Q(pk=current)
    ).distinct()


class EncounterForm(forms.ModelForm):
    # Not a model field: it becomes the history row's change_reason. Its label
    # and help text are rewritten in __init__ from the org's terminology map.
    change_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                **_TEXTAREA,
                'rows': 2,
                'placeholder': 'What is being corrected, and why?',
            }
        ),
    )
    # Also not model fields. A visit being created has no row to hang a
    # photograph on yet, so the files travel with the same POST and the view
    # attaches them once the encounter exists. Cleaning them here is what keeps
    # a rejected file from costing the typed note.
    photos = MultipleImageField()
    photo_caption = forms.CharField(
        required=False,
        max_length=140,
        widget=forms.TextInput(
            attrs={**_INPUT, 'placeholder': 'Blood report, X-ray, rash on left arm…'}
        ),
    )

    class Meta:
        # patient and branch are org-scoped relations; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = Encounter
        fields = [
            'patient',
            'branch',
            'practitioner',
            'occurred_at',
            'chief_complaint',
            'examination',
            'assessment',
            'plan',
            'follow_up_date',
        ]
        widgets = {
            'occurred_at': forms.DateTimeInput(
                attrs={**_INPUT, 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'
            ),
            'follow_up_date': date_widget(),
            'chief_complaint': forms.Textarea(attrs=_TEXTAREA),
            'examination': forms.Textarea(attrs=_TEXTAREA),
            'assessment': forms.Textarea(attrs=_TEXTAREA),
            'plan': forms.Textarea(attrs=_TEXTAREA),
            # Not a dropdown: a clinic's whole patient list is unusable as a
            # <select>, and the doctor starts here rather than at Patients. The
            # visible control is a search box in
            # templates/clinical/_patient_picker.html; this stays the real
            # field, so validation and org scoping are untouched.
            'patient': forms.HiddenInput(attrs={'data-role': 'patient-id'}),
            'branch': forms.Select(attrs=_SELECT),
            'practitioner': forms.Select(attrs=_SELECT),
        }

    def __init__(self, *args, organization=None, requires_reason=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.requires_reason = requires_reason
        reason = self.fields['change_reason']
        reason.required = requires_reason
        # Every user-facing word for the record comes from the org's map (SPEC §5).
        terms = organization.terms if organization else DEFAULT_TERMINOLOGY
        amend, one = terms['amend'].lower(), terms['encounter'].lower()
        reason.label = f'Reason for this {amend}'
        # "Follow up date" was a date the clinic wrote down and never saw again.
        # It books an appointment now, so it is named after what it produces.
        self.fields['follow_up_date'].label = f'Next {terms["appointment"].lower()}'
        # The clinic's own word for these, so an org that mostly photographs
        # referral letters can call them Documents (SPEC §5).
        self.fields['photos'].label = terms['photo_plural']
        reason.help_text = (
            f'Saved in the {one} history. '
            f'Needed once the {one} is no longer a {terms["status_draft"].lower()}.'
        )
        if organization is None:
            return
        self.fields['patient'].queryset = Patient.objects.for_organization(organization)
        self.fields['branch'].queryset = active_branches(organization)
        # Practitioners are users, not org-owned rows, so narrow by membership.
        self.fields['practitioner'].queryset = _practitioner_choices(
            organization, self.instance
        )
        self.fields['occurred_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']


class PrescriptionForm(forms.ModelForm):
    class Meta:
        model = Prescription
        fields = ['general_instructions', 'print_size']
        widgets = {
            'general_instructions': forms.Textarea(attrs=_TEXTAREA),
            'print_size': forms.Select(attrs=_SELECT),
        }


class PrescriptionItemForm(forms.ModelForm):
    """One row of the prescription, medicine or advice.

    The visible text box is ``display_name``, which is *not* a model field. The
    real source — a catalog FK or ``free_text_name`` — is decided in ``clean()``,
    so the exactly-one-source constraint cannot be violated by a form post, and
    the row still works with JavaScript disabled (typed text becomes free text).
    """

    display_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                **_INPUT,
                'placeholder': 'Search medicines and advice…',
                'autocomplete': 'off',
                'data-role': 'item-search',
            }
        ),
    )

    class Meta:
        # product and advice_template are org-scoped relations; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = PrescriptionItem
        fields = [
            'item_type',
            'product',
            'advice_template',
            'free_text_name',
            'strength',
            'pack_size',
            'preparation',
            'dosage',
            'frequency',
            'duration',
            'instructions',
            'sort_order',
        ]
        widgets = {
            'item_type': forms.HiddenInput(attrs={'data-role': 'item-type'}),
            'product': forms.HiddenInput(attrs={'data-role': 'item-product'}),
            'advice_template': forms.HiddenInput(attrs={'data-role': 'item-advice'}),
            'free_text_name': forms.HiddenInput(attrs={'data-role': 'item-free-text'}),
            'dosage': forms.TextInput(attrs={**_INPUT, 'placeholder': '1 tablet'}),
            'frequency': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Twice daily'}
            ),
            'duration': forms.TextInput(attrs={**_INPUT, 'placeholder': '7 days'}),
            'instructions': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'After meals'}
            ),
            'sort_order': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # clean() derives item_type from whichever source won, so the posted
        # value is a hint, not a requirement — a client without JavaScript never
        # sends one.
        self.fields['item_type'].required = False
        # Catalog relations are org-scoped; the queryset is narrowed by the
        # formset factory below via the parent form's organization.
        self.fields['product'].queryset = Product.all_objects.none()
        self.fields['advice_template'].queryset = AdviceTemplate.all_objects.none()
        if self.instance.pk:
            self.fields['display_name'].initial = self.instance.name_snapshot

    #: Set by ``bind_organization``: the optional fields this clinic records,
    #: in row order. Empty until then, so an unbound row offers none.
    optional_field_names = ()

    def bind_organization(self, organization) -> None:
        """Restrict the catalog relations to one tenant, and gate the extras."""
        self.fields['product'].queryset = Product.objects.for_organization(organization)
        self.fields[
            'advice_template'
        ].queryset = AdviceTemplate.objects.for_organization(organization)
        # Dropped, not hidden. A field left on the form and merely omitted from
        # the template is still settable by a hand-built POST — and, worse, is
        # rebuilt as empty by ``construct_instance`` on every subsequent save,
        # which would quietly erase what was recorded before the clinic turned
        # the capability off. See docs/adr/0015-prescribed-strength.md.
        names = []
        for field in PRESCRIBING_FIELDS:
            if not getattr(organization, field.enabled_field):
                self.fields.pop(field.key, None)
                continue
            self.fields[field.key].label = organization.terms[field.key]
            # Every optional prescribing field is a closed list: the clinic's
            # configured values are the only values, and an unlisted one is
            # added in Settings rather than typed into a visit. The widget is
            # built here because this is where the organization is in scope,
            # and per form instance because the choices include whatever *this*
            # row already holds — see core.forms.closed_choices.
            self.fields[field.key].widget = forms.Select(
                attrs=_SELECT,
                choices=closed_choices(
                    organization.suggestions(field.key), self[field.key].value()
                ),
            )
            names.append(field.key)
        self.optional_field_names = names

    @property
    def optional_fields(self) -> list:
        """The extras that survived binding, for the row template to render.

        The template asks the form which fields exist rather than asking the
        organization which are on: those are the same answer, and only one of
        them stays true after ``bind_organization`` has popped a field.
        """
        return [self[name] for name in self.optional_field_names]

    #: The half of the row that lives behind the disclosure.
    DETAIL_FIELDS = ('dosage', 'frequency', 'duration', 'instructions')

    @property
    def has_details(self) -> bool:
        """Whether the collapsed half already holds something.

        Decided here rather than in JavaScript, so editing an older visit shows
        what is on it even with scripting off, and so an HTMX-added row (which
        renders through this same template) starts closed. ``BoundField.value()``
        reads posted data when bound and the instance when not, which covers
        redisplay after a validation error too.
        """
        return any(self[name].value() for name in self.DETAIL_FIELDS)

    #: Fields whose presence means the practitioner actually entered something.
    CONTENT_FIELDS = (
        'display_name',
        'product',
        'advice_template',
        'strength',
        'pack_size',
        'preparation',
        'dosage',
        'frequency',
        'duration',
        'instructions',
    )

    def has_changed(self) -> bool:
        """True only when the row actually holds something.

        Removing an unsaved row deletes its inputs, so that index posts nothing
        at all. Django's default reads the absent ``item_type`` as differing
        from its initial and calls the gap a filled-in row, which then fails
        validation for having no source. Judge the row by its content instead.
        """
        if self.instance.pk:
            return super().has_changed()
        return any(self.data.get(self.add_prefix(name)) for name in self.CONTENT_FIELDS)

    def clean(self):
        cleaned = super().clean()
        display = (cleaned.get('display_name') or '').strip()
        product = cleaned.get('product')
        advice = cleaned.get('advice_template')

        if product and advice:
            raise forms.ValidationError('Choose either a medicine or advice, not both.')

        # The catalog entry wins when one is selected; otherwise whatever was
        # typed becomes free text. Either way exactly one source survives.
        if advice:
            cleaned['item_type'] = ItemType.ADVICE
            cleaned['free_text_name'] = ''
        elif product:
            cleaned['item_type'] = ItemType.MEDICATION
            cleaned['free_text_name'] = ''
        else:
            cleaned['free_text_name'] = display[:200]
            if not cleaned.get('item_type'):
                cleaned['item_type'] = ItemType.MEDICATION

        if cleaned['item_type'] == ItemType.ADVICE:
            cleaned['dosage'] = None
            # Guarded on the field still being present: writing the key when
            # the capability is off would let ``construct_instance`` blank a
            # value recorded while it was on.
            for field in PRESCRIBING_FIELDS:
                if field.key in self.fields:
                    cleaned[field.key] = ''

        has_source = bool(product or advice or cleaned['free_text_name'])
        if not has_source and self.has_changed():
            raise forms.ValidationError(
                'Enter a medicine or advice, or pick one from the list.'
            )
        return cleaned


class BasePrescriptionItemFormSet(forms.BaseInlineFormSet):
    """Passes the organization down so each row's catalog querysets are scoped."""

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        if organization is not None:
            for form in self.forms:
                form.bind_organization(organization)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if self.organization is not None:
            form.bind_organization(self.organization)
        deletion = form.fields.get(forms.formsets.DELETION_FIELD_NAME)
        if deletion is not None:
            # Stays a checkbox so ticking it still posts "on", but it is driven
            # by the row's Remove button rather than shown as one.
            deletion.widget = forms.CheckboxInput(
                attrs={'class': 'hidden', 'data-role': 'item-delete'}
            )


PrescriptionItemFormSet = forms.inlineformset_factory(
    Prescription,
    PrescriptionItem,
    form=PrescriptionItemForm,
    formset=BasePrescriptionItemFormSet,
    # One empty row: the autocomplete adds more on demand, and three blank rows
    # is just noise to skip past.
    extra=1,
    can_delete=True,
)
