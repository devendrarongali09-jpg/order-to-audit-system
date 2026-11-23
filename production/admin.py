
from django.contrib import admin, messages
from django.core.exceptions import ValidationError


from .models import (
    BillOfMaterials,
    BOMItem,
    ProductRecipe,
    ProductionRun,
    ProductionConsumption,
    ProductionCost,
    ProductionRunAuditLog
)
from .audit import snapshot_production_run

from .services import (
    initialize_production_consumptions,
    post_production_run,
    calculate_production_cost
)


class BOMItemInline(admin.TabularInline):
    model = BOMItem
    extra = 1


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(admin.ModelAdmin):

    list_display = (
        "code",
        "name",
        "version",
        "is_active"
    )

    search_fields = ("code", "name")
    list_filter = ("is_active",)
    inlines = [BOMItemInline]


@admin.register(BOMItem)
class BOMItemAdmin(admin.ModelAdmin):

    list_display = (
        "bom",
        "raw_material",
        "quantity_per_batch"
    )

    list_filter = ("bom",)


@admin.register(ProductRecipe)
class ProductRecipeAdmin(admin.ModelAdmin):

    list_display = (
        "product",
        "bom",
        "expected_output_quantity",
        "output_unit"
    )

    search_fields = ("product__name", "bom__code")


class ProductionConsumptionInline(admin.TabularInline):

    model = ProductionConsumption
    extra = 0
    can_delete = False

    fields = (
        "raw_material",
        "planned_quantity",
        "actual_quantity"
    )

    readonly_fields = ("planned_quantity",)

    def has_change_permission(self, request, obj=None):
        if obj and obj.status == ProductionRun.Status.POSTED:
            return False

        return super().has_change_permission(request, obj)


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):

    list_display = (
        "run_number",
        "product_recipe",
        "production_date",
        "batch_count",
        "expected_output_quantity",
        "actual_good_output",
        "yield_variance_percent",
        "status"
    )

    list_filter = (
        "status",
        "production_date"
    )

    search_fields = (
        "run_number",
        "product_recipe__product__name"
    )

    readonly_fields = (
        "created_at",
        "posted_at"
    )

    inlines = [ProductionConsumptionInline]

    actions = ["post_selected_runs"]

    def has_change_permission(self, request, obj=None):
        if obj and obj.status == ProductionRun.Status.POSTED:
            return False

        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == ProductionRun.Status.POSTED:
            return False

        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        request._production_run_audit_previous = None

        if change:
            previous_run = ProductionRun.objects.get(pk=obj.pk)
            request._production_run_audit_previous = (
                snapshot_production_run(previous_run)
            )
        else:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        run = form.instance

        if not run.consumptions.exists():
            initialize_production_consumptions(
                production_run_id=run.pk
            )

        previous = request._production_run_audit_previous
        current = snapshot_production_run(run)

        if not change:
            action = ProductionRunAuditLog.Action.CREATED
            previous = {}
        elif previous != current:
            action = ProductionRunAuditLog.Action.UPDATED
        else:
            return

        ProductionRunAuditLog.objects.create(
            production_run=run,
            action=action,
            previous_values=previous or {},
            new_values=current,
            changed_by=request.user,
        )

    @admin.action(
        description="Post selected production runs to stock"
    )
    def post_selected_runs(self, request, queryset):

        for run in queryset:

            try:
                post_production_run(
                    production_run_id=run.pk,
                    user=request.user
                )

            except ValidationError as exc:

                self.message_user(
                    request,
                    f"{run.run_number}: {exc}",
                    level=messages.ERROR
                )

            else:

                self.message_user(
                    request,
                    f"{run.run_number} posted successfully.",
                    level=messages.SUCCESS
                )


@admin.register(ProductionConsumption)
class ProductionConsumptionAdmin(admin.ModelAdmin):

    list_display = (
        "production_run",
        "raw_material",
        "planned_quantity",
        "actual_quantity",
        "variance_quantity",
        "variance_percent"
    )

    readonly_fields = (
        "planned_quantity",
    )

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(ProductionCost)
class ProductionCostAdmin(admin.ModelAdmin):

    list_display = (
        "production_run",
        "labour_cost",
        "gas_power_cost",
        "material_cost",
        "total_cost",
        "cost_per_packet",
        "calculated_at"
    )

    readonly_fields = (
        "material_cost",
        "total_cost",
        "cost_per_packet",
        "calculated_at"
    )

    actions = ["calculate_selected_costs"]

    @admin.action(
        description="Calculate weighted-average production costs"
    )
    def calculate_selected_costs(self, request, queryset):

        for cost_record in queryset:

            try:
                result = calculate_production_cost(
                    cost_record_id=cost_record.pk
                )

            except ValidationError as exc:

                self.message_user(
                    request,
                    str(exc),
                    level=messages.ERROR
                )

            else:

                self.message_user(
                    request,
                    f"{result.production_run.run_number}: "
                    f"Cost per packet ₹{result.cost_per_packet}",
                    level=messages.SUCCESS
                )

@admin.register(ProductionRunAuditLog)
class ProductionRunAuditLogAdmin(admin.ModelAdmin):

    list_display = (
        "production_run",
        "action",
        "changed_by",
        "changed_at",
    )

    list_filter = ("action", "changed_at")

    search_fields = (
        "production_run__run_number",
        "changed_by__username",
    )

    readonly_fields = (
        "production_run",
        "action",
        "previous_values",
        "new_values",
        "changed_by",
        "changed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
