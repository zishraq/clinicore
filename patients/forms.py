"""Patient forms. Demographics and clinical narrative are separate forms.

STAFF is served ``PatientForm`` only — the split is what makes the permission
boundary structural rather than a hidden template block.
"""

from django import forms

from core.forms import org_scoped_formfield
from organizations.services import active_branches, default_branch
from patients.models import Patient, PatientClinicalProfile

__all__ = ['ClinicalProfileForm', 'PatientForm']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}


class PatientForm(forms.ModelForm):
    """Demographics.

    ``approx_age_years`` is deliberately absent: the column and the rows that
    already carry a value stay exactly as they are, but reception no longer has
    two ways to record one fact. A date of birth entered later supersedes the
    estimate — see ``clean()``.
    """

    class Meta:
        # registered_branch points at an org-scoped model; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = Patient
        fields = [
            'full_name',
            'phone',
            'sex',
            'date_of_birth',
            'address',
            'registered_branch',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs=_INPUT),
            'phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'sex': forms.Select(attrs=_SELECT),
            'date_of_birth': forms.DateInput(attrs={**_INPUT, 'type': 'date'}),
            'address': forms.Textarea(attrs=_TEXTAREA),
            'registered_branch': forms.Select(attrs=_SELECT),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization is None:
            return
        self.fields['registered_branch'].queryset = active_branches(organization)
        if self.instance.pk is None:
            # Registration happens at a desk in a building; asking which one
            # every time is a dropdown nobody reads. Multi-branch clinics can
            # still change it.
            branch = default_branch(organization)
            if branch is not None:
                self.initial.setdefault('registered_branch', branch.pk)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('date_of_birth') and self.instance.approx_age_years is not None:
            # The two are mutually exclusive at the database level, and a real
            # date of birth is strictly better than an estimate — so it replaces
            # it rather than colliding with it. Nothing else can reach the
            # estimate now that it is off the form, so this is the one path that
            # has to resolve the pair.
            self.instance.approx_age_years = None
        return cleaned


class ClinicalProfileForm(forms.ModelForm):
    class Meta:
        model = PatientClinicalProfile
        fields = ['medical_history', 'allergies']
        widgets = {
            'medical_history': forms.Textarea(attrs={**_TEXTAREA, 'rows': 6}),
            'allergies': forms.Textarea(attrs=_TEXTAREA),
        }
