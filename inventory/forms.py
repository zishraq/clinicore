"""Goods receipt and stock adjustment forms.

The receipt line is a plain ``Form``, not a ``ModelForm``: one typed row spans
two models — the ``GoodsReceiptItem`` and the ``StockBatch`` it lands in — and
which batch that is, is the service's decision, not the form's.
"""

from decimal import Decimal

from django import forms

from catalog.models import Product
from core.forms import org_scoped_formfield
from inventory.models import GoodsReceipt
from organizations.models import Branch

__all__ = [
    'AdjustmentForm',
    'GoodsReceiptForm',
    'GoodsReceiptItemFormSet',
    'receipt_item_formset_class',
]

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 2}
_SELECT = {'class': 'select select-bordered w-full'}
_NUMBER = {**_INPUT, 'type': 'number', 'step': '0.01', 'inputmode': 'decimal'}


class GoodsReceiptForm(forms.ModelForm):
    """The delivery header: where it landed and who it came from."""

    class Meta:
        # branch is an org-scoped relation; see core/forms.py.
        formfield_callback = staticmethod(org_scoped_formfield)
        model = GoodsReceipt
        fields = ['branch', 'supplier', 'reference', 'received_at', 'notes']
        widgets = {
            'branch': forms.Select(attrs=_SELECT),
            'supplier': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Who delivered this'}
            ),
            'reference': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Their invoice number, if any'}
            ),
            'received_at': forms.DateTimeInput(
                attrs={**_INPUT, 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'
            ),
            'notes': forms.Textarea(attrs=_TEXTAREA),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['received_at'].input_formats = ['%Y-%m-%dT%H:%M']
        branches = Branch.all_objects.none()
        if organization is not None:
            branches = Branch.objects.for_organization(organization).filter(
                is_active=True
            )
            if branches.count() == 1:
                self.fields['branch'].initial = branches.first()
        self.fields['branch'].queryset = branches


class GoodsReceiptItemForm(forms.Form):
    """One delivered line, and the batch details that come with it."""

    product = forms.ModelChoiceField(
        queryset=Product.all_objects.none(),
        required=False,
        widget=forms.Select(attrs={**_SELECT, 'data-role': 'receipt-product'}),
    )
    lot_number = forms.CharField(
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**_INPUT, 'placeholder': 'Lot / batch no.'}),
    )
    expiry_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={**_INPUT, 'type': 'date'})
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={**_NUMBER, 'min': '0.01'}),
    )
    cost_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={**_NUMBER, 'min': '0'}),
    )

    def bind_organization(self, organization) -> None:
        """Restrict the catalog relation to one tenant.

        Only stock-tracked products: receiving something nobody counts would
        write a ledger entry that no page ever reads.
        """
        self.fields['product'].queryset = Product.objects.for_organization(
            organization
        ).filter(is_stock_tracked=True, is_active=True)

    def has_changed(self) -> bool:
        """True only when the row holds something.

        A row nobody touched posts empty inputs; Django's default would read
        that as a filled-in line and fail it for having no product.
        """
        return any(
            self.data.get(self.add_prefix(name)) for name in ('product', 'quantity')
        )

    def clean(self):
        cleaned = super().clean()
        if not self.has_changed():
            return cleaned

        product = cleaned.get('product')
        if product is None:
            raise forms.ValidationError('Choose a product for this line.')

        quantity = cleaned.get('quantity')
        if quantity is None or quantity <= Decimal('0'):
            raise forms.ValidationError('Enter how many arrived.')

        # A tracked product with no expiry date cannot be checked for expiry
        # later, and the block on dispensing expired stock silently stops
        # applying to it. Required at the point it is knowable: now.
        if product.is_stock_tracked and not cleaned.get('expiry_date'):
            raise forms.ValidationError(f'{product.name} needs an expiry date.')
        return cleaned


class BaseGoodsReceiptItemFormSet(forms.BaseFormSet):
    """Passes the organization down and insists on at least one real line."""

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

    @property
    def lines(self) -> list[dict]:
        """The rows someone actually filled in, ready for ``receive_stock``."""
        return [
            form.cleaned_data
            for form in self.forms
            if form.has_changed() and form.cleaned_data.get('product')
        ]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.lines:
            raise forms.ValidationError('Add at least one line to this delivery.')


def receipt_item_formset_class(*, extra: int = 3):
    """Formset class with room for ``extra`` blank rows."""
    return forms.formset_factory(
        GoodsReceiptItemForm, formset=BaseGoodsReceiptItemFormSet, extra=extra
    )


GoodsReceiptItemFormSet = receipt_item_formset_class()


class AdjustmentForm(forms.Form):
    """A manual correction. Signed, and it always says why (SPEC §6.5)."""

    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        label='Change',
        help_text='Negative to write stock off, positive for stock found.',
        widget=forms.NumberInput(attrs={**_NUMBER, 'placeholder': '-1.00'}),
    )
    reason = forms.CharField(
        max_length=300,
        widget=forms.TextInput(
            attrs={**_INPUT, 'placeholder': 'Counted short, damaged in transit, …'}
        ),
    )

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        if quantity == Decimal('0'):
            raise forms.ValidationError('An adjustment of zero changes nothing.')
        return quantity
