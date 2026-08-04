"""Invoice, line, payment, and void forms.

The line row mirrors the prescription row (clinical/forms.py): the visible box
is ``display_name``, which is not a model field, and the real source — a catalog
product or plain typed text — is decided in ``clean()``. That keeps the row
working with JavaScript disabled and keeps the check constraint unbreakable
from a form post.
"""

from decimal import Decimal

from django import forms

from billing.models import (
    Invoice,
    InvoiceItem,
    LineType,
    Payment,
    PaymentMethod,
)
from billing.money import ZERO, to_money
from catalog.models import Product
from clinical.models import Encounter
from core.forms import org_scoped_formfield
from organizations.models import Branch
from patients.models import Patient

__all__ = [
    'InvoiceForm',
    'InvoiceItemFormSet',
    'PaymentForm',
    'VoidForm',
    'invoice_item_formset_class',
]

#: A line with no quantity typed is one of something.
ONE = Decimal('1')

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 2}
_SELECT = {'class': 'select select-bordered w-full'}
_AMOUNT_INPUT = {
    **_INPUT,
    'type': 'number',
    'step': '0.01',
    'min': '0',
    'inputmode': 'decimal',
}


class InvoiceForm(forms.ModelForm):
    class Meta:
        # patient and encounter are org-scoped relations; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = Invoice
        fields = ['patient', 'encounter', 'branch', 'notes']
        widgets = {
            'patient': forms.Select(attrs=_SELECT),
            'branch': forms.Select(attrs=_SELECT),
            # Set from the visit the bill was started from, never picked in a
            # dropdown — but still a real field, so a tampered post cannot
            # attach another tenant's visit.
            'encounter': forms.HiddenInput(),
            'notes': forms.Textarea(attrs=_TEXTAREA),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization is None:
            return
        self.fields['patient'].queryset = Patient.objects.for_organization(organization)
        self.fields['encounter'].queryset = Encounter.objects.for_organization(
            organization
        )
        branches = Branch.objects.for_organization(organization).filter(is_active=True)
        self.fields['branch'].queryset = branches
        # A single-branch clinic is never asked which shelf: the service fills
        # it in. The field only appears where the answer is genuinely open.
        if branches.count() < 2:
            del self.fields['branch']


class InvoiceItemForm(forms.ModelForm):
    """One billed line: the consultation, a catalog product, or typed text."""

    display_name = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                **_INPUT,
                'placeholder': 'Search products, or type anything…',
                'autocomplete': 'off',
                'data-role': 'line-search',
            }
        ),
    )

    class Meta:
        # product is an org-scoped relation; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = InvoiceItem
        fields = [
            'line_type',
            'product',
            'quantity',
            'unit_price',
            'discount',
            'sort_order',
        ]
        widgets = {
            'line_type': forms.HiddenInput(attrs={'data-role': 'line-type'}),
            'product': forms.HiddenInput(attrs={'data-role': 'line-product'}),
            'quantity': forms.NumberInput(
                attrs={**_AMOUNT_INPUT, 'step': '0.01', 'min': '0.01'}
            ),
            'unit_price': forms.NumberInput(attrs=_AMOUNT_INPUT),
            'discount': forms.NumberInput(attrs=_AMOUNT_INPUT),
            'sort_order': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # clean() derives the type from whichever source won, so a posted value
        # is a hint; a client without JavaScript never sends one.
        self.fields['line_type'].required = False
        # An empty row must post nothing that reads as content, or has_changed()
        # below cannot tell a skipped row from a half-filled one.
        self.fields['unit_price'].required = False
        self.fields['quantity'].required = False
        self.fields['product'].queryset = Product.all_objects.none()
        if self.instance.pk:
            self.fields['display_name'].initial = self.instance.name_snapshot

    def bind_organization(self, organization) -> None:
        """Restrict the catalog relation to one tenant."""
        self.fields['product'].queryset = Product.objects.for_organization(organization)

    #: Fields whose presence means someone actually entered a line.
    CONTENT_FIELDS = ('display_name', 'product', 'unit_price')

    def has_changed(self) -> bool:
        """True only when the row holds something.

        Same reasoning as the prescription row: a removed unsaved row posts no
        inputs at all, and Django's default would read that gap as a filled-in
        row and fail it for having no name.
        """
        if self.instance.pk:
            return super().has_changed()
        return any(self.data.get(self.add_prefix(name)) for name in self.CONTENT_FIELDS)

    def clean(self):
        cleaned = super().clean()
        display = (cleaned.get('display_name') or '').strip()
        product = cleaned.get('product')
        posted_type = cleaned.get('line_type')

        if product is not None:
            cleaned['line_type'] = LineType.PRODUCT
            # The catalog name at issue time is what gets frozen, not whatever
            # is left in the search box.
            cleaned['name_snapshot'] = product.name[:300]
        else:
            cleaned['line_type'] = (
                LineType.CONSULTATION
                if posted_type == LineType.CONSULTATION
                else LineType.OTHER
            )
            cleaned['name_snapshot'] = display[:300]

        if not cleaned['name_snapshot']:
            if self.has_changed():
                raise forms.ValidationError('Give this line a description.')
            return cleaned

        quantity = cleaned.get('quantity')
        if quantity is None:
            quantity = ONE
        if quantity <= ZERO:
            raise forms.ValidationError('Quantity must be more than zero.')
        cleaned['quantity'] = quantity
        cleaned['unit_price'] = to_money(cleaned.get('unit_price') or ZERO)
        cleaned['discount'] = to_money(cleaned.get('discount') or ZERO)

        gross = to_money(cleaned['quantity'] * cleaned['unit_price'])
        if cleaned['discount'] > gross:
            raise forms.ValidationError(
                f'The discount is more than this line is worth ({gross}).'
            )
        return cleaned

    def save(self, commit=True):
        item = super().save(commit=False)
        # name_snapshot is not a form field: it is frozen from whichever source
        # clean() settled on, so the live catalog can never rewrite it later.
        item.name_snapshot = self.cleaned_data.get('name_snapshot', '')
        if commit:
            item.save()
        return item


class BaseInvoiceItemFormSet(forms.BaseInlineFormSet):
    """Passes the organization down so each row's catalog queryset is scoped."""

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
            deletion.widget = forms.CheckboxInput(
                attrs={'class': 'hidden', 'data-role': 'line-delete'}
            )

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        live = [
            form
            for form in self.forms
            # A saved row that was left alone has not "changed" but is still a
            # line on the bill, so it counts.
            if (form.instance.pk or form.has_changed())
            and not form.cleaned_data.get('DELETE')
        ]
        if not live:
            raise forms.ValidationError('Add at least one line to this bill.')


def invoice_item_formset_class(*, extra: int = 1):
    """Formset class with room for ``extra`` blank rows.

    Built per call rather than mutating ``formset.extra`` after construction:
    creating a bill from a visit needs the prefilled consultation line plus one
    empty row, and the count has to be known before the forms are built.
    """
    return forms.inlineformset_factory(
        Invoice,
        InvoiceItem,
        form=InvoiceItemForm,
        formset=BaseInvoiceItemFormSet,
        extra=extra,
        can_delete=True,
    )


InvoiceItemFormSet = invoice_item_formset_class()


class PaymentForm(forms.ModelForm):
    """Amount and method. The balance check itself lives in the service."""

    class Meta:
        model = Payment
        fields = ['amount', 'method', 'note']
        widgets = {
            'amount': forms.NumberInput(
                attrs={**_AMOUNT_INPUT, 'min': '0.01', 'placeholder': '0.00'}
            ),
            'method': forms.Select(attrs=_SELECT),
            'note': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Reference, if any'}
            ),
        }

    def __init__(self, *args, balance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['method'].initial = PaymentMethod.CASH
        if balance is not None:
            # A hint, not the guard: the real check is under a row lock in
            # services.record_payment, because the balance can move between
            # rendering this form and posting it.
            self.fields['amount'].widget.attrs['max'] = f'{balance}'
            self.fields['amount'].help_text = f'Balance now: {balance}'


class VoidForm(forms.Form):
    """Reversal always carries a reason, and the actor comes from the request."""

    reason = forms.CharField(
        max_length=300,
        widget=forms.TextInput(
            attrs={**_INPUT, 'placeholder': 'Why is this being voided?'}
        ),
    )
