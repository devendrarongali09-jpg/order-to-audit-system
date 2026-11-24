
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Sum
from masterdata.models import Customer, Product


class SalesOrder(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        INVOICED = "invoiced", "Invoiced"
        DISPATCHED = "dispatched", "Dispatched"

    order_number = models.CharField(
        max_length=50,
        unique=True
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="sales_orders"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_sales_orders"
    )

    order_date = models.DateField()

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.DRAFT
    )

    notes = models.TextField(blank=True)

    rejection_reason = models.TextField(blank=True)

    submitted_at = models.DateTimeField(
        null=True,
        blank=True
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_sales_orders"
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date", "-id"]

    @property
    def total_amount(self):
        return sum(
            (item.line_total for item in self.items.all()),
            Decimal("0.00")
        )

    def __str__(self):
        return self.order_number


class SalesOrderItem(models.Model):

    order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sales_order_items"
    )

    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1)
        ]
    )

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00"))
        ]
    )

    @property
    def line_total(self):
        return (
            Decimal(self.quantity) * self.unit_price
        ).quantize(Decimal("0.01"))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"],
                name="unique_product_per_sales_order"
            )
        ]

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.quantity} packets"
        )

class InvoiceSequence(models.Model):

    financial_year = models.CharField(
        max_length=9,
        unique=True
    )

    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.financial_year


class Invoice(models.Model):

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        CANCELLED = "cancelled", "Cancelled"

    invoice_number = models.CharField(
        max_length=40,
        unique=True
    )

    order = models.OneToOneField(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name="invoice"
    )

    invoice_date = models.DateField()

    seller_name = models.CharField(
        max_length=150,
        default="Demo Food Manufacturer"
    )

    seller_gstin = models.CharField(
        max_length=15,
        blank=True
    )

    seller_state_code = models.CharField(
        max_length=2,
        default="33"
    )

    customer_name = models.CharField(max_length=150)

    customer_gstin = models.CharField(
        max_length=15,
        blank=True
    )

    customer_address = models.TextField(blank=True)

    place_of_supply = models.CharField(max_length=100)

    place_of_supply_state_code = models.CharField(
        max_length=2
    )

    taxable_value = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    cgst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    sgst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    igst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    grand_total = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ISSUED
    )

    cancellation_reason = models.TextField(blank=True)

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_invoices"
    )

    issued_at = models.DateTimeField(default=timezone.now)

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["-invoice_date", "-id"]

    def __str__(self):
        return self.invoice_number

    @property
    def amount_paid(self):
        total = self.payments.filter(
            status=Payment.Status.RECEIVED
        ).aggregate(
            total=Sum("amount")
        )["total"]

        return total or Decimal("0.00")

    @property
    def amount_credited(self):
        total = self.credit_notes.aggregate(
            total=Sum("grand_total")
        )["total"]

        return total or Decimal("0.00")

    @property
    def net_total(self):
        return max(
            Decimal("0.00"),
            self.grand_total - self.amount_credited
        )

    @property
    def customer_credit(self):
        return max(
            Decimal("0.00"),
            self.amount_paid - self.net_total
        )

    @property
    def balance_due(self):
        return max(
            Decimal("0.00"),
            self.net_total - self.amount_paid
        )

    @property
    def payment_status(self):

        if self.amount_paid > self.net_total:
            return "Customer Credit"

        if self.net_total == 0:
            return "Fully Credited"

        if self.amount_paid == 0:
            return "Unpaid"

        if self.amount_paid < self.net_total:
            return "Partially Paid"

        return "Paid"

class InvoiceLine(models.Model):

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="lines"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="invoice_lines"
    )

    product_name = models.CharField(max_length=150)

    hsn_code = models.CharField(max_length=8, blank=True)

    quantity = models.PositiveIntegerField()

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2
    )

    taxable_value = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    cgst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    sgst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    igst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    def __str__(self):
        return f"{self.product_name} - {self.quantity}"


class CreditNoteSequence(models.Model):

    financial_year = models.CharField(
        max_length=9,
        unique=True
    )

    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.financial_year


class CreditNote(models.Model):

    credit_note_number = models.CharField(
        max_length=40,
        unique=True
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="credit_notes"
    )

    credit_note_date = models.DateField()

    reason = models.TextField()

    taxable_value = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    cgst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    sgst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    igst_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    grand_total = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_credit_notes"
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False
    )

    class Meta:
        ordering = ["-credit_note_date", "-id"]

    def __str__(self):
        return self.credit_note_number


class CreditNoteLine(models.Model):

    credit_note = models.ForeignKey(
        CreditNote,
        on_delete=models.PROTECT,
        related_name="lines"
    )

    invoice_line = models.ForeignKey(
        InvoiceLine,
        on_delete=models.PROTECT,
        related_name="credit_note_lines"
    )

    product_name = models.CharField(max_length=150)

    hsn_code = models.CharField(
        max_length=8,
        blank=True
    )

    quantity = models.PositiveIntegerField(
    validators=[
        MinValueValidator(1)
    ]
)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2
    )

    taxable_value = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    cgst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    sgst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    igst_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["credit_note", "invoice_line"],
                name="unique_invoice_line_per_credit_note"
            )
        ]

    def __str__(self):
        return f"{self.credit_note.credit_note_number} - {self.product_name}"


class Payment(models.Model):

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        BANK = "bank", "Bank Transfer"
        CHEQUE = "cheque", "Cheque"
        CARD = "card", "Card"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        REVERSED = "reversed", "Reversed"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments"
    )

    payment_date = models.DateField()

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01"))
        ]
    )

    method = models.CharField(
        max_length=10,
        choices=Method.choices
    )

    transaction_reference = models.CharField(
        max_length=100,
        blank=True
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.RECEIVED
    )

    notes = models.TextField(blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_payments"
    )

    reversed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_payments"
    )

    reversal_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-payment_date", "-id"]

    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.amount}"



class Dispatch(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    dispatch_number = models.CharField(
        max_length=50,
        unique=True
    )

    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name="dispatches"
    )

    dispatch_date = models.DateField()

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_dispatches"
    )

    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="posted_dispatches"
    )

    posted_at = models.DateTimeField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-dispatch_date", "-id"]

    def __str__(self):
        return self.dispatch_number


class DispatchItem(models.Model):

    dispatch = models.ForeignKey(
        Dispatch,
        on_delete=models.CASCADE,
        related_name="items"
    )

    order_item = models.ForeignKey(
        SalesOrderItem,
        on_delete=models.PROTECT,
        related_name="dispatch_items"
    )

    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1)
        ]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dispatch", "order_item"],
                name="unique_order_item_per_dispatch"
            )
        ]

    def __str__(self):
        return (
            f"{self.dispatch.dispatch_number} - "
            f"{self.order_item.product.name} - "
            f"{self.quantity}"
        )
