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