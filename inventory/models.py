
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

from masterdata.models import (
    RawMaterial,
    Product,
    UnitOfMeasure,
    Supplier
)


class StockMovement(models.Model):

    class MovementType(models.TextChoices):
        OPENING_IN = "opening_in", "Opening Stock"
        PURCHASE_IN = "purchase_in", "Purchase Receipt"
        PRODUCTION_ISSUE = "production_issue", "Production Issue"
        PRODUCTION_OUTPUT = "production_output", "Production Output"
        DISPATCH = "dispatch", "Sales Dispatch"
        RETURN_IN = "return_in", "Customer Return"
        ADJUSTMENT_IN = "adjustment_in", "Stock Adjustment In"
        ADJUSTMENT_OUT = "adjustment_out", "Stock Adjustment Out"

    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_movements"
    )
    purchase = models.ForeignKey(
    "Purchase",
    on_delete=models.PROTECT,
    null=True,
    blank=True,
    related_name="stock_movements"
)
    production_run = models.ForeignKey(
    "production.ProductionRun",
    on_delete=models.PROTECT,
    null=True,
    blank=True,
    related_name="stock_movements"
)
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_movements"
    )

    movement_type = models.CharField(
        max_length=25,
        choices=MovementType.choices
    )

    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[
            MinValueValidator(Decimal("0.001"))
        ]
    )
    unit_cost = models.DecimalField(
    max_digits=12,
    decimal_places=4,
    null=True,
    blank=True,
    validators=[
        MinValueValidator(Decimal("0"))
    ]
)

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT
    )

    reference_type = models.CharField(
        max_length=40
    )

    reference_number = models.CharField(
        max_length=60,
        blank=True
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_movements"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        raw_material__isnull=False,
                        product__isnull=True
                    )
                    |
                    Q(
                        raw_material__isnull=True,
                        product__isnull=False
                    )
                ),
                name="stock_movement_exactly_one_item"
            )
        ]

    def clean(self):
        if self.raw_material_id and self.unit_id:
            if self.raw_material.unit_id != self.unit_id:
                raise ValidationError(
                    "Raw material movement must use its stock unit."
                )

        if self.product_id and self.unit_id:
            if self.unit.dimension != UnitOfMeasure.Dimension.COUNT:
                raise ValidationError(
                    "Finished goods must use a count unit."
                )

    @property
    def signed_quantity(self):
        incoming = {
            self.MovementType.OPENING_IN,
            self.MovementType.PURCHASE_IN,
            self.MovementType.PRODUCTION_OUTPUT,
            self.MovementType.RETURN_IN,
            self.MovementType.ADJUSTMENT_IN,
        }

        if self.movement_type in incoming:
            return self.quantity

        return -self.quantity

    def __str__(self):
        item = self.raw_material or self.product

        return (
            f"{item} - {self.movement_type} "
            f"- {self.quantity} {self.unit.symbol}"
        )

class Purchase(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    purchase_number = models.CharField(
        max_length=50,
        unique=True
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchases"
    )

    purchase_date = models.DateField()

    supplier_invoice_number = models.CharField(
        max_length=50,
        blank=True
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_purchases"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    posted_at = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["-purchase_date", "-id"]

    @property
    def total_amount(self):
        return sum(
            (item.line_total for item in self.items.all()),
            Decimal("0.00")
        )

    def __str__(self):
        return self.purchase_number


class PurchaseItem(models.Model):

    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="items"
    )

    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name="purchase_items"
    )

    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[
            MinValueValidator(Decimal("0.001"))
        ]
    )

    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[
            MinValueValidator(Decimal("0.00"))
        ]
    )

    @property
    def line_total(self):
        return (self.quantity * self.unit_cost).quantize(
            Decimal("0.01")
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["purchase", "raw_material"],
                name="unique_material_per_purchase"
            )
        ]

    def clean(self):
        if self.purchase_id and self.purchase.status == Purchase.Status.POSTED:
            raise ValidationError(
                "Posted purchases cannot be edited."
            )

    def __str__(self):
        return (
            f"{self.raw_material.name}: "
            f"{self.quantity} @ ₹{self.unit_cost}"
        )
