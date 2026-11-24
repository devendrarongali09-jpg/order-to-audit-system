from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from masterdata.models import Customer, Product, UnitOfMeasure
from sales.models import (
    SalesOrder,
    Invoice,
    InvoiceLine,
    CreditNote,
)
from sales.services import issue_credit_note


User = get_user_model()


class CreditNoteViewTests(TestCase):

    def setUp(self):
        self.owner = self.create_user_with_group(
            "view_credit_owner",
            "Owner",
        )

        self.factory_admin = self.create_user_with_group(
            "view_credit_factory",
            "Factory Admin",
        )

        self.accountant = self.create_user_with_group(
            "view_credit_accountant",
            "Accountant/Auditor",
        )

        self.outsider = User.objects.create_user(
            username="view_credit_outsider",
            password="TestPassword123!",
        )

        self.gram = UnitOfMeasure.objects.create(
            name="Credit View Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.product = Product.objects.create(
            sku="VIEW-CREDIT-200",
            name="Murukku 200 g",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            hsn_code="210690",
            selling_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
        )

        self.customer = Customer.objects.create(
            name="Credit View Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        self.order = SalesOrder.objects.create(
            order_number="SO-CN-VIEW-001",
            customer=self.customer,
            created_by=self.owner,
            order_date=date(2026, 11, 23),
            status=SalesOrder.Status.INVOICED,
        )

        self.invoice = Invoice.objects.create(
            invoice_number="INV-CN-VIEW-001",
            order=self.order,
            invoice_date=date(2026, 11, 23),
            seller_name="Demo Food Manufacturer",
            seller_gstin="DEMO-SELLER-001",
            seller_state_code="33",
            customer_name=self.customer.name,
            customer_gstin="",
            customer_address="",
            place_of_supply="Tamil Nadu",
            place_of_supply_state_code="33",
            taxable_value=Decimal("7500.00"),
            cgst_total=Decimal("187.50"),
            sgst_total=Decimal("187.50"),
            igst_total=Decimal("0.00"),
            grand_total=Decimal("7875.00"),
            issued_by=self.owner,
            status=Invoice.Status.ISSUED,
        )

        self.invoice_line = InvoiceLine.objects.create(
            invoice=self.invoice,
            product=self.product,
            product_name="Murukku 200 g",
            hsn_code="210690",
            quantity=150,
            unit_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
            taxable_value=Decimal("7500.00"),
            cgst_amount=Decimal("187.50"),
            sgst_amount=Decimal("187.50"),
            igst_amount=Decimal("0.00"),
            total_amount=Decimal("7875.00"),
        )

        self.list_url = reverse("sales:credit_note_list")

        self.create_url = reverse(
            "sales:credit_note_create",
            args=[self.invoice.pk],
        )

    def create_user_with_group(self, username, group_name):
        user = User.objects.create_user(
            username=username,
            password="TestPassword123!",
        )

        group, _created = Group.objects.get_or_create(
            name=group_name
        )

        user.groups.add(group)
        return user

    def create_credit_note(self):
        return issue_credit_note(
            invoice_id=self.invoice.pk,
            user=self.owner,
            reason="Customer returned goods",
            lines=[
                {
                    "invoice_line_id": self.invoice_line.pk,
                    "quantity": 10,
                }
            ],
            credit_note_date=date(2026, 11, 24),
        )

    def test_owner_can_open_credit_note_register(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_issue"])
        self.assertContains(response, self.invoice.invoice_number)

    def test_factory_admin_can_open_credit_note_register(self):
        self.client.force_login(self.factory_admin)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_issue"])

    def test_accountant_can_view_register_but_cannot_issue(self):
        self.client.force_login(self.accountant)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_issue"])
        self.assertEqual(len(response.context["invoices"]), 0)

    def test_factory_admin_can_open_issue_form(self):
        self.client.force_login(self.factory_admin)

        response = self.client.get(self.create_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["invoice"],
            self.invoice,
        )
        self.assertEqual(
            len(response.context["rows"]),
            1,
        )

    def test_factory_admin_can_issue_credit_note(self):
        self.client.force_login(self.factory_admin)

        response = self.client.post(
            self.create_url,
            {
                "credit_note_date": "2026-11-24",
                "reason": "Customer returned 10 packets",
                "invoice_line_id": [str(self.invoice_line.pk)],
                "quantity": ["10"],
            },
        )

        self.assertEqual(response.status_code, 302)

        credit_note = CreditNote.objects.get(
            invoice=self.invoice
        )

        self.assertEqual(
            credit_note.credit_note_number,
            "CN/2026-27/0001",
        )
        self.assertEqual(
            credit_note.grand_total,
            Decimal("525.00"),
        )
        self.assertEqual(
            credit_note.created_by,
            self.factory_admin,
        )

        self.invoice.refresh_from_db()

        self.assertEqual(
            self.invoice.balance_due,
            Decimal("7350.00"),
        )

        expected_detail_url = reverse(
            "sales:credit_note_detail",
            args=[credit_note.pk],
        )

        self.assertEqual(
            response["Location"],
            expected_detail_url,
        )

    def test_invalid_quantity_does_not_create_credit_note(self):
        self.client.force_login(self.factory_admin)

        response = self.client.post(
            self.create_url,
            {
                "credit_note_date": "2026-11-24",
                "reason": "Invalid quantity test",
                "invoice_line_id": [str(self.invoice_line.pk)],
                "quantity": ["151"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CreditNote.objects.count(), 0)

    def test_accountant_cannot_open_issue_form(self):
        self.client.force_login(self.accountant)

        response = self.client.get(self.create_url)

        self.assertEqual(response.status_code, 403)

    def test_user_without_role_cannot_open_register(self):
        self.client.force_login(self.outsider)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_must_log_in(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 302)

    def test_accountant_can_view_credit_note_detail(self):
        credit_note = self.create_credit_note()

        self.client.force_login(self.accountant)

        detail_url = reverse(
            "sales:credit_note_detail",
            args=[credit_note.pk],
        )

        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            credit_note.credit_note_number,
        )
        self.assertContains(
            response,
            self.invoice.invoice_number,
        )
