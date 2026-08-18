"""Catalog maintenance forms.

Both forms check their text field against the rest of the catalog. The database
constraint is the guard (``catalog/models.py``), but it names the organization,
which a ModelForm excludes from constraint validation — so without the check
here a duplicate name arrives as a 500 rather than as "that is already in the
catalog". The queryset is organization-scoped through the ambient manager.
"""

from django import forms

from catalog.models import AdviceTemplate, Product

__all__ = ['AdviceTemplateForm', 'ProductForm']

_INPUT = {'class': 'input input-bordered w-full'}
_TEXTAREA = {'class': 'textarea textarea-bordered w-full', 'rows': 3}
_SELECT = {'class': 'select select-bordered w-full'}
_CHECKBOX = {'class': 'checkbox'}


def _already_in_catalog(model, *, field: str, value: str, instance) -> bool:
    """Whether another row in this organization already holds ``value``."""
    queryset = model.objects.filter(**{f'{field}__iexact': value})
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)
    return queryset.exists()


class ProductForm(forms.ModelForm):
    """A catalog medicine.

    ``organization`` is passed so the strength field can be dropped outright
    where the clinic does not record strengths — hiding it in the template
    would leave a field a hand-built POST could still set.
    """

    class Meta:
        model = Product
        fields = [
            'name',
            'sku',
            'category',
            'unit',
            'default_strength',
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
            # list= points at the datalist rendered by the product form
            # template, so the usual values are offered without ruling out
            # anything else.
            'default_strength': forms.TextInput(
                attrs={**_INPUT, 'list': 'strength-options', 'autocomplete': 'off'}
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

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is None:
            return
        if organization.strength_enabled:
            self.fields[
                'default_strength'
            ].label = f'Usual {organization.terms["strength"].lower()}'
        else:
            self.fields.pop('default_strength')

    def clean_name(self) -> str:
        name = self.cleaned_data['name'].strip()
        if _already_in_catalog(
            Product, field='name', value=name, instance=self.instance
        ):
            raise forms.ValidationError(
                'This medicine is already in the catalog. Edit that entry instead — '
                'two rows for one medicine split its prescriptions and its stock.'
            )
        return name


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

    def clean_text(self) -> str:
        text = self.cleaned_data['text'].strip()
        if _already_in_catalog(
            AdviceTemplate, field='text', value=text, instance=self.instance
        ):
            raise forms.ValidationError(
                'This advice is already in the catalog. Edit that entry instead.'
            )
        return text
