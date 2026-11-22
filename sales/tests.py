
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from masterdata.models import (
    Customer,
    Product,
    UnitOfMeasure,
)
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from inventory.models import StockMovement
from inventory.services import get_stock_balance

from sales.models import (
    SalesOrder,
    SalesOrderItem,
    Invoice,
    Dispatch,
    DispatchItem,
)

from sales.services import post_dispatch


User = get_user_model()


class PostDispatchTests(TestCase):

    def setUp(self):

        # Create test user.
        self.user = User.objects.create_user(
            username="dispatch_tester",
            password="testpassword123",
        )

        # Product packaging unit.
        self.gram = UnitOfMeasure.objects.create(
            name="Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        # Finished-goods stock unit.
        self.piece = UnitOfMeasure.objects.create(
            name="Piece",
            symbol="piece",
            dimension=UnitOfMeasure.Dimension.COUNT,
        )

        # Customer.
        self.customer = Customer.objects.create(
            name="Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

    def create_product(self, sku="TEST-001"):

        return Product.objects.create(
            sku=sku,
            name=f"Test Product {sku}",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            selling_price=Decimal("50.00"),
        )

    def add_stock(self, product, quantity):

        return StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.OPENING_IN,
            quantity=Decimal(str(quantity)),
            unit=self.piece,
            unit_cost=None,
            reference_type="opening",
            reference_number=f"OPEN-{product.sku}",
            notes="Automated test opening stock",
            created_by=self.user,
        )

    def create_invoiced_order(
        self,
        products_and_quantities,
        order_number="SO-TEST-001",
    ):

        order = SalesOrder.objects.create(
            order_number=order_number,
            customer=self.customer,
            created_by=self.user,
            order_date=date(2026, 11, 22),
            status=SalesOrder.Status.INVOICED,
        )

        order_items = []

        taxable_value = Decimal("0.00")

        for product, quantity in products_and_quantities:

            item = SalesOrderItem.objects.create(
                order=order,
                product=product,
                quantity=quantity,
                unit_price=Decimal("50.00"),
            )

            order_items.append(item)

            taxable_value += (
                Decimal(quantity) * Decimal("50.00")
            )

        Invoice.objects.create(
            invoice_number=f"INV-{order_number}",
            order=order,
            invoice_date=date(2026, 11, 22),
            customer_name=self.customer.name,
            place_of_supply="Tamil Nadu",
            place_of_supply_state_code="33",
            taxable_value=taxable_value,
            grand_total=(
                taxable_value * Decimal("1.05")
            ).quantize(Decimal("0.01")),
            issued_by=self.user,
            status=Invoice.Status.ISSUED,
        )

        return order, order_items

    def create_dispatch(
        self,
        order,
        order_items_and_quantities,
        dispatch_number="DSP-TEST-001",
    ):

        dispatch = Dispatch.objects.create(
            dispatch_number=dispatch_number,
            sales_order=order,
            dispatch_date=date(2026, 11, 22),
            created_by=self.user,
        )

        for order_item, quantity in order_items_and_quantities:

            DispatchItem.objects.create(
                dispatch=dispatch,
                order_item=order_item,
                quantity=quantity,
            )

        return dispatch

    # TEST 1:
    # Successful dispatch creates stock movement.

    def test_successful_dispatch_reduces_stock(self):

        product = self.create_product()

        self.add_stock(product, 20)

        order, order_items = self.create_invoiced_order(
            [(product, 10)]
        )

        dispatch = self.create_dispatch(
            order,
            [(order_items[0], 10)],
        )

        posted = post_dispatch(
            dispatch_id=dispatch.pk,
            user=self.user,
        )

        dispatch.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            posted.status,
            Dispatch.Status.POSTED,
        )

        self.assertEqual(
            dispatch.posted_by,
            self.user,
        )

        self.assertIsNotNone(dispatch.posted_at)

        self.assertEqual(
            get_stock_balance(product=product),
            Decimal("10.000"),
        )

        self.assertEqual(
            order.status,
            SalesOrder.Status.DISPATCHED,
        )

        self.assertEqual(
            StockMovement.objects.filter(
                product=product,
                movement_type=StockMovement.MovementType.DISPATCH,
                reference_number=dispatch.dispatch_number,
            ).count(),
            1,
        )

    # TEST 2:
    # Insufficient stock must reject dispatch.

    def test_insufficient_stock_rejects_dispatch(self):

        product = self.create_product()

        self.add_stock(product, 5)

        order, order_items = self.create_invoiced_order(
            [(product, 10)]
        )

        dispatch = self.create_dispatch(
            order,
            [(order_items[0], 6)],
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Insufficient stock",
        ):

            post_dispatch(
                dispatch_id=dispatch.pk,
                user=self.user,
            )

        dispatch.refresh_from_db()

        self.assertEqual(
            dispatch.status,
            Dispatch.Status.DRAFT,
        )

        self.assertIsNone(dispatch.posted_at)

        self.assertEqual(
            get_stock_balance(product=product),
            Decimal("5.000"),
        )

        self.assertFalse(
            StockMovement.objects.filter(
                reference_number=dispatch.dispatch_number,
                movement_type=StockMovement.MovementType.DISPATCH,
            ).exists()
        )

    # TEST 3:
    # Posting the same dispatch twice must not deduct twice.

    def test_duplicate_posting_does_not_deduct_stock_twice(self):

        product = self.create_product()

        self.add_stock(product, 20)

        order, order_items = self.create_invoiced_order(
            [(product, 10)]
        )

        dispatch = self.create_dispatch(
            order,
            [(order_items[0], 10)],
        )

        post_dispatch(
            dispatch_id=dispatch.pk,
            user=self.user,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Only draft dispatches can be posted.",
        ):

            post_dispatch(
                dispatch_id=dispatch.pk,
                user=self.user,
            )

        self.assertEqual(
            get_stock_balance(product=product),
            Decimal("10.000"),
        )

        self.assertEqual(
            StockMovement.objects.filter(
                reference_number=dispatch.dispatch_number,
                movement_type=StockMovement.MovementType.DISPATCH,
            ).count(),
            1,
        )

    # TEST 4:
    # Previously dispatched quantities cannot exceed the order.

    def test_partial_dispatch_cannot_exceed_order_quantity(self):

        product = self.create_product()

        self.add_stock(product, 20)

        order, order_items = self.create_invoiced_order(
            [(product, 10)]
        )

        # First dispatch: 7 packets already posted.
        previous_dispatch = self.create_dispatch(
            order,
            [(order_items[0], 7)],
            dispatch_number="DSP-PREVIOUS",
        )

        previous_dispatch.status = Dispatch.Status.POSTED
        previous_dispatch.posted_by = self.user
        previous_dispatch.save()

        self.add_dispatch_stock_movement(
            product,
            7,
            "DSP-PREVIOUS",
        )

        # Second dispatch requests another 4 packets.
        # Total would become 11, while the order permits only 10.

        new_dispatch = self.create_dispatch(
            order,
            [(order_items[0], 4)],
            dispatch_number="DSP-EXCESS",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "exceeds the remaining ordered quantity",
        ):

            post_dispatch(
                dispatch_id=new_dispatch.pk,
                user=self.user,
            )

        self.assertEqual(
            get_stock_balance(product=product),
            Decimal("13.000"),
        )

        new_dispatch.refresh_from_db()

        self.assertEqual(
            new_dispatch.status,
            Dispatch.Status.DRAFT,
        )

    def add_dispatch_stock_movement(
        self,
        product,
        quantity,
        reference_number,
    ):

        return StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.DISPATCH,
            quantity=Decimal(str(quantity)),
            unit=self.piece,
            unit_cost=None,
            reference_type="dispatch",
            reference_number=reference_number,
            notes="Previous test dispatch",
            created_by=self.user,
        )

    # TEST 5:
    # A database constraint prevents duplicate rows
    # for the same order item in one dispatch.

    def test_duplicate_order_item_in_dispatch_is_rejected(self):

        product = self.create_product()

        order, order_items = self.create_invoiced_order(
            [(product, 10)]
        )

        dispatch = self.create_dispatch(
            order,
            [(order_items[0], 2)],
        )

        with self.assertRaises(IntegrityError):

            with transaction.atomic():

                DispatchItem.objects.create(
                    dispatch=dispatch,
                    order_item=order_items[0],
                    quantity=3,
                )

    # TEST 6:
    # Failure while creating a later movement must roll
    # back earlier movements in the same transaction.

    def test_transaction_rolls_back_partial_stock_movements(self):

        product_one = self.create_product("TEST-001")
        product_two = self.create_product("TEST-002")

        self.add_stock(product_one, 10)
        self.add_stock(product_two, 10)

        order, order_items = self.create_invoiced_order(
            [
                (product_one, 4),
                (product_two, 4),
            ],
            order_number="SO-ROLLBACK",
        )

        dispatch = self.create_dispatch(
            order,
            [
                (order_items[0], 2),
                (order_items[1], 2),
            ],
            dispatch_number="DSP-ROLLBACK",
        )

        original_save = StockMovement.save

        call_count = 0

        def fail_on_second_save(instance, *args, **kwargs):

            nonlocal call_count

            call_count += 1

            if call_count == 2:

                raise ValidationError(
                    "Simulated second movement failure."
                )

            return original_save(
                instance,
                *args,
                **kwargs,
            )

        with patch.object(
            StockMovement,
            "save",
            new=fail_on_second_save,
        ):

            with self.assertRaisesMessage(
                ValidationError,
                "Simulated second movement failure.",
            ):

                post_dispatch(
                    dispatch_id=dispatch.pk,
                    user=self.user,
                )

        dispatch.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            dispatch.status,
            Dispatch.Status.DRAFT,
        )

        self.assertIsNone(dispatch.posted_at)

        self.assertEqual(
            order.status,
            SalesOrder.Status.INVOICED,
        )

        self.assertEqual(
            get_stock_balance(product=product_one),
            Decimal("10.000"),
        )

        self.assertEqual(
            get_stock_balance(product=product_two),
            Decimal("10.000"),
        )

        self.assertFalse(
            StockMovement.objects.filter(
                reference_number=dispatch.dispatch_number,
                movement_type=StockMovement.MovementType.DISPATCH,
            ).exists()
        )

