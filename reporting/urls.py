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
path(
    "purchase-register/",
    views.purchase_register_report,
    name="purchase_register_report",
),

path(
    "purchase-register/export/excel/",
    views.purchase_register_export_excel,
    name="purchase_register_export_excel",
),

path(
    "purchase-register/export/pdf/",
    views.purchase_register_export_pdf,
    name="purchase_register_export_pdf",
),
path(
    "gst-outward/",
    views.gst_outward_report,
    name="gst_outward_report",
),

path(
    "gst-outward/export/excel/",
    views.gst_outward_export_excel,
    name="gst_outward_export_excel",
),

path(
    "gst-outward/export/pdf/",
    views.gst_outward_export_pdf,
    name="gst_outward_export_pdf",
),

    path(
        "outstanding-ageing/",
        views.outstanding_ageing_report,
        name="outstanding_ageing_report",
    ),
    path(
        "outstanding-ageing/export/excel/",
        views.outstanding_ageing_export_excel,
        name="outstanding_ageing_export_excel",
    ),
    path(
        "outstanding-ageing/export/pdf/",
        views.outstanding_ageing_export_pdf,
        name="outstanding_ageing_export_pdf",
    ),
]