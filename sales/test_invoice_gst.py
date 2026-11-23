
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from masterdata.models import Customer, Product, UnitOfMeasure
from sales.models import SalesOrder, SalesOrderItem, Invoice
from sales.services import generate_invoice


User = get_user_model()


@override_settings(
    SELLER_LEGAL_NAME="Demo Food Manufacturer",
    SELLER_GSTIN="DEMO-SELLER-001",
    SELLER_STATE_CODE="33",
)
class InvoiceGSTTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="gst_test_user",
            password="testpassword123",
        )

        self.gram = UnitOfMeasure.objects.create(
            name="Gram",
            symbol="g",
            dimension=UnitOfMeasure.Dimension.MASS,
        )

        self.product = Product.objects.create(
            sku="GST-MUR-200",
            name="Murukku 200 g",
            pack_size_value=Decimal("200"),
            pack_size_unit=self.gram,
            hsn_code="210690",
            selling_price=Decimal("50.00"),
            gst_rate=Decimal("5.00"),
        )

    def create_order(self, customer, quantity, order_number):
        order = SalesOrder.objects.create(
            order_number=order_number,
            customer=customer,
            created_by=self.user,
            order_date=date(2026, 11, 23),
            status=SalesOrder.Status.APPROVED,
        )

        SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=quantity,
            unit_price=Decimal("50.00"),
        )

        return order

    def test_intrastate_invoice_gstin_hsn_and_cgst_sgst(self):
        customer = Customer.objects.create(
            name="GST Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            gstin="DEMO-RET-000001",
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        order = self.create_order(
            customer,
            150,
            "SO-GST-INTRA-001",
        )

        invoice = generate_invoice(
            order_id=order.pk,
            user=self.user,
        )

        line = invoice.lines.get()

        self.assertEqual(invoice.seller_gstin, "DEMO-SELLER-001")
        self.assertEqual(invoice.customer_gstin, "DEMO-RET-000001")

        self.assertEqual(line.hsn_code, "210690")
        self.assertEqual(line.gst_rate, Decimal("5.00"))

        self.assertEqual(invoice.taxable_value, Decimal("7500.00"))
        self.assertEqual(invoice.cgst_total, Decimal("187.50"))
        self.assertEqual(invoice.sgst_total, Decimal("187.50"))
        self.assertEqual(invoice.igst_total, Decimal("0.00"))
        self.assertEqual(invoice.grand_total, Decimal("7875.00"))

    def test_interstate_invoice_uses_igst(self):
        customer = Customer.objects.create(
            name="GST Test Distributor",
            customer_type=Customer.CustomerType.DISTRIBUTOR,
            gstin="DEMO-DIST-00001",
            city="Bengaluru",
            state="Karnataka",
            state_code="29",
        )

        order = self.create_order(
            customer,
            120,
            "SO-GST-INTER-001",
        )

        invoice = generate_invoice(
            order_id=order.pk,
            user=self.user,
        )

        line = invoice.lines.get()

        self.assertEqual(invoice.seller_gstin, "DEMO-SELLER-001")
        self.assertEqual(invoice.customer_gstin, "DEMO-DIST-00001")

        self.assertEqual(line.hsn_code, "210690")
        self.assertEqual(line.gst_rate, Decimal("5.00"))

        self.assertEqual(invoice.taxable_value, Decimal("6000.00"))
        self.assertEqual(invoice.cgst_total, Decimal("0.00"))
        self.assertEqual(invoice.sgst_total, Decimal("0.00"))
        self.assertEqual(invoice.igst_total, Decimal("300.00"))
        self.assertEqual(invoice.grand_total, Decimal("6300.00"))

    def test_invoice_snapshots_do_not_change_after_master_edit(self):
        customer = Customer.objects.create(
            name="Snapshot Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            gstin="DEMO-RET-000001",
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        order = self.create_order(
            customer,
            1,
            "SO-GST-SNAPSHOT-001",
        )

        invoice = generate_invoice(
            order_id=order.pk,
            user=self.user,
        )

        customer.gstin = "DEMO-NEW-000001"
        customer.save()

        self.product.hsn_code = "UPDATED"
        self.product.gst_rate = Decimal("12.00")
        self.product.save()

        invoice.refresh_from_db()
        line = invoice.lines.get()

        self.assertEqual(invoice.customer_gstin, "DEMO-RET-000001")
        self.assertEqual(line.hsn_code, "210690")
        self.assertEqual(line.gst_rate, Decimal("5.00"))
