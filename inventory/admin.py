
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import StockMovement, Purchase, PurchaseItem
from .services import post_purchase


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):

    list_display = (
        "purchase_number",
        "supplier",
        "purchase_date",
        "status",
        "total_amount"
    )

    list_filter = ("status", "purchase_date")

    search_fields = (
        "purchase_number",
        "supplier__name"
    )

    inlines = [PurchaseItemInline]

    readonly_fields = ("created_at", "posted_at")

    actions = ["post_selected_purchases"]

    @admin.action(
        description="Post selected draft purchases to stock"
    )
    def post_selected_purchases(self, request, queryset):

        for purchase in queryset:
            try:
                post_purchase(
                    purchase_id=purchase.pk,
                    user=request.user
                )

            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{purchase.purchase_number}: {exc}",
                    level=messages.ERROR
                )

            else:
                self.message_user(
                    request,
                    f"{purchase.purchase_number} posted successfully.",
                    level=messages.SUCCESS
                )


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):

    list_display = (
        "movement_type",
        "raw_material",
        "product",
        "quantity",
        "unit",
        "reference_number",
        "created_at"
    )

    list_filter = ("movement_type", "created_at")

    search_fields = (
        "raw_material__name",
        "product__name",
        "reference_number"
    )

    readonly_fields = (
        "raw_material",
        "product",
        "purchase",
        "movement_type",
        "quantity",
        "unit",
        "unit_cost",
        "reference_type",
        "reference_number",
        "notes",
        "created_by",
        "created_at"
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
