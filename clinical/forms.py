"""Encounter, prescription, and prescription-item forms.

Optimized for speed of entry (SPEC §6.4): the whole consultation is one page —
notes, prescription instructions, and item rows — submitted once.
"""

from django import forms

from accounts.models import Role, User
from clinical.models import Encounter, Prescription, PrescriptionItem
from core.forms import org_scoped_formfield
from organizations.services import active_branches
from patients.models import Patient

__all__ = ['EncounterForm', 'PrescriptionForm', 'PrescriptionItemFormSet']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}


def _practitioner_users(organization):
    """Users who may be recorded as the practitioner on an encounter."""
    return User.objects.filter(
        memberships__organization=organization,
        memberships__is_active=True,
        memberships__role__in=[Role.OWNER, Role.PRACTITIONER],
    ).distinct()


class EncounterForm(forms.ModelForm):
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
            'follow_up_date': forms.DateInput(attrs={**_INPUT, 'type': 'date'}),
            'chief_complaint': forms.Textarea(attrs=_TEXTAREA),
            'examination': forms.Textarea(attrs=_TEXTAREA),
            'assessment': forms.Textarea(attrs=_TEXTAREA),
            'plan': forms.Textarea(attrs=_TEXTAREA),
            'patient': forms.Select(attrs=_SELECT),
            'branch': forms.Select(attrs=_SELECT),
            'practitioner': forms.Select(attrs=_SELECT),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization is None:
            return
        self.fields['patient'].queryset = Patient.objects.for_organization(organization)
        self.fields['branch'].queryset = active_branches(organization)
        # Practitioners are users, not org-owned rows, so narrow by membership.
        self.fields['practitioner'].queryset = _practitioner_users(organization)
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
    class Meta:
        model = PrescriptionItem
        fields = [
            'free_text_name',
            'dosage',
            'frequency',
            'duration',
            'instructions',
            'sort_order',
        ]
        widgets = {
            'free_text_name': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Medicine or preparation'}
            ),
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


PrescriptionItemFormSet = forms.inlineformset_factory(
    Prescription,
    PrescriptionItem,
    form=PrescriptionItemForm,
    extra=3,
    can_delete=True,
)
