
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase

from masterdata.models import (
    UnitOfMeasure,
    RawMaterial,
    Product,
)

from inventory.models import StockMovement

from production.models import (
    BillOfMaterials,
    BOMItem,
    ProductRecipe,
    ProductionRun,
    ProductionConsumption,
    ProductionRunAuditLog,
)

from production.admin import (
    ProductionRunAdmin,
    ProductionRunAuditLogAdmin,
)

from production.services import post_production_run


User = get_user_model()


class ProductionAuditTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="production_audit_admin",
            email="audit@example.test",
            password="testpassword123",
        )

        self.kg = UnitOfMeasure.objects.create(
            name="Kilogram",
            symbol="kg",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.gram = UnitOfMeasure.objects.create(
            name="Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.piece = UnitOfMeasure.objects.create(
            name="Piece",
            symbol="piece",
            dimension=UnitOfMeasure.Dimension.COUNT,
        )

        self.flour = RawMaterial.objects.create(
            sku="AUDIT-FLOUR-001",
            name="Audit Test Flour",
            category="ingredient",
            unit=self.kg,
        )

        self.product = Product.objects.create(
            sku="AUDIT-MUR-001",
            name="Audit Test Murukku",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            selling_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
        )

        self.bom = BillOfMaterials.objects.create(
            code="AUDIT-BOM-001",
            name="Audit Test BOM",
        )

        BOMItem.objects.create(
            bom=self.bom,
            raw_material=self.flour,
            quantity_per_batch=Decimal("1.000"),
        )

        self.recipe = ProductRecipe.objects.create(
            product=self.product,
            bom=self.bom,
            expected_output_quantity=Decimal("10.000"),
            output_unit=self.piece,
        )

        self.production_admin = ProductionRunAdmin(
            ProductionRun,
            admin.site,
        )

        self.request = SimpleNamespace(user=self.user)

        self.form = SimpleNamespace(
            save_m2m=lambda: None
        )

    def create_run_through_admin(self):
        run = ProductionRun(
            run_number="AUDIT-PROD-001",
            product_recipe=self.recipe,
            batch_count=1,
            production_date=date(2026, 11, 23),
            actual_good_output=Decimal("9.000"),
            status=ProductionRun.Status.DRAFT,
        )
        self.form.instance = run

        self.production_admin.save_model(
            self.request,
            run,
            self.form,
            False,
        )

        self.production_admin.save_related(
            self.request,
            self.form,
            [],
            False,
        )

        return run

    def add_opening_stock(self):
        movement = StockMovement(
            raw_material=self.flour,
            movement_type=StockMovement.MovementType.OPENING_IN,
            quantity=Decimal("10.000"),
            unit=self.kg,
            unit_cost=Decimal("5.0000"),
            reference_type="audit_test",
            reference_number="AUDIT-OPEN-001",
            notes="Test opening stock",
            created_by=self.user,
        )

        movement.full_clean()
        movement.save()

        return movement

    def test_creation_records_audit_history(self):
        run = self.create_run_through_admin()

        log = ProductionRunAuditLog.objects.get(
            production_run=run,
        )

        self.assertEqual(
            log.action,
            ProductionRunAuditLog.Action.CREATED,
        )
        self.assertEqual(log.changed_by, self.user)
        self.assertEqual(log.previous_values, {})
        self.assertEqual(
            log.new_values["run_number"],
            "AUDIT-PROD-001",
        )
        self.assertTrue(log.changed_at)

        self.assertEqual(
            len(log.new_values["consumptions"]),
            1,
        )

    def test_draft_edit_records_previous_and_new_values(self):
        run = self.create_run_through_admin()

        consumption = run.consumptions.get()

        run.notes = "Updated after quality inspection"

        self.production_admin.save_model(
            self.request,
            run,
            self.form,
            True,
        )

        # Simulate the consumption inline saving its edited value.
        ProductionConsumption.objects.filter(
            pk=consumption.pk
        ).update(
            actual_quantity=Decimal("1.250"),
        )

        self.production_admin.save_related(
            self.request,
            self.form,
            [],
            True,
        )

        log = ProductionRunAuditLog.objects.filter(
            production_run=run,
            action=ProductionRunAuditLog.Action.UPDATED,
        ).latest("changed_at")

        self.assertEqual(
            log.previous_values["notes"],
            "",
        )

        self.assertEqual(
            log.new_values["notes"],
            "Updated after quality inspection",
        )

        self.assertEqual(
            log.previous_values["consumptions"][0]["actual_quantity"],
            "1.000",
        )

        self.assertEqual(
            log.new_values["consumptions"][0]["actual_quantity"],
            "1.250",
        )

        self.assertEqual(log.changed_by, self.user)

    def test_posting_records_audit_and_protects_run(self):
        run = self.create_run_through_admin()
        self.add_opening_stock()

        posted_run = post_production_run(
            production_run_id=run.pk,
            user=self.user,
        )

        posted_run.refresh_from_db()

        self.assertEqual(
            posted_run.status,
            ProductionRun.Status.POSTED,
        )

        self.assertIsNotNone(posted_run.posted_at)

        log = ProductionRunAuditLog.objects.filter(
            production_run=run,
            action=ProductionRunAuditLog.Action.POSTED,
        ).get()

        self.assertEqual(log.changed_by, self.user)
        self.assertEqual(
            log.previous_values["status"],
            ProductionRun.Status.DRAFT,
        )
        self.assertEqual(
            log.new_values["status"],
            ProductionRun.Status.POSTED,
        )

        self.assertFalse(
            self.production_admin.has_change_permission(
                self.request,
                posted_run,
            )
        )

        self.assertFalse(
            self.production_admin.has_delete_permission(
                self.request,
                posted_run,
            )
        )

    def test_audit_log_is_read_only_in_admin(self):
        audit_admin = ProductionRunAuditLogAdmin(
            ProductionRunAuditLog,
            admin.site,
        )

        self.assertFalse(
            audit_admin.has_add_permission(self.request)
        )

        self.assertFalse(
            audit_admin.has_change_permission(self.request)
        )

        self.assertFalse(
            audit_admin.has_delete_permission(self.request)
        )
