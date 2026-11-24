from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from masterdata.models import Customer, Product, UnitOfMeasure
from sales.models import (
    SalesOrder,
    SalesOrderItem,
    Invoice,
    InvoiceAuditLog,
    Payment,
)
from sales.services import generate_invoice, issue_credit_note


User = get_user_model()


class InvoiceAuditTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="invoice_audit_owner",
            password="TestPassword123!",
        )

        group, _ = Group.objects.get_or_create(name="Owner")
        self.user.groups.add(group)

        self.gram = UnitOfMeasure.objects.create(
            name="Audit Test Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.product = Product.objects.create(
            sku="AUDIT-MUR-200",
            name="Murukku 200 g",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            hsn_code="210690",
            selling_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
        )

        self.customer = Customer.objects.create(
            name="Invoice Audit Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        self.order = SalesOrder.objects.create(
            order_number="SO-AUDIT-001",
            customer=self.customer,
            created_by=self.user,
            order_date=timezone.localdate(),
            status=SalesOrder.Status.APPROVED,
        )

        SalesOrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=2,
            unit_price=Decimal("50.00"),
        )

        self.invoice = generate_invoice(
            order_id=self.order.pk,
            user=self.user,
        )

        self.invoice_line = self.invoice.lines.get()

    def issue_credit(self, quantity):
        return issue_credit_note(
            invoice_id=self.invoice.pk,
            user=self.user,
            reason="Customer returned goods",
            lines=[
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": quantity,
                }
            ],
            credit_note_date=timezone.localdate(),
        )

    def test_invoice_generation_creates_issued_audit_log(self):
        log = InvoiceAuditLog.objects.get(
            invoice=self.invoice,
            action=InvoiceAuditLog.Action.ISSUED,
        )

        self.assertEqual(log.changed_by, self.user)
        self.assertEqual(
            log.new_values["invoice_number"],
            self.invoice.invoice_number,
        )
        self.assertEqual(
            log.new_values["status"],
            Invoice.Status.ISSUED,
        )
        self.assertEqual(len(log.new_values["lines"]), 1)

    def test_partial_credit_does_not_cancel_invoice(self):
        credit = self.issue_credit(1)

        self.invoice.refresh_from_db()

        self.assertEqual(
            self.invoice.status,
            Invoice.Status.ISSUED,
        )

        self.assertIsNone(self.invoice.cancelled_at)

        self.assertEqual(
            InvoiceAuditLog.objects.filter(
                invoice=self.invoice,
                action=InvoiceAuditLog.Action.CANCELLED,
            ).count(),
            0,
        )

        self.assertEqual(credit.grand_total, Decimal("52.50"))

    def test_full_credit_cancels_invoice_and_creates_audit_log(self):
        payment = Payment.objects.create(
            invoice=self.invoice,
            payment_date=timezone.localdate(),
            amount=Decimal("25.00"),
            method=Payment.Method.UPI,
            received_by=self.user,
            status=Payment.Status.RECEIVED,
        )

        credit = self.issue_credit(2)

        self.invoice.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(
            self.invoice.status,
            Invoice.Status.CANCELLED,
        )

        self.assertIsNotNone(self.invoice.cancelled_at)

        self.assertIn(
            credit.credit_note_number,
            self.invoice.cancellation_reason,
        )

        self.assertEqual(
            self.invoice.balance_due,
            Decimal("0.00"),
        )

        self.assertEqual(
            payment.status,
            Payment.Status.RECEIVED,
        )

        logs = InvoiceAuditLog.objects.filter(
            invoice=self.invoice
        ).order_by("id")

        self.assertEqual(logs.count(), 2)

        cancellation_log = logs.last()

        self.assertEqual(
            cancellation_log.action,
            InvoiceAuditLog.Action.CANCELLED,
        )

        self.assertEqual(
            cancellation_log.changed_by,
            self.user,
        )

        self.assertEqual(
            cancellation_log.previous_values["status"],
            Invoice.Status.ISSUED,
        )

        self.assertEqual(
            cancellation_log.new_values["status"],
            Invoice.Status.CANCELLED,
        )
