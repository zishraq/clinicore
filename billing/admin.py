"""Admin is for the developer, not the customer (SPEC §6.1)."""

from django.contrib import admin

from billing.models import Invoice, InvoiceItem, Payment
from core.admin import OrgOwnedAdmin


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    # Snapshots and the computed total are read-only here for the same reason
    # they are not form fields: they are frozen at issue time.
    readonly_fields = ['name_snapshot', 'line_total']


@admin.register(Invoice)
class InvoiceAdmin(OrgOwnedAdmin):
    list_display = ['number', 'patient', 'issued_at', 'status', 'organization']
    list_filter = ['organization', 'status']
    search_fields = ['number']
    inlines = [InvoiceItemInline]


@admin.register(Payment)
class PaymentAdmin(OrgOwnedAdmin):
    list_display = ['invoice', 'amount', 'method', 'received_at', 'voided_at']
    list_filter = ['organization', 'method']
