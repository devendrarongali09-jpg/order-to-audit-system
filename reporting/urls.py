from django.urls import path
from . import views

app_name = "reporting"

urlpatterns = [
    path(
        "stock-movements/",
        views.stock_movement_report,
        name="stock_movement_report",
    ),

    path(
        "stock-movements/export/excel/",
        views.stock_movement_export_excel,
        name="stock_movement_export_excel",
    ),
    path(
    "stock-movements/export/pdf/",
    views.stock_movement_export_pdf,
    name="stock_movement_export_pdf",
),
path(
    "raw-materials/",
    views.raw_material_movement_report,
    name="raw_material_movement_report",
),
path(
    "raw-materials/export/excel/",
    views.raw_material_export_excel,
    name="raw_material_export_excel",
),
path(
    "raw-materials/export/pdf/",
    views.raw_material_export_pdf,
    name="raw_material_export_pdf",
),
path(
    "sales-audit/",
    views.sales_audit_report,
    name="sales_audit_report",
),
path(
    "sales-audit/export/excel/",
    views.sales_audit_export_excel,
    name="sales_audit_export_excel",
),
path(
    "sales-audit/export/pdf/",
    views.sales_audit_export_pdf,
    name="sales_audit_export_pdf",
),
path(
    "production-audit/",
    views.production_audit_report,
    name="production_audit_report",
),
path(
    "production-audit/export/excel/",
    views.production_audit_export_excel,
    name="production_audit_export_excel",
),
path(
    "production-audit/export/pdf/",
    views.production_audit_export_pdf,
    name="production_audit_export_pdf",
),
path(
    "management-dashboard/",
    views.management_dashboard,
    name="management_dashboard",
),
]