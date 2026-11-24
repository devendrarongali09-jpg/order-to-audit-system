from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


class ManagementDashboardTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="reporting_test",
            password="TestPassword123!"
        )

        factory_admin_group, created = Group.objects.get_or_create(
            name="Factory Admin"
        )
        self.user.groups.add(factory_admin_group)

        self.dashboard_url = reverse("reporting:management_dashboard")

    def test_dashboard_requires_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)

    def test_dashboard_loads_for_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "reporting/management_dashboard.html"
        )

    def test_dashboard_accepts_valid_date_filters(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "2026-11-21",
                "to_date": "2026-11-22",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["from_date"], "2026-11-21")
        self.assertEqual(response.context["to_date"], "2026-11-22")

    def test_dashboard_rejects_invalid_date(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "invalid-date",
                "to_date": "2026-11-22",
            }
        )

        self.assertEqual(response.status_code, 400)

    def test_dashboard_rejects_reversed_date_range(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "2026-11-22",
                "to_date": "2026-11-21",
            }
        )

        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_without_role_gets_forbidden(self):
        User = get_user_model()

        unauthorized_user = User.objects.create_user(
            username="unauthorized_test",
            password="TestPassword123!"
        )

        self.client.force_login(unauthorized_user)

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 403)

class OutstandingAgeingReportTests(TestCase):

    def setUp(self):
        from django.contrib.auth.models import Group
        from django.contrib.auth import get_user_model
        from masterdata.models import Customer
        from sales.models import Invoice, Payment, SalesOrder

        self.User = get_user_model()
        self.Customer = Customer
        self.Invoice = Invoice
        self.Payment = Payment
        self.SalesOrder = SalesOrder
        self.user = self.User.objects.create_user(
            username="ageing_report_user",
            password="TestPassword123!",
        )
        group, _created = Group.objects.get_or_create(name="Factory Admin")
        self.user.groups.add(group)
        self.customer = self.Customer.objects.create(
            name="Ageing Test Retailer",
            customer_type="retailer",
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )
        self.as_of_date = date(2026, 11, 22)
        self.report_url = reverse("reporting:outstanding_ageing_report")

    def create_invoice(self, number, age_days, amount, status=None):
        invoice_date = self.as_of_date - timedelta(days=age_days)
        order = self.SalesOrder.objects.create(
            order_number=f"AGE-ORDER-{number}",
            customer=self.customer,
            order_date=invoice_date,
            status=self.SalesOrder.Status.INVOICED,
        )
        fields = {
            "invoice_number": f"AGE/2026-27/{number:04d}",
            "order": order,
            "invoice_date": invoice_date,
            "customer_name": self.customer.name,
            "place_of_supply": "Tamil Nadu",
            "place_of_supply_state_code": "33",
            "taxable_value": amount,
            "grand_total": amount,
            "issued_by": self.user,
        }
        if status:
            fields["status"] = status
        return self.Invoice.objects.create(**fields)

    def add_payment(self, invoice, amount, status=None, payment_date=None):
        fields = {
            "invoice": invoice,
            "payment_date": payment_date or self.as_of_date,
            "amount": amount,
            "method": self.Payment.Method.UPI,
            "received_by": self.user,
        }
        if status:
            fields["status"] = status
        return self.Payment.objects.create(**fields)

    def test_ageing_buckets_and_reversed_payments(self):
        from sales.models import Invoice, Payment

        invoice_0_30 = self.create_invoice(1, 10, Decimal("100.00"))
        self.add_payment(invoice_0_30, Decimal("25.00"))
        self.add_payment(
            invoice_0_30,
            Decimal("60.00"),
            status=Payment.Status.REVERSED,
        )

        invoice_31_60 = self.create_invoice(2, 35, Decimal("200.00"))
        self.add_payment(invoice_31_60, Decimal("50.00"))
        self.create_invoice(3, 65, Decimal("300.00"))
        self.create_invoice(4, 95, Decimal("400.00"))

        paid_invoice = self.create_invoice(5, 20, Decimal("80.00"))
        self.add_payment(paid_invoice, Decimal("80.00"))
        self.create_invoice(
            6,
            45,
            Decimal("70.00"),
            status=Invoice.Status.CANCELLED,
        )

        self.client.force_login(self.user)
        response = self.client.get(
            self.report_url,
            {"as_of_date": self.as_of_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 4)
        buckets = response.context["totals"]["bucket_totals"]
        self.assertEqual(buckets["0_30"], Decimal("75.00"))
        self.assertEqual(buckets["31_60"], Decimal("150.00"))
        self.assertEqual(buckets["61_90"], Decimal("300.00"))
        self.assertEqual(buckets["over_90"], Decimal("400.00"))
        self.assertEqual(
            response.context["totals"]["total_outstanding"],
            Decimal("925.00"),
        )

    def test_future_dated_payment_is_not_deducted_as_of_report_date(self):
        invoice = self.create_invoice(7, 5, Decimal("100.00"))
        self.add_payment(
            invoice,
            Decimal("100.00"),
            payment_date=self.as_of_date + timedelta(days=1),
        )
        self.client.force_login(self.user)
        response = self.client.get(
            self.report_url,
            {"as_of_date": self.as_of_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0]["balance_due"], Decimal("100.00"))

    def test_invalid_as_of_date_returns_bad_request(self):
        self.client.force_login(self.user)
        response = self.client.get(self.report_url, {"as_of_date": "not-a-date"})
        self.assertEqual(response.status_code, 400)

    def test_report_requires_login(self):
        response = self.client.get(self.report_url)
        self.assertEqual(response.status_code, 302)

    def test_user_without_report_role_is_forbidden(self):
        unauthorized = self.User.objects.create_user(
            username="ageing_unauthorized",
            password="TestPassword123!",
        )
        self.client.force_login(unauthorized)
        response = self.client.get(self.report_url)
        self.assertEqual(response.status_code, 403)

    def test_excel_and_pdf_exports(self):
        self.client.force_login(self.user)
        query = {"as_of_date": self.as_of_date.isoformat()}

        excel_response = self.client.get(
            reverse("reporting:outstanding_ageing_export_excel"),
            query,
        )
        self.assertEqual(excel_response.status_code, 200)
        self.assertIn(
            "spreadsheetml.sheet",
            excel_response["Content-Type"],
        )

        pdf_response = self.client.get(
            reverse("reporting:outstanding_ageing_export_pdf"),
            query,
        )
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
    def test_credit_notes_are_deducted_only_after_their_date(self):
        from masterdata.models import Product, UnitOfMeasure
        from sales.models import InvoiceLine, CreditNote, CreditNoteLine

        invoice = self.create_invoice(
            20,
            10,
            Decimal("100.00"),
        )

        gram = UnitOfMeasure.objects.create(
            name="Ageing Test Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        product = Product.objects.create(
            sku="AGE-CREDIT-001",
            name="Ageing Credit Test Product",
            pack_size_value=Decimal("200"),
            pack_size_unit=gram,
            hsn_code="210690",
            selling_price=Decimal("25.00"),
            gst_rate=Decimal("0.00"),
        )

        invoice_line = InvoiceLine.objects.create(
            invoice=invoice,
            product=product,
            product_name=product.name,
            hsn_code="210690",
            quantity=4,
            unit_price=Decimal("25.00"),
            gst_rate=Decimal("0.00"),
            taxable_value=Decimal("100.00"),
            cgst_amount=Decimal("0.00"),
            sgst_amount=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        )

        credit_date = self.as_of_date + timedelta(days=1)

        credit_note = CreditNote.objects.create(
            credit_note_number="CN/2026-27/TEST01",
            invoice=invoice,
            credit_note_date=credit_date,
            reason="Test goods return",
            taxable_value=Decimal("25.00"),
            cgst_total=Decimal("0.00"),
            sgst_total=Decimal("0.00"),
            igst_total=Decimal("0.00"),
            grand_total=Decimal("25.00"),
            created_by=self.user,
        )

        CreditNoteLine.objects.create(
            credit_note=credit_note,
            invoice_line=invoice_line,
            product_name=product.name,
            hsn_code="210690",
            quantity=1,
            unit_price=Decimal("25.00"),
            gst_rate=Decimal("0.00"),
            taxable_value=Decimal("25.00"),
            cgst_amount=Decimal("0.00"),
            sgst_amount=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            total_amount=Decimal("25.00"),
        )

        self.client.force_login(self.user)

        # Before the credit-note date, the full invoice remains due.
        before_response = self.client.get(
            self.report_url,
            {"as_of_date": self.as_of_date.isoformat()},
        )

        self.assertEqual(before_response.status_code, 200)
        before_row = before_response.context["rows"][0]

        self.assertEqual(
            before_row["amount_credited"],
            Decimal("0.00"),
        )
        self.assertEqual(
            before_row["net_invoice_amount"],
            Decimal("100.00"),
        )
        self.assertEqual(
            before_row["balance_due"],
            Decimal("100.00"),
        )

        # After the credit-note date, the credit reduces the balance.
        after_date = credit_date + timedelta(days=1)

        after_response = self.client.get(
            self.report_url,
            {"as_of_date": after_date.isoformat()},
        )

        self.assertEqual(after_response.status_code, 200)
        after_row = after_response.context["rows"][0]

        self.assertEqual(
            after_row["amount_credited"],
            Decimal("25.00"),
        )
        self.assertEqual(
            after_row["net_invoice_amount"],
            Decimal("75.00"),
        )
        self.assertEqual(
            after_row["balance_due"],
            Decimal("75.00"),
        )