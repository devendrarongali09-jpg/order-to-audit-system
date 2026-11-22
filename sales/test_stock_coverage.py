from datetime import date
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase

from inventory.models import StockMovement
from masterdata.models import Customer, Product, UnitOfMeasure
from sales.admin import SalesOrderAdmin
from sales.models import (
    SalesOrder,
    SalesOrderItem,
    Dispatch,
    DispatchItem,
)


User = get_user_model()


class StockCoverageAdminTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="stock_coverage_tester",
            password="TestPassword123!",
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

        self.customer = Customer.objects.create(
            name="Stock Coverage Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        self.product = Product.objects.create(
            sku="COVERAGE-001",
            name="Test Murukku",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            selling_price=Decimal("50.00"),
        )

        self.admin = SalesOrderAdmin(
            SalesOrder,
            AdminSite(),
        )

    def add_stock(self, quantity, reference):
        return StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.MovementType.OPENING_IN,
            quantity=Decimal(str(quantity)),
            unit=self.piece,
            unit_cost=None,
            reference_type="opening",
            reference_number=reference,
            notes="Stock coverage test",
            created_by=self.user,
        )

    def create_order(self, quantity, order_number):
        order = SalesOrder.objects.create(
            order_number=order_number,
            customer=self.customer,
            created_by=self.user,
            order_date=date(2026, 11, 22),
            status=SalesOrder.Status.INVOICED,
        )

        item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=quantity,
            unit_price=Decimal("50.00"),
        )

        return order, item

    def add_posted_dispatch(self, order, item, quantity):
        dispatch = Dispatch.objects.create(
            dispatch_number=f"DSP-{order.order_number}",
            sales_order=order,
            dispatch_date=date(2026, 11, 22),
            status=Dispatch.Status.POSTED,
            created_by=self.user,
        )

        DispatchItem.objects.create(
            dispatch=dispatch,
            order_item=item,
            quantity=quantity,
        )

        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.MovementType.DISPATCH,
            quantity=Decimal(str(quantity)),
            unit=self.piece,
            unit_cost=None,
            reference_type="dispatch",
            reference_number=dispatch.dispatch_number,
            notes="Posted test dispatch",
            created_by=self.user,
        )

        return dispatch

    def test_insufficient_stock_displays_shortage(self):
        self.add_stock(88, "OPEN-SHORTAGE")

        order, item = self.create_order(
            120,
            "SO-COVERAGE-001",
        )

        result = str(self.admin.stock_coverage(order))

        self.assertIn("Shortage", result)
        self.assertIn("32", result)

    def test_sufficient_stock_displays_covered(self):
        self.add_stock(120, "OPEN-COVERED")

        order, item = self.create_order(
            120,
            "SO-COVERAGE-002",
        )

        result = str(self.admin.stock_coverage(order))

        self.assertEqual(result, "Stock covered")

    def test_fully_dispatched_order_displays_completed(self):
        self.add_stock(238, "OPEN-FULL")

        order, item = self.create_order(
            150,
            "SO-COVERAGE-003",
        )

        self.add_posted_dispatch(
            order,
            item,
            150,
        )

        result = str(self.admin.stock_coverage(order))

        self.assertEqual(result, "Fully dispatched")

    def test_partial_dispatch_checks_remaining_quantity(self):
        self.add_stock(238, "OPEN-PARTIAL")

        order, item = self.create_order(
            150,
            "SO-COVERAGE-004",
        )

        self.add_posted_dispatch(
            order,
            item,
            100,
        )

        result = str(self.admin.stock_coverage(order))
        details = str(
            self.admin.stock_coverage_details(order)
        )

        self.assertEqual(result, "Stock covered")
        self.assertIn("Remaining: 50", details)
        self.assertIn("Available: 138", details)
        self.assertIn("Shortage: 0", details)