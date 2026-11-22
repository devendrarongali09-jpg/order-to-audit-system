from django.contrib import admin

from .models import (
    UnitOfMeasure,
    Customer,
    Supplier,
    Product,
    RawMaterial
)


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "dimension", "conversion_factor")
    search_fields = ("name", "symbol")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "name", "customer_type", "gstin",
        "city", "state", "is_active"
    )
    list_filter = ("customer_type", "state", "is_active")
    search_fields = ("name", "gstin", "phone")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name", "gstin", "city", "state", "is_active"
    )
    search_fields = ("name", "gstin")

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "sku", "name", "hsn_code",
        "selling_price", "gst_rate", "is_active"
    )
    search_fields = ("sku", "name", "hsn_code")

@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "unit", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("sku", "name")