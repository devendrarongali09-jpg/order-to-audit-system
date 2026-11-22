
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import Purchase, StockMovement


INCOMING_TYPES = [
    StockMovement.MovementType.OPENING_IN,
    StockMovement.MovementType.PURCHASE_IN,
    StockMovement.MovementType.PRODUCTION_OUTPUT,
    StockMovement.MovementType.RETURN_IN,
    StockMovement.MovementType.ADJUSTMENT_IN,
]

OUTGOING_TYPES = [
    StockMovement.MovementType.PRODUCTION_ISSUE,
    StockMovement.MovementType.DISPATCH,
    StockMovement.MovementType.ADJUSTMENT_OUT,
]


def get_stock_balance(*, raw_material=None, product=None):

    if (raw_material is None) == (product is None):
        raise ValueError(
            "Provide exactly one raw material or product."
        )

    filters = (
        {"raw_material": raw_material}
        if raw_material is not None
        else {"product": product}
    )

    totals = StockMovement.objects.filter(
        **filters
    ).aggregate(
        incoming=Sum(
            "quantity",
            filter=Q(movement_type__in=INCOMING_TYPES)
        ),
        outgoing=Sum(
            "quantity",
            filter=Q(movement_type__in=OUTGOING_TYPES)
        )
    )

    incoming = totals["incoming"] or Decimal("0")
    outgoing = totals["outgoing"] or Decimal("0")

    return incoming - outgoing


@transaction.atomic
def post_purchase(*, purchase_id, user):

    purchase = Purchase.objects.select_for_update().get(
        pk=purchase_id
    )

    if purchase.status != Purchase.Status.DRAFT:
        raise ValidationError(
            "Only draft purchases can be posted."
        )

    items = list(
        purchase.items.select_related(
            "raw_material__unit"
        ).all()
    )

    if not items:
        raise ValidationError(
            "Cannot post a purchase without items."
        )

    for item in items:
        item.full_clean()

        movement = StockMovement(
            raw_material=item.raw_material,
            movement_type=StockMovement.MovementType.PURCHASE_IN,
            quantity=item.quantity,
            unit=item.raw_material.unit,
            unit_cost=item.unit_cost,
            purchase=purchase,
            reference_type="purchase",
            reference_number=purchase.purchase_number,
            notes="Posted purchase receipt.",
            created_by=user,
        )

        movement.full_clean()
        movement.save()

    purchase.status = Purchase.Status.POSTED
    purchase.posted_at = timezone.now()

    purchase.save(
        update_fields=["status", "posted_at"]
    )

    return purchase
