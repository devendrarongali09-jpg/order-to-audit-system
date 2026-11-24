from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from masterdata.models import Customer, Product, UnitOfMeasure
from sales.models import (
    SalesOrder,
    Invoice,
    InvoiceLine,
    Payment,
    CreditNote,
    CreditNoteLine,
)
from sales.services import issue_credit_note


User = get_user_model()


class CreditNoteServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(
            username="credit_owner",
            password="TestPassword123!",
        )

        owner_group = Group.objects.get_or_create(
            name="Owner"
        )[0]
        self.owner.groups.add(owner_group)

        self.factory_admin = User.objects.create_user(
            username="credit_factory_admin",
            password="TestPassword123!",
        )

        factory_group = Group.objects.get_or_create(
            name="Factory Admin"
        )[0]
        self.factory_admin.groups.add(factory_group)

        self.other_user = User.objects.create_user(
            username="credit_other_user",
            password="TestPassword123!",
        )

        self.gram = UnitOfMeasure.objects.create(
            name="Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.product = Product.objects.create(
            sku="CREDIT-MUR-200",
            name="Murukku 200 g",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            hsn_code="210690",
            selling_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
        )

        self.invoice, self.invoice_line = self.create_invoice()

    def create_invoice(
        self,
        *,
        quantity=150,
        state_code="33",
        suffix="001",
    ):
        interstate = state_code != "33"

        customer = Customer.objects.create(
            name=f"Credit Test Customer {suffix}",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai" if not interstate else "Bengaluru",
            state="Tamil Nadu" if not interstate else "Karnataka",
            state_code=state_code,
        )

        order = SalesOrder.objects.create(
            order_number=f"SO-CREDIT-{suffix}",
            customer=customer,
            created_by=self.owner,
            order_date=date(2026, 11, 23),
            status=SalesOrder.Status.INVOICED,
        )

        taxable = Decimal(quantity) * Decimal("50.00")
        tax = (taxable * Decimal("5.00") / Decimal("100")).quantize(
            Decimal("0.01")
        )

        if interstate:
            cgst = Decimal("0.00")
            sgst = Decimal("0.00")
            igst = tax
        else:
            cgst = (tax / Decimal("2")).quantize(Decimal("0.01"))
            sgst = tax - cgst
            igst = Decimal("0.00")

        grand_total = taxable + cgst + sgst + igst

        invoice = Invoice.objects.create(
            invoice_number=f"INV-CREDIT-{suffix}",
            order=order,
            invoice_date=date(2026, 11, 23),
            seller_name="Demo Food Manufacturer",
            seller_gstin="DEMO-SELLER-001",
            seller_state_code="33",
            customer_name=customer.name,
            customer_gstin="",
            customer_address="",
            place_of_supply=customer.state,
            place_of_supply_state_code=state_code,
            taxable_value=taxable,
            cgst_total=cgst,
            sgst_total=sgst,
            igst_total=igst,
            grand_total=grand_total,
            issued_by=self.owner,
            status=Invoice.Status.ISSUED,
        )

        invoice_line = InvoiceLine.objects.create(
            invoice=invoice,
            product=self.product,
            product_name="Murukku 200 g",
            hsn_code="210690",
            quantity=quantity,
            unit_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
            taxable_value=taxable,
            cgst_amount=cgst,
            sgst_amount=sgst,
            igst_amount=igst,
            total_amount=grand_total,
        )

        return invoice, invoice_line

    def issue(self, lines, *, user=None, invoice=None, note_date=None,
              reason="Goods returned by customer"):
        return issue_credit_note(
            invoice_id=(invoice or self.invoice).pk,
            user=user or self.owner,
            reason=reason,
            lines=lines,
            credit_note_date=note_date or date(2026, 11, 24),
        )

    def test_partial_intrastate_credit_calculates_cgst_and_sgst(self):
        credit = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 50,
            }
        ])

        self.assertEqual(credit.credit_note_number, "CN/2026-27/0001")
        self.assertEqual(credit.taxable_value, Decimal("2500.00"))
        self.assertEqual(credit.cgst_total, Decimal("62.50"))
        self.assertEqual(credit.sgst_total, Decimal("62.50"))
        self.assertEqual(credit.igst_total, Decimal("0.00"))
        self.assertEqual(credit.grand_total, Decimal("2625.00"))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_credited, Decimal("2625.00"))
        self.assertEqual(self.invoice.net_total, Decimal("5250.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("5250.00"))

    def test_credit_note_preserves_payments_and_calculates_balance(self):
        payment = Payment.objects.create(
            invoice=self.invoice,
            payment_date=date(2026, 11, 23),
            amount=Decimal("4000.00"),
            method=Payment.Method.UPI,
            received_by=self.owner,
            status=Payment.Status.RECEIVED,
        )

        self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 50,
            }
        ])

        self.invoice.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.Status.RECEIVED)
        self.assertEqual(payment.amount, Decimal("4000.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("4000.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("1250.00"))
        self.assertEqual(self.invoice.customer_credit, Decimal("0.00"))

    def test_full_credit_note_does_not_delete_or_reverse_payment(self):
        payment = Payment.objects.create(
            invoice=self.invoice,
            payment_date=date(2026, 11, 23),
            amount=Decimal("4000.00"),
            method=Payment.Method.UPI,
            received_by=self.owner,
            status=Payment.Status.RECEIVED,
        )

        credit = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 150,
            }
        ])

        self.invoice.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(credit.grand_total, Decimal("7875.00"))
        self.assertEqual(self.invoice.net_total, Decimal("0.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("4000.00"))
        self.assertEqual(self.invoice.customer_credit, Decimal("4000.00"))
        self.assertEqual(self.invoice.payment_status, "Customer Credit")
        self.assertEqual(payment.status, Payment.Status.RECEIVED)
        self.assertEqual(self.invoice.payments.count(), 1)

    def test_interstate_credit_uses_igst(self):
        invoice, invoice_line = self.create_invoice(
            quantity=120,
            state_code="29",
            suffix="002",
        )

        credit = self.issue(
            [
                {
                    "invoice_line_id": invoice_line.pk,
                    "quantity": 120,
                }
            ],
            invoice=invoice,
        )

        self.assertEqual(credit.taxable_value, Decimal("6000.00"))
        self.assertEqual(credit.cgst_total, Decimal("0.00"))
        self.assertEqual(credit.sgst_total, Decimal("0.00"))
        self.assertEqual(credit.igst_total, Decimal("300.00"))
        self.assertEqual(credit.grand_total, Decimal("6300.00"))

    def test_factory_admin_can_issue_credit_note(self):
        credit = self.issue(
            [
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 1,
                }
            ],
            user=self.factory_admin,
        )

        self.assertEqual(credit.created_by, self.factory_admin)

    def test_non_authorized_user_cannot_issue_credit_note(self):
        with self.assertRaises(PermissionDenied):
            self.issue(
                [
                    {
                        "invoice_line_id": self.invoice_line.pk,
                        "quantity": 1,
                    }
                ],
                user=self.other_user,
            )

        self.assertEqual(CreditNote.objects.count(), 0)

    def test_anonymous_user_cannot_issue_credit_note(self):
        with self.assertRaises(PermissionDenied):
            self.issue(
                [
                    {
                        "invoice_line_id": self.invoice_line.pk,
                        "quantity": 1,
                    }
                ],
                user=AnonymousUser(),
            )

    def test_reason_is_required(self):
        with self.assertRaises(ValidationError):
            self.issue(
                [
                    {
                        "invoice_line_id": self.invoice_line.pk,
                        "quantity": 1,
                    }
                ],
                reason="   ",
            )

        self.assertEqual(CreditNote.objects.count(), 0)

    def test_future_credit_note_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue(
                [
                    {
                        "invoice_line_id": self.invoice_line.pk,
                        "quantity": 1,
                    }
                ],
                note_date=date(2026, 11, 25),
            )

    def test_credit_note_date_before_invoice_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue(
                [
                    {
                        "invoice_line_id": self.invoice_line.pk,
                        "quantity": 1,
                    }
                ],
                note_date=date(2026, 11, 22),
            )

    def test_credit_quantity_cannot_exceed_remaining_quantity(self):
        self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 100,
            }
        ])

        with self.assertRaises(ValidationError):
            self.issue([
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 51,
                }
            ])

        second_credit = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 50,
            }
        ])

        self.assertEqual(
            second_credit.credit_note_number,
            "CN/2026-27/0002",
        )

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_credited, Decimal("7875.00"))

    def test_duplicate_invoice_line_in_one_credit_note_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue([
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 1,
                },
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 1,
                },
            ])

        self.assertEqual(CreditNote.objects.count(), 0)

    def test_fractional_credit_quantity_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue([
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": "1.5",
                }
            ])

    def test_zero_credit_quantity_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue([
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 0,
                }
            ])

    def test_invoice_line_from_another_invoice_is_rejected(self):
        other_invoice, other_line = self.create_invoice(
            quantity=10,
            suffix="003",
        )

        with self.assertRaises(ValidationError):
            self.issue([
                {
                    "invoice_line_id": other_line.pk,
                    "quantity": 1,
                }
            ])

        self.assertEqual(
            CreditNote.objects.filter(invoice=self.invoice).count(),
            0,
        )

    def test_credit_note_uses_original_invoice_line_snapshots(self):
        self.product.hsn_code = "UPDATED"
        self.product.gst_rate = Decimal("12.00")
        self.product.save()

        credit = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 1,
            }
        ])

        credit_line = credit.lines.get()

        self.assertEqual(credit_line.hsn_code, "210690")
        self.assertEqual(credit_line.gst_rate, Decimal("5.00"))
        self.assertEqual(credit_line.product_name, "Murukku 200 g")

    def test_credit_note_number_increments_within_financial_year(self):
        first = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 1,
            }
        ])

        second = self.issue([
            {
                "invoice_line_id": self.invoice_line.pk,
                "quantity": 1,
            }
        ])

        self.assertEqual(first.credit_note_number, "CN/2026-27/0001")
        self.assertEqual(second.credit_note_number, "CN/2026-27/0002")
