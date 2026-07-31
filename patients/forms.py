"""Patient forms. Demographics and clinical narrative are separate forms.

STAFF is served ``PatientForm`` only — the split is what makes the permission
boundary structural rather than a hidden template block.
"""

from django import forms

from core.forms import org_scoped_formfield
from organizations.services import active_branches
from patients.models import Patient, PatientClinicalProfile

__all__ = ['ClinicalProfileForm', 'PatientForm']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}


class PatientForm(forms.ModelForm):
    class Meta:
        # registered_branch points at an org-scoped model; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = Patient
        fields = [
            'full_name',
            'phone',
            'sex',
            'date_of_birth',
            'approx_age_years',
            'address',
            'registered_branch',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs=_INPUT),
            'phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'sex': forms.Select(attrs=_SELECT),
            'date_of_birth': forms.DateInput(attrs={**_INPUT, 'type': 'date'}),
            'approx_age_years': forms.NumberInput(attrs={**_INPUT, 'min': 0}),
            'address': forms.Textarea(attrs=_TEXTAREA),
            'registered_branch': forms.Select(attrs=_SELECT),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization is not None:
            self.fields['registered_branch'].queryset = active_branches(organization)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('date_of_birth') and cleaned.get('approx_age_years'):
            # Mirrors the DB check constraint so the user sees a field error
            # rather than an IntegrityError.
            raise forms.ValidationError(
                'Record either a date of birth or an approximate age, not both.'
            )
        return cleaned


class ClinicalProfileForm(forms.ModelForm):
    class Meta:
        model = PatientClinicalProfile
        fields = ['medical_history', 'allergies']
        widgets = {
            'medical_history': forms.Textarea(attrs={**_TEXTAREA, 'rows': 6}),
            'allergies': forms.Textarea(attrs=_TEXTAREA),
        }
