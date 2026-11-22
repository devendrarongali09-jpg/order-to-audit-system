
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


from inventory.models import StockMovement
from inventory.services import get_stock_balance
from .models import ProductionCost
from inventory.models import StockMovement

from .models import (
    ProductionRun,
    ProductionConsumption,
    BOMItem
)


@transaction.atomic
def initialize_production_consumptions(*, production_run_id):

    run = ProductionRun.objects.select_for_update().get(
        pk=production_run_id
    )

    if run.status != ProductionRun.Status.DRAFT:
        raise ValidationError(
            "Cannot initialize a posted production run."
        )

    if run.consumptions.exists():
        raise ValidationError(
            "Production consumption lines already exist."
        )

    bom_items = list(
        BOMItem.objects.filter(
            bom=run.product_recipe.bom
        ).select_related("raw_material")
    )

    if not bom_items:
        raise ValidationError(
            "The selected BOM has no material items."
        )

    for item in bom_items:

        planned = (
            item.quantity_per_batch * run.batch_count
        )

        consumption = ProductionConsumption(
            production_run=run,
            raw_material=item.raw_material,
            planned_quantity=planned,
            actual_quantity=planned
        )

        consumption.full_clean()
        consumption.save()

    return run


@transaction.atomic
def post_production_run(*, production_run_id, user):

    run = ProductionRun.objects.select_for_update().get(
        pk=production_run_id
    )

    if run.status != ProductionRun.Status.DRAFT:
        raise ValidationError(
            "This production run has already been posted."
        )

    if run.actual_good_output <= 0:
        raise ValidationError(
            "Actual good output must be greater than zero."
        )

    if run.actual_good_output > run.expected_output_quantity:
        raise ValidationError(
            "Actual output cannot exceed expected output "
            "without an approved production adjustment."
        )

    consumptions = list(
        run.consumptions.select_related(
            "raw_material__unit"
        ).all()
    )

    bom_items = list(
        BOMItem.objects.filter(
            bom=run.product_recipe.bom
        ).select_related("raw_material__unit")
    )

    if len(consumptions) != len(bom_items):
        raise ValidationError(
            "Production consumption lines do not match the BOM."
        )

    actual_material_ids = {
        row.raw_material_id for row in consumptions
    }

    expected_material_ids = {
        item.raw_material_id for item in bom_items
    }

    if actual_material_ids != expected_material_ids:
        raise ValidationError(
            "Every BOM material must have exactly one "
            "production consumption record."
        )

    for row in consumptions:

        row.full_clean()

        if row.actual_quantity < 0:
            raise ValidationError(
                "Actual consumption cannot be negative."
            )

        current_stock = get_stock_balance(
            raw_material=row.raw_material
        )

        if current_stock < row.actual_quantity:
            raise ValidationError(
                f"Insufficient stock for "
                f"{row.raw_material.name}. "
                f"Available: {current_stock}, "
                f"Required: {row.actual_quantity}."
            )

        row.save()

    # Deduct the actual raw materials used.
    for row in consumptions:

        if row.actual_quantity == 0:
            continue

        movement = StockMovement(
            raw_material=row.raw_material,
            production_run=run,
            movement_type=(
                StockMovement.MovementType.PRODUCTION_ISSUE
            ),
            quantity=row.actual_quantity,
            unit=row.raw_material.unit,
            reference_type="production_run",
            reference_number=run.run_number,
            notes="Raw material issued to production.",
            created_by=user
        )

        movement.full_clean()
        movement.save()

    # Add only good finished packets to inventory.
    output_movement = StockMovement(
        product=run.product_recipe.product,
        production_run=run,
        movement_type=(
            StockMovement.MovementType.PRODUCTION_OUTPUT
        ),
        quantity=run.actual_good_output,
        unit=run.product_recipe.output_unit,
        reference_type="production_run",
        reference_number=run.run_number,
        notes="Good finished packets produced.",
        created_by=user
    )

    output_movement.full_clean()
    output_movement.save()

    # Mark the production run as posted.
    run.status = ProductionRun.Status.POSTED
    run.posted_at = timezone.now()

    run.save(
        update_fields=["status", "posted_at"]
    )

    return run

@transaction.atomic
def calculate_production_cost(*, cost_record_id):

    cost_record = ProductionCost.objects.select_for_update().get(
        pk=cost_record_id
    )

    run = ProductionRun.objects.select_for_update().get(
        pk=cost_record.production_run_id
    )

    if run.status != ProductionRun.Status.POSTED:
        raise ValidationError(
            "Only posted production runs can be costed."
        )

    if run.actual_good_output <= 0:
        raise ValidationError(
            "Production output must be greater than zero."
        )

    consumptions = list(
        run.consumptions.select_related(
            "raw_material"
        ).all()
    )

    if not consumptions:
        raise ValidationError(
            "Production consumption records are missing."
        )

    receipt_types = [
        StockMovement.MovementType.OPENING_IN,
        StockMovement.MovementType.PURCHASE_IN,
        StockMovement.MovementType.ADJUSTMENT_IN,
    ]

    material_cost = Decimal("0")

    for item in consumptions:

        receipts = StockMovement.objects.filter(
            raw_material=item.raw_material,
            movement_type__in=receipt_types,
            unit_cost__isnull=False,
            created_at__lte=run.created_at
        ).order_by("created_at", "pk")

        received_quantity = Decimal("0")
        received_value = Decimal("0")

        for receipt in receipts:
            received_quantity += receipt.quantity
            received_value += receipt.quantity * receipt.unit_cost

        if received_quantity <= 0:
            raise ValidationError(
                f"No costed receipts found for "
                f"{item.raw_material.name}."
            )

        average_cost = received_value / received_quantity

        material_cost += (
            item.actual_quantity * average_cost
        )

    material_cost = material_cost.quantize(
        Decimal("0.01")
    )

    overhead = (
        cost_record.labour_cost
        + cost_record.gas_power_cost
        + cost_record.other_overhead
    )

    total_cost = material_cost + overhead

    cost_per_packet = (
        total_cost / run.actual_good_output
    ).quantize(Decimal("0.01"))

    cost_record.material_cost = material_cost
    cost_record.total_cost = total_cost
    cost_record.cost_per_packet = cost_per_packet
    cost_record.calculated_at = timezone.now()

    cost_record.save(
        update_fields=[
            "material_cost",
            "total_cost",
            "cost_per_packet",
            "calculated_at"
        ]
    )

    return cost_record
