"""Patient demographics. The clinical narrative is a different form entirely.

STAFF is served this module only — the split is what makes the permission
boundary structural rather than a hidden template block. The clinical half is
``patients/case_forms.py``, behind ``clinical_access_required``.
"""

from django import forms

from core.forms import date_widget, org_scoped_formfield
from organizations.services import active_branches, default_branch
from patients.models import MaritalStatus, Patient

__all__ = ['PatientForm']

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
            # The seven demographics the desk also takes (ADR 0020 §9). None is
            # clinical narrative, so none of them belongs on the far side of the
            # split SPEC §6.1 draws — STAFF records all of these.
            'marital_status',
            'occupation',
            'email',
            'alt_phone',
            'emergency_contact_name',
            'emergency_contact_phone',
            'referred_by',
        ]
        widgets = {
            'full_name': forms.TextInput(attrs=_INPUT),
            'phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'sex': forms.Select(attrs=_SELECT),
            'date_of_birth': date_widget(),
            'address': forms.Textarea(attrs=_TEXTAREA),
            'registered_branch': forms.Select(attrs=_SELECT),
            'marital_status': forms.Select(attrs=_SELECT),
            'occupation': forms.TextInput(attrs=_INPUT),
            'email': forms.EmailInput(attrs=_INPUT),
            'alt_phone': forms.TextInput(attrs={**_INPUT, 'inputmode': 'tel'}),
            'emergency_contact_name': forms.TextInput(attrs=_INPUT),
            'emergency_contact_phone': forms.TextInput(
                attrs={**_INPUT, 'inputmode': 'tel'}
            ),
            'referred_by': forms.TextInput(attrs=_INPUT),
        }
        labels = {
            'alt_phone': 'Alternative phone',
            'emergency_contact_name': 'Emergency contact',
            'emergency_contact_phone': 'Emergency contact phone',
        }

    #: What reception fills in at the desk for everybody, in order. Everything
    #: else on the form goes behind the disclosure — asked when it is relevant,
    #: never in the way of registering someone in ten seconds.
    DESK_FIELDS = (
        'full_name',
        'phone',
        'sex',
        'date_of_birth',
        'address',
        'registered_branch',
    )

    @property
    def desk_fields(self) -> list:
        return [self[name] for name in self.DESK_FIELDS]

    @property
    def detail_fields(self) -> list:
        """The rest, rendered inside "More details".

        Behind a disclosure, and **still on the form and in the DOM** — a closed
        ``<details>`` posts what is inside it. Omitting them with a template
        conditional, or popping them in ``__init__``, would let
        ``construct_instance`` rebuild them as empty and erase an occupation
        recorded last month (ADR 0017's rule, ADR 0020 §9 reuses it).
        """
        return [field for field in self if field.name not in self.DESK_FIELDS]

    @property
    def has_details(self) -> bool:
        """Whether the collapsed half already holds something worth showing.

        Decided server-side through ``BoundField.value()``, so editing a patient
        who has an occupation on file opens the section without JavaScript.
        ``marital_status`` is compared against its default rather than tested
        for truth: every row carries "U", and treating that as content would
        open the disclosure for every patient in the clinic.
        """
        return any(
            field.value() not in (None, '', MaritalStatus.UNKNOWN)
            for field in self.detail_fields
        )

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Not required, unlike ``sex``, and the disclosure is the whole reason.
        # A required field inside a closed ``<details>`` fails validation
        # somewhere the person filling in the form cannot see, and this is a
        # demographic nobody should be stopped by. Absent means "not recorded",
        # which the column already has a value for — see ``clean_marital_status``.
        self.fields['marital_status'].required = False
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

    def clean_marital_status(self) -> str:
        """Blank is the stored "not recorded" value, never an empty string.

        The column mirrors ``Sex``: an explicit unknown rather than a blank that
        cannot be told apart from a question nobody asked.
        """
        return self.cleaned_data.get('marital_status') or MaritalStatus.UNKNOWN

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
