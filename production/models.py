
from decimal import Decimal

from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

from masterdata.models import Product, RawMaterial, UnitOfMeasure


class BillOfMaterials(models.Model):

    code = models.CharField(
        max_length=40,
        unique=True
    )

    name = models.CharField(max_length=150)

    version = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version"],
                name="unique_bom_code_version"
            )
        ]

    def __str__(self):
        return f"{self.code} - {self.name} (v{self.version})"


class BOMItem(models.Model):

    bom = models.ForeignKey(
        BillOfMaterials,
        on_delete=models.PROTECT,
        related_name="items"
    )

    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name="bom_items"
    )

    quantity_per_batch = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[
            MinValueValidator(Decimal("0.001"))
        ]
    )

    class Meta:
        ordering = ["raw_material__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["bom", "raw_material"],
                name="unique_material_per_bom"
            )
        ]

    def __str__(self):
        return (
            f"{self.raw_material.name}: "
            f"{self.quantity_per_batch} "
            f"{self.raw_material.unit.symbol}"
        )


class ProductRecipe(models.Model):

    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        related_name="recipe"
    )

    bom = models.ForeignKey(
        BillOfMaterials,
        on_delete=models.PROTECT,
        related_name="product_recipes"
    )

    expected_output_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[
            MinValueValidator(Decimal("0.001"))
        ]
    )

    output_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="product_recipes"
    )

    class Meta:
        verbose_name = "Product Recipe"
        verbose_name_plural = "Product Recipes"

    def clean(self):
        if (
            self.output_unit_id
            and self.output_unit.dimension
            != UnitOfMeasure.Dimension.COUNT
        ):
            raise ValidationError({
                "output_unit":
                "Recipe output must use a count unit such as piece."
            })

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.expected_output_quantity} "
            f"{self.output_unit.symbol}"
        )

from django.conf import settings
from django.utils import timezone


class ProductionRun(models.Model):

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    run_number = models.CharField(
        max_length=50,
        unique=True
    )

    product_recipe = models.ForeignKey(
        ProductRecipe,
        on_delete=models.PROTECT,
        related_name="production_runs"
    )

    batch_count = models.PositiveIntegerField(default=1)

    production_date = models.DateField()

    actual_good_output = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[
            MinValueValidator(Decimal("0"))
        ]
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
        related_name="production_runs"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    posted_at = models.DateTimeField(null=True, blank=True)

    @property
    def expected_output_quantity(self):
        return (
            self.product_recipe.expected_output_quantity
            * self.batch_count
        )

    @property
    def output_wastage(self):
        return max(
            Decimal("0"),
            self.expected_output_quantity - self.actual_good_output
        )

    @property
    def yield_variance_percent(self):
        expected = self.expected_output_quantity

        if not expected:
            return Decimal("0")

        return (
            (self.actual_good_output - expected)
            / expected
            * Decimal("100")
        ).quantize(Decimal("0.01"))

    class Meta:
        ordering = ["-production_date", "-id"]

    def __str__(self):
        return self.run_number


class ProductionConsumption(models.Model):

    production_run = models.ForeignKey(
        ProductionRun,
        on_delete=models.PROTECT,
        related_name="consumptions"
    )

    raw_material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name="production_consumptions"
    )

    # Calculated from BOM and batch count.
    # Users should not manually change this value.
    planned_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        editable=False
    )

    actual_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[
            MinValueValidator(Decimal("0"))
        ]
    )

    @property
    def variance_quantity(self):
        return self.actual_quantity - self.planned_quantity

    @property
    def variance_percent(self):
        if self.planned_quantity == 0:
            return Decimal("0")

        return (
            self.variance_quantity
            / self.planned_quantity
            * Decimal("100")
        ).quantize(Decimal("0.01"))

    def clean(self):
        super().clean()

        if not self.production_run_id or not self.raw_material_id:
            return

        if self.production_run.status == ProductionRun.Status.POSTED:
            raise ValidationError(
                "Posted production consumption cannot be edited."
            )

        bom_item = BOMItem.objects.filter(
            bom=self.production_run.product_recipe.bom,
            raw_material=self.raw_material
        ).first()

        if bom_item is None:
            raise ValidationError({
                "raw_material":
                "This material does not belong to the production BOM."
            })

        self.planned_quantity = (
            bom_item.quantity_per_batch
            * self.production_run.batch_count
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["production_run", "raw_material"],
                name="unique_material_per_production_run"
            )
        ]

    def __str__(self):
        return (
            f"{self.production_run.run_number} - "
            f"{self.raw_material.name}"
        )

class ProductionCost(models.Model):

    class ValuationMethod(models.TextChoices):
        WEIGHTED_AVERAGE = (
            "weighted_average",
            "Weighted Average"
        )

    production_run = models.OneToOneField(
        ProductionRun,
        on_delete=models.PROTECT,
        related_name="cost_record"
    )

    labour_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0")
    )

    gas_power_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0")
    )

    other_overhead = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0")
    )

    valuation_method = models.CharField(
        max_length=30,
        choices=ValuationMethod.choices,
        default=ValuationMethod.WEIGHTED_AVERAGE
    )

    material_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        editable=False
    )

    total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        editable=False
    )

    cost_per_packet = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        editable=False
    )

    calculated_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Costing - {self.production_run.run_number}"
