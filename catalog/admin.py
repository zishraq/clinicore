"""Admin is for the developer, not the customer (SPEC §6.1)."""

from django.contrib import admin

from catalog.models import AdviceTemplate, Product
from core.admin import OrgOwnedAdmin


@admin.register(Product)
class ProductAdmin(OrgOwnedAdmin):
    list_display = ['name', 'sku', 'category', 'organization', 'is_active']
    list_filter = ['organization', 'is_active', 'is_stock_tracked', 'is_sellable']
    search_fields = ['name', 'sku']


@admin.register(AdviceTemplate)
class AdviceTemplateAdmin(OrgOwnedAdmin):
    list_display = ['prescribing_name', 'category', 'organization', 'is_active']
    list_filter = ['organization', 'category', 'is_active']
    search_fields = ['text']
