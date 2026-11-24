from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path(
        "invoices/<int:invoice_id>/pdf/",
        views.invoice_pdf,
        name="invoice_pdf",
    ),
    path(
        "salesman/",
        views.salesman_dashboard,
        name="salesman_dashboard",
    ),
    path(
        "salesman/orders/new/",
        views.salesman_new_order,
        name="salesman_new_order",
    ),
    path(
        "credit-notes/",
        views.credit_note_list,
        name="credit_note_list",
    ),
    path(
        "credit-notes/create/<int:invoice_id>/",
        views.credit_note_create,
        name="credit_note_create",
    ),
    path(
        "credit-notes/<int:credit_note_id>/",
        views.credit_note_detail,
        name="credit_note_detail",
    ),
]