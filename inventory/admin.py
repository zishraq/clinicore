"""Admin is for the developer, not the customer (SPEC §6.1)."""

from django.contrib import admin

from core.admin import OrgOwnedAdmin
from inventory.models import GoodsReceipt, GoodsReceiptItem, StockBatch, StockMovement


class GoodsReceiptItemInline(admin.TabularInline):
    model = GoodsReceiptItem
    extra = 0


@admin.register(StockBatch)
class StockBatchAdmin(OrgOwnedAdmin):
    list_display = ['product', 'branch', 'lot_number', 'expiry_date', 'cost_price']
    list_filter = ['organization', 'branch']
    search_fields = ['lot_number']


@admin.register(StockMovement)
class StockMovementAdmin(OrgOwnedAdmin):
    """Read-only on purpose: the ledger is append-only (ADR 0009)."""

    list_display = ['batch', 'movement_type', 'quantity', 'occurred_at', 'created_by']
    list_filter = ['organization', 'movement_type']

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(OrgOwnedAdmin):
    list_display = ['number', 'branch', 'supplier', 'received_at']
    list_filter = ['organization', 'branch']
    search_fields = ['number', 'supplier', 'reference']
    inlines = [GoodsReceiptItemInline]
