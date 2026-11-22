from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from masterdata.models import Customer
from sales.models import SalesOrder, Invoice, Payment
from sales.services import reverse_payment


User = get_user_model()


class PaymentReversalTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(
            username="payment_owner",
            password="TestPassword123!"
        )

        owner_group = Group.objects.get_or_create(
            name="Owner"
        )[0]
        self.owner.groups.add(owner_group)

        self.other_user = User.objects.create_user(
            username="payment_user",
            password="TestPassword123!"
        )

        self.customer = Customer.objects.create(
            name="Payment Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        self.order = SalesOrder.objects.create(
            order_number="SO-PAY-001",
            customer=self.customer,
            created_by=self.owner,
            order_date=date(2026, 11, 22),
            status=SalesOrder.Status.INVOICED,
        )

        self.invoice = Invoice.objects.create(
            invoice_number="INV-PAY-001",
            order=self.order,
            invoice_date=date(2026, 11, 22),
            customer_name=self.customer.name,
            place_of_supply="Tamil Nadu",
            place_of_supply_state_code="33",
            taxable_value=Decimal("7500.00"),
            grand_total=Decimal("7875.00"),
            issued_by=self.owner,
            status=Invoice.Status.ISSUED,
        )

    def create_payment(self, amount):
        return Payment.objects.create(
            invoice=self.invoice,
            payment_date=date(2026, 11, 22),
            amount=Decimal(amount),
            method=Payment.Method.UPI,
            received_by=self.owner,
            status=Payment.Status.RECEIVED,
        )

    def test_owner_can_reverse_received_payment(self):
        payment = self.create_payment("3875.00")

        reversed_payment = reverse_payment(
            payment_id=payment.pk,
            user=self.owner,
            reason="Incorrect demonstration payment",
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            Payment.Status.REVERSED,
        )
        self.assertEqual(payment.reversed_by, self.owner)
        self.assertIsNotNone(payment.reversed_at)
        self.assertEqual(
            payment.reversal_reason,
            "Incorrect demonstration payment",
        )
        self.assertEqual(
            self.invoice.payments.count(),
            1,
        )

    def test_reversal_restores_invoice_balance(self):
        self.create_payment("4000.00")
        payment = self.create_payment("3875.00")

        reverse_payment(
            payment_id=payment.pk,
            user=self.owner,
            reason="Incorrect duplicate entry",
        )

        self.invoice.refresh_from_db()

        self.assertEqual(
            self.invoice.amount_paid,
            Decimal("4000.00"),
        )
        self.assertEqual(
            self.invoice.balance_due,
            Decimal("3875.00"),
        )

    def test_non_owner_cannot_reverse_payment(self):
        payment = self.create_payment("1000.00")

        with self.assertRaises(PermissionDenied):
            reverse_payment(
                payment_id=payment.pk,
                user=self.other_user,
                reason="Test reversal",
            )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            Payment.Status.RECEIVED,
        )

    def test_anonymous_user_cannot_reverse_payment(self):
        payment = self.create_payment("1000.00")

        with self.assertRaises(PermissionDenied):
            reverse_payment(
                payment_id=payment.pk,
                user=AnonymousUser(),
                reason="Test reversal",
            )

    def test_reversal_requires_reason(self):
        payment = self.create_payment("1000.00")

        with self.assertRaises(ValidationError):
            reverse_payment(
                payment_id=payment.pk,
                user=self.owner,
                reason="   ",
            )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            Payment.Status.RECEIVED,
        )

    def test_payment_cannot_be_reversed_twice(self):
        payment = self.create_payment("1000.00")

        reverse_payment(
            payment_id=payment.pk,
            user=self.owner,
            reason="First reversal",
        )

        with self.assertRaises(ValidationError):
            reverse_payment(
                payment_id=payment.pk,
                user=self.owner,
                reason="Second reversal attempt",
            )

        payment.refresh_from_db()

        self.assertEqual(
            payment.reversal_reason,
            "First reversal",
        )
