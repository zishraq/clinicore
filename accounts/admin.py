"""Admin is for the developer, not the customer (SPEC §6.1)."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import Membership, User


@admin.register(User)
class ClinicoreUserAdmin(UserAdmin):
    ordering = ['full_name']
    list_display = ['phone', 'full_name', 'email', 'is_active', 'is_staff']
    search_fields = ['phone', 'full_name', 'email']
    fieldsets = [
        (None, {'fields': ['phone', 'password']}),
        ('Personal', {'fields': ['full_name', 'email']}),
        (
            'Permissions',
            {'fields': ['is_active', 'is_staff', 'is_superuser', 'groups']},
        ),
        ('Dates', {'fields': ['last_login', 'date_joined']}),
    ]
    add_fieldsets = [
        (
            None,
            {
                'classes': ['wide'],
                'fields': ['phone', 'full_name', 'password1', 'password2'],
            },
        )
    ]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'role', 'is_active']
    list_filter = ['organization', 'role', 'is_active']
