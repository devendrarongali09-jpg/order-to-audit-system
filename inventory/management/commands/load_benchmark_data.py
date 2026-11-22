"""Create or validate the internship benchmark master data and opening stock.

This command is intentionally non-destructive:
- Existing master records are validated, never silently overwritten.
- Existing opening stock is delegated to the idempotent load_opening_stock command.
- Posted purchases, production runs, orders, dispatches, and payments are untouched.
- --dry-run performs the same checks inside a transaction and rolls everything back.
"""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import StockMovement
from masterdata.models import (
    Customer,
    Product,
    RawMaterial,
    Supplier,
    UnitOfMeasure,
)
from production.models import BOMItem, BillOfMaterials, ProductRecipe


OPENING_REFERENCE_TYPE = "opening_balance"
OPENING_REFERENCE_NUMBER = "OPENING-001"


class Command(BaseCommand):
    help = (
        "Create or validate the internship benchmark master data and "
        "opening stock without changing posted business transactions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and preview the seed operation, then roll back all changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        output = StringIO()
        original_stdout = self.stdout
        self.stdout = output

        try:
            with transaction.atomic():
                self._require_superuser()
                units = self._ensure_units()
                self._ensure_customers()
                self._ensure_supplier()
                product = self._ensure_product(units)
                materials = self._ensure_raw_materials(units)
                bom = self._ensure_bom()
                self._ensure_bom_items(bom, materials)
                self._ensure_recipe(product, bom, units["piece"])
                self._check_opening_stock_conflicts(materials, product)

                # Reuse the established, idempotent opening-stock command.
                # Its nested transaction is part of this outer transaction.
                call_command("load_opening_stock", stdout=self.stdout)

                self.stdout.write("Benchmark master data and opening stock checks completed.")

                if dry_run:
                    transaction.set_rollback(True)
        finally:
            self.stdout = original_stdout

        prefix = "DRY RUN: " if dry_run else ""
        for line in output.getvalue().splitlines():
            self.stdout.write(f"{prefix}{line}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run finished. All database changes were rolled back."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Benchmark data is ready. Existing posted transactions were not modified."
                )
            )

    def _require_superuser(self):
        user_model = get_user_model()
        if not user_model.objects.filter(is_superuser=True).exists():
            raise CommandError("Create a Django superuser first: python manage.py createsuperuser")

    def _ensure_units(self):
        definitions = {
            "g": ("Gram", UnitOfMeasure.Dimension.MASS, Decimal("0.001000")),
            "kg": ("kilogram", UnitOfMeasure.Dimension.MASS, Decimal("1.000000")),
            "L": ("Litre", UnitOfMeasure.Dimension.VOLUME, Decimal("1.000000")),
            "piece": ("Piece", UnitOfMeasure.Dimension.COUNT, Decimal("1.000000")),
        }
        units = {}

        for symbol, (name, dimension, factor) in definitions.items():
            unit, created = UnitOfMeasure.objects.get_or_create(
                symbol=symbol,
                defaults={
                    "name": name,
                    "dimension": dimension,
                    "conversion_factor": factor,
                    "is_active": True,
                },
            )
            if (
                unit.dimension != dimension
                or unit.conversion_factor != factor
            ):
                raise CommandError(
                    f"Unit '{symbol}' has unexpected dimension or conversion factor. "
                    "Review it manually; the seed command will not overwrite it."
                )
            self._report_record("unit", symbol, created)
            units[symbol] = unit

        return units

    def _ensure_customers(self):
        definitions = [
            {
                "name": "Retailer A",
                "customer_type": Customer.CustomerType.RETAILER,
                "city": "Chennai",
                "state": "Tamil Nadu",
                "state_code": "33",
            },
            {
                "name": "Distributor B",
                "customer_type": Customer.CustomerType.DISTRIBUTOR,
                "city": "Bengaluru",
                "state": "Karnataka",
                "state_code": "29",
            },
        ]

        for data in definitions:
            matches = Customer.objects.filter(name=data["name"])
            if matches.count() > 1:
                raise CommandError(
                    f"More than one customer named '{data['name']}' exists. "
                    "Resolve duplicates manually."
                )
            if matches.exists():
                customer = matches.get()
                self._validate_fields(
                    f"customer '{data['name']}'",
                    customer,
                    data,
                )
                self._report_record("customer", data["name"], False)
            else:
                Customer.objects.create(**data)
                self._report_record("customer", data["name"], True)

    def _ensure_supplier(self):
        data = {
            "name": "Demo Raw Material Supplier",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "state_code": "33",
        }
        matches = Supplier.objects.filter(name=data["name"])
        if matches.count() > 1:
            raise CommandError(
                f"More than one supplier named '{data['name']}' exists. "
                "Resolve duplicates manually."
            )
        if matches.exists():
            supplier = matches.get()
            self._validate_fields(f"supplier '{data['name']}'", supplier, data)
            self._report_record("supplier", data["name"], False)
        else:
            Supplier.objects.create(**data)
            self._report_record("supplier", data["name"], True)

    def _ensure_product(self, units):
        data = {
            "sku": "SNK-MUR-200",
            "name": "Murukku 200 g",
            "pack_size_value": Decimal("200.000"),
            "pack_size_unit": units["g"],
            "hsn_code": "210690",
            "selling_price": Decimal("50.00"),
            "gst_rate": Decimal("5.00"),
        }
        product, created = Product.objects.get_or_create(
            sku=data["sku"],
            defaults=data,
        )
        expected = {
            "name": data["name"],
            "pack_size_value": data["pack_size_value"],
            "pack_size_unit_id": data["pack_size_unit"].pk,
            "hsn_code": data["hsn_code"],
            "selling_price": data["selling_price"],
            "gst_rate": data["gst_rate"],
        }
        self._validate_fields(f"product '{data['sku']}'", product, expected)
        self._report_record("product", data["sku"], created)
        return product

    def _ensure_raw_materials(self, units):
        definitions = [
            ("RM-FLOUR-001", "Flour", RawMaterial.Category.INGREDIENT, "kg"),
            ("RM-OIL-001", "Cooking oil", RawMaterial.Category.INGREDIENT, "L"),
            ("RM-SPICE-001", "Salt and spice mix", RawMaterial.Category.INGREDIENT, "kg"),
            ("PKG-POUCH-200G", "200 g pouch", RawMaterial.Category.PACKAGING, "piece"),
            ("PKG-CARTON-20", "Carton (20 packets)", RawMaterial.Category.PACKAGING, "piece"),
        ]
        materials = {}

        for sku, name, category, unit_symbol in definitions:
            data = {
                "sku": sku,
                "name": name,
                "category": category,
                "unit": units[unit_symbol],
            }
            material, created = RawMaterial.objects.get_or_create(
                sku=sku,
                defaults=data,
            )
            self._validate_fields(
                f"raw material '{sku}'",
                material,
                {
                    "name": name,
                    "category": category,
                    "unit_id": units[unit_symbol].pk,
                },
            )
            self._report_record("raw material", sku, created)
            materials[sku] = material

        return materials

    def _ensure_bom(self):
        bom, created = BillOfMaterials.objects.get_or_create(
            code="BOM-MURUKKU-001",
            defaults={
                "name": "Murukku 200 g Standard Recipe",
                "version": 1,
                "is_active": True,
                "notes": "Internship benchmark recipe; quantities are for one planned batch.",
            },
        )
        self._validate_fields(
            f"BOM '{bom.code}'",
            bom,
            {"name": "Murukku 200 g Standard Recipe", "version": 1},
        )
        self._report_record("BOM", bom.code, created)
        return bom

    def _ensure_bom_items(self, bom, materials):
        quantities = {
            "RM-FLOUR-001": Decimal("40.000"),
            "RM-OIL-001": Decimal("10.000"),
            "RM-SPICE-001": Decimal("1.200"),
            "PKG-POUCH-200G": Decimal("200.000"),
            "PKG-CARTON-20": Decimal("10.000"),
        }

        existing_skus = set(
            BOMItem.objects.filter(bom=bom).values_list("raw_material__sku", flat=True)
        )
        unexpected = existing_skus - set(quantities)
        if unexpected:
            raise CommandError(
                "The benchmark BOM contains unexpected materials: "
                + ", ".join(sorted(unexpected))
                + ". Review the BOM manually; no items were removed."
            )

        for sku, quantity in quantities.items():
            item, created = BOMItem.objects.get_or_create(
                bom=bom,
                raw_material=materials[sku],
                defaults={"quantity_per_batch": quantity},
            )
            if item.quantity_per_batch != quantity:
                raise CommandError(
                    f"BOM quantity for {sku} is {item.quantity_per_batch}; "
                    f"benchmark expects {quantity}. No existing value was changed."
                )
            self._report_record("BOM item", sku, created)

    def _ensure_recipe(self, product, bom, piece_unit):
        recipe = ProductRecipe.objects.filter(product=product).first()
        if recipe:
            self._validate_fields(
                f"recipe for '{product.sku}'",
                recipe,
                {
                    "bom_id": bom.pk,
                    "expected_output_quantity": Decimal("200.000"),
                    "output_unit_id": piece_unit.pk,
                },
            )
            self._report_record("recipe", product.sku, False)
        else:
            recipe = ProductRecipe.objects.create(
                product=product,
                bom=bom,
                expected_output_quantity=Decimal("200.000"),
                output_unit=piece_unit,
            )
            recipe.full_clean()
            self._report_record("recipe", product.sku, True)

    def _check_opening_stock_conflicts(self, materials, product):
        """Prevent the opening-stock helper from adding a second opening balance."""
        targets = [
            ("raw_material", material)
            for material in materials.values()
        ] + [("product", product)]

        for field, instance in targets:
            movements = StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.OPENING_IN,
                **{field: instance},
            )
            expected = movements.filter(
                reference_type=OPENING_REFERENCE_TYPE,
                reference_number=OPENING_REFERENCE_NUMBER,
            )
            if expected.count() > 1:
                raise CommandError(
                    f"Multiple {OPENING_REFERENCE_NUMBER} opening movements exist for "
                    f"{instance}. Review the stock ledger manually."
                )
            if movements.exclude(
                reference_type=OPENING_REFERENCE_TYPE,
                reference_number=OPENING_REFERENCE_NUMBER,
            ).exists():
                raise CommandError(
                    f"An opening-stock movement with a different reference exists for "
                    f"{instance}. The command will not add another opening balance."
                )

            if (
                not expected.exists()
                and StockMovement.objects.filter(**{field: instance})
                .exclude(movement_type=StockMovement.MovementType.OPENING_IN)
                .exists()
            ):
                raise CommandError(
                    f"Non-opening stock movements already exist for {instance}, but "
                    f"its {OPENING_REFERENCE_NUMBER} opening movement is missing. "
                    "Review the ledger manually before seeding."
                )

    def _validate_fields(self, label, instance, expected_fields):
        mismatches = []
        for field, expected in expected_fields.items():
            actual = getattr(instance, field)
            if actual != expected:
                mismatches.append(f"{field}: found {actual!r}, expected {expected!r}")
        if mismatches:
            raise CommandError(
                f"Existing {label} does not match the benchmark data:\n- "
                + "\n- ".join(mismatches)
                + "\nNo existing record was overwritten."
            )

    def _report_record(self, label, key, created):
        status = "Would create" if created else "Verified existing"
        self.stdout.write(f"{status} {label}: {key}")
