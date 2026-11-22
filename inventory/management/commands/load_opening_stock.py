
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from masterdata.models import (
    RawMaterial,
    Product,
    UnitOfMeasure
)

from inventory.models import StockMovement


OPENING_REFERENCE = "OPENING-001"


class Command(BaseCommand):

    help = "Load the internship benchmark opening stock."

    @transaction.atomic
    def handle(self, *args, **options):

        User = get_user_model()

        admin_user = User.objects.filter(
            is_superuser=True
        ).order_by("pk").first()

        if admin_user is None:
            raise CommandError(
                "Create a Django superuser first."
            )

        units = {
            unit.symbol: unit
            for unit in UnitOfMeasure.objects.filter(
                symbol__in=["kg", "L", "piece"]
            )
        }

        required_units = ["kg", "L", "piece"]

        missing_units = [
            symbol for symbol in required_units
            if symbol not in units
        ]

        if missing_units:
            raise CommandError(
                f"Missing units: {', '.join(missing_units)}"
            )

        raw_stock = [
            ("RM-FLOUR-001", "100", "kg", "42.0000"),
            ("RM-OIL-001", "30", "L", "140.0000"),
            ("RM-SPICE-001", "5", "kg", "200.0000"),
            ("PKG-POUCH-200G", "500", "piece", "2.5000"),
            ("PKG-CARTON-20", "30", "piece", "15.0000"),
        ]

        for sku, quantity, symbol, cost in raw_stock:

            material = RawMaterial.objects.get(sku=sku)

            if material.unit_id != units[symbol].pk:
                raise CommandError(
                    f"Incorrect unit configured for {sku}."
                )

            self.create_opening_movement(
                raw_material=material,
                product=None,
                quantity=Decimal(quantity),
                unit=units[symbol],
                unit_cost=Decimal(cost),
                user=admin_user,
            )

        product = Product.objects.get(
            sku="SNK-MUR-200"
        )

        self.create_opening_movement(
            raw_material=None,
            product=product,
            quantity=Decimal("50"),
            unit=units["piece"],
            unit_cost=None,
            user=admin_user,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Benchmark opening stock loaded successfully."
            )
        )

    # IMPORTANT: This method must remain INSIDE the Command class.

    def create_opening_movement(
        self,
        *,
        raw_material,
        product,
        quantity,
        unit,
        unit_cost,
        user
    ):

        lookup = {
            "movement_type": StockMovement.MovementType.OPENING_IN,
            "reference_type": "opening_balance",
            "reference_number": OPENING_REFERENCE,
            "raw_material": raw_material,
            "product": product,
        }

        existing = StockMovement.objects.filter(
            **lookup
        ).first()

        if existing:

            if (
                existing.quantity != quantity
                or existing.unit_id != unit.pk
                or existing.unit_cost != unit_cost
            ):
                raise CommandError(
                    "Existing opening stock differs from benchmark data. "
                    "Review the ledger before proceeding."
                )

            self.stdout.write(
                f"Already loaded: {raw_material or product}"
            )
            return

        movement = StockMovement(
            movement_type=StockMovement.MovementType.OPENING_IN,
            reference_type="opening_balance",
            reference_number=OPENING_REFERENCE,
            raw_material=raw_material,
            product=product,
            quantity=quantity,
            unit=unit,
            unit_cost=unit_cost,
            notes="Initial benchmark opening balance.",
            created_by=user,
        )

        movement.full_clean()
        movement.save()

        self.stdout.write(
            f"Loaded: {raw_material or product} - {quantity}"
        )
