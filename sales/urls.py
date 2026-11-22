from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path(
        "invoices/<int:invoice_id>/pdf/",
        views.invoice_pdf,
        name="invoice_pdf"
    ),
]
from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path(
        "invoices/<int:invoice_id>/pdf/",
        views.invoice_pdf,
        name="invoice_pdf"
    ),

    path(
        "salesman/",
        views.salesman_dashboard,
        name="salesman_dashboard"
    ),

    path(
        "salesman/orders/new/",
        views.salesman_new_order,
        name="salesman_new_order"
    ),
]