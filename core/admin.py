"""Admin base for organization-owned models.

The admin is cross-tenant by nature, so it reads through the unfiltered manager
rather than tripping ``ActiveOrganizationRequired`` on every changelist. This is
the reviewed exception documented in docs/adr/0005-org-scoped-default-manager.md.
"""

from django.contrib import admin

__all__ = ['OrgOwnedAdmin']


class OrgOwnedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        # all_objects is unfiltered; using unscoped() here would not survive the
        # lazy queryset outliving the with-block.
        return self.model.all_objects.get_queryset()
