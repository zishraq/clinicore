"""Catalog maintenance forms."""

from django import forms

from catalog.models import AdviceTemplate, Product

__all__ = ['AdviceTemplateForm', 'ProductForm']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}
_CHECKBOX = {'class': 'checkbox'}


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name',
            'sku',
            'category',
            'unit',
            'sale_price',
            'reorder_level',
            'is_stock_tracked',
            'is_sellable',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_INPUT),
            'sku': forms.TextInput(attrs=_INPUT),
            'category': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Tablet, syrup, supplement …'}
            ),
            'unit': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Tablet, ml, drops'}
            ),
            'sale_price': forms.NumberInput(
                attrs={**_INPUT, 'type': 'number', 'step': '0.01', 'min': '0'}
            ),
            'reorder_level': forms.NumberInput(
                attrs={**_INPUT, 'type': 'number', 'step': '0.01', 'min': '0'}
            ),
            'is_stock_tracked': forms.CheckboxInput(attrs=_CHECKBOX),
            'is_sellable': forms.CheckboxInput(attrs=_CHECKBOX),
            'is_active': forms.CheckboxInput(attrs=_CHECKBOX),
        }


class AdviceTemplateForm(forms.ModelForm):
    class Meta:
        model = AdviceTemplate
        fields = [
            'text',
            'category',
            'default_frequency',
            'default_duration',
            'is_active',
        ]
        widgets = {
            'text': forms.Textarea(
                attrs={**_TEXTAREA, 'placeholder': 'Walk 30 minutes after dinner'}
            ),
            'category': forms.Select(attrs=_SELECT),
            'default_frequency': forms.TextInput(
                attrs={**_INPUT, 'placeholder': 'Daily'}
            ),
            'default_duration': forms.TextInput(
                attrs={**_INPUT, 'placeholder': '1 month'}
            ),
            'is_active': forms.CheckboxInput(attrs=_CHECKBOX),
        }