@skipUnlessDBFeature("has_select_for_update")
class PostDispatchConcurrencyTests(TransactionTestCase):

    def setUp(self):

        self.user = User.objects.create_user(
            username="concurrency_tester",
            password="testpassword123",
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
            name="Concurrency Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        self.product = Product.objects.create(
            sku="CONC-001",
            name="Concurrency Test Murukku",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            selling_price=Decimal("50.00"),
        )

        # Initial stock: 10 packets.
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.MovementType.OPENING_IN,
            quantity=Decimal("10"),
            unit=self.piece,
            unit_cost=None,
            reference_type="opening",
            reference_number="CONC-OPENING",
            notes="Concurrency test opening stock",
            created_by=self.user,
        )

        self.dispatches = []

        # Create two separately invoiced orders and dispatches.
        for number in (1, 2):

            order = SalesOrder.objects.create(
                order_number=f"CONC-SO-{number}",
                customer=self.customer,
                created_by=self.user,
                order_date=date(2026, 11, 22),
                status=SalesOrder.Status.INVOICED,
            )

            order_item = SalesOrderItem.objects.create(
                order=order,
                product=self.product,
                quantity=8,
                unit_price=Decimal("50.00"),
            )

            Invoice.objects.create(
                invoice_number=f"CONC-INV-{number}",
                order=order,
                invoice_date=date(2026, 11, 22),
                customer_name=self.customer.name,
                place_of_supply="Tamil Nadu",
                place_of_supply_state_code="33",
                taxable_value=Decimal("400.00"),
                grand_total=Decimal("420.00"),
                issued_by=self.user,
                status=Invoice.Status.ISSUED,
            )

            dispatch = Dispatch.objects.create(
                dispatch_number=f"CONC-DSP-{number}",
                sales_order=order,
                dispatch_date=date(2026, 11, 22),
                created_by=self.user,
            )

            DispatchItem.objects.create(
                dispatch=dispatch,
                order_item=order_item,
                quantity=8,
            )

            self.dispatches.append(dispatch)

    def test_simultaneous_dispatches_do_not_oversell_stock(self):

        barrier = Barrier(2)
        user_id = self.user.pk

        def attempt_dispatch(dispatch_id):

            # Each worker must use its own database connection.
            connections.close_all()

            try:

                worker_user = User.objects.get(pk=user_id)

                # Start both dispatch attempts together.
                barrier.wait(timeout=15)

                try:

                    post_dispatch(
                        dispatch_id=dispatch_id,
                        user=worker_user,
                    )

                    return "posted"

                except ValidationError:
                    return "rejected"

            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(
                    attempt_dispatch,
                    dispatch.pk,
                )
                for dispatch in self.dispatches
            ]

            results = [
                future.result(timeout=30)
                for future in futures
            ]

        # Exactly one dispatch must succeed.
        self.assertCountEqual(
            results,
            ["posted", "rejected"],
        )

        for dispatch in self.dispatches:
            dispatch.refresh_from_db()

        posted_count = Dispatch.objects.filter(
            dispatch_number__in=[
                "CONC-DSP-1",
                "CONC-DSP-2",
            ],
            status=Dispatch.Status.POSTED,
        ).count()

        self.assertEqual(posted_count, 1)

        # One successful dispatch consumes 8 packets.
        self.assertEqual(
            get_stock_balance(product=self.product),
            Decimal("2.000"),
        )

        # Exactly one outgoing movement should exist.
        movements = StockMovement.objects.filter(
            product=self.product,
            movement_type=StockMovement.MovementType.DISPATCH,
            reference_number__in=[
                "CONC-DSP-1",
                "CONC-DSP-2",
            ],
        )

        self.assertEqual(movements.count(), 1)

        self.assertEqual(
            movements.get().quantity,
            Decimal("8.000"),
        )
