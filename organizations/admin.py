"""Admin is for the developer, not the customer (SPEC §6.1)."""

from django.contrib import admin

from core.admin import OrgOwnedAdmin
from organizations.models import Branch, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'currency', 'timezone', 'is_active']
    prepopulated_fields = {'slug': ['name']}


@admin.register(Branch)
class BranchAdmin(OrgOwnedAdmin):
    list_display = ['name', 'code', 'organization', 'is_active']
    list_filter = ['organization', 'is_active']
