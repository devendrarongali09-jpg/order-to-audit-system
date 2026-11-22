
from decimal import Decimal

from django.db import models
from django.core.validators import MinValueValidator


class UnitOfMeasure(models.Model):

    class Dimension(models.TextChoices):
        MASS = "mass", "Mass"
        VOLUME = "volume", "Volume"
        COUNT = "count", "Count"

    name = models.CharField(max_length=40, unique=True)
    symbol = models.CharField(max_length=10, unique=True)

    dimension = models.CharField(
        max_length=10,
        choices=Dimension.choices
    )

    conversion_factor = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.000001"))]
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class Customer(models.Model):

    class CustomerType(models.TextChoices):
        RETAILER = "retailer", "Retailer"
        DISTRIBUTOR = "distributor", "Distributor"

    name = models.CharField(max_length=150)

    customer_type = models.CharField(
        max_length=20,
        choices=CustomerType.choices
    )

    gstin = models.CharField(max_length=15, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    state_code = models.CharField(max_length=2)
    pincode = models.CharField(max_length=10, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):

    name = models.CharField(max_length=150)

    gstin = models.CharField(max_length=15, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    state_code = models.CharField(max_length=2)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Product(models.Model):

    sku = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)

    pack_size_value = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))]
    )

    pack_size_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="products"
    )

    hsn_code = models.CharField(max_length=8, blank=True)

    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00")
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.sku})"


class RawMaterial(models.Model):

    class Category(models.TextChoices):
        INGREDIENT = "ingredient", "Ingredient"
        PACKAGING = "packaging", "Packaging"

    sku = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)

    category = models.CharField(
        max_length=20,
        choices=Category.choices
    )

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="raw_materials"
    )

    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))]
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.sku})"
