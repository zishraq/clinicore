"""Form plumbing for organization-scoped models.

``ForeignKey.formfield()`` builds its queryset from ``Model._default_manager``
at *class definition* time — i.e. at import, when no organization is active and
``OrgScopedManager`` correctly raises. Forms narrow the queryset per
organization in ``__init__`` anyway, so relation fields start from the
unfiltered manager and are restricted before they are ever rendered.

See docs/adr/0005-org-scoped-default-manager.md and docs/MVP-NOTES.md.
"""

from django import forms
from django.utils.text import capfirst

__all__ = ['date_widget', 'org_scoped_formfield']


def org_scoped_formfield(model_field, **kwargs):
    """``Meta.formfield_callback`` for forms touching org-owned relations."""
    remote_model = getattr(model_field.remote_field, 'model', None)
    if remote_model is None or not hasattr(remote_model, 'all_objects'):
        return model_field.formfield(**kwargs)

    defaults = {
        # Empty by default: a form that forgets to set a per-organization
        # queryset shows no options rather than every tenant's rows.
        'queryset': remote_model.all_objects.none(),
        'required': not model_field.blank,
        'label': capfirst(model_field.verbose_name),
        'help_text': model_field.help_text,
    }
    defaults.update(kwargs)
    return forms.ModelChoiceField(**defaults)


def date_widget(**attrs):
    """A date box the application draws itself, not the operating system.

    ``type='date'`` is deliberately absent: the native control renders its text
    in the device's locale, so the same field reads d/m/Y here and m/d/Y on a
    laptop from elsewhere. ``data-datepicker`` hands it to
    static/js/date-picker.js instead. The stored and posted value is unchanged
    — hence the explicit ISO ``format``, which pins the rendered value rather
    than leaving it to whatever ``LANGUAGE_CODE`` happens to be.

    See docs/adr/0016-one-date-picker-the-app-controls.md.
    """
    return forms.DateInput(
        attrs={
            'class': 'input input-bordered w-full',
            'data-datepicker': '',
            'autocomplete': 'off',
            **attrs,
        },
        format='%Y-%m-%d',
    )
