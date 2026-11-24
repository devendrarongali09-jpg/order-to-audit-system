from uuid import uuid4
from decimal import Decimal
from django.db.models import Sum
from django.utils.dateparse import parse_date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import group_required
from inventory.services import get_stock_balance
from masterdata.models import Customer, Product

from .models import (
    Invoice,
    InvoiceLine,
    SalesOrder,
    SalesOrderItem,
    CreditNote,
    CreditNoteLine,
)

from .services import (
    check_order_stock,
    submit_order,
    issue_credit_note,
)
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)



@staff_member_required
def invoice_pdf(request, invoice_id):

    invoice = get_object_or_404(
        Invoice.objects.select_related("order").prefetch_related("lines"),
        pk=invoice_id
    )

    buffer = BytesIO()

    response = HttpResponse(
        content_type="application/pdf"
    )

    filename = invoice.invoice_number.replace("/", "-")

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}.pdf"'
    )

    document = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        spaceAfter=10,
    )

    elements = []

    elements.append(
        Paragraph("DEMONSTRATION TAX INVOICE", title_style)
    )

    elements.append(
        Paragraph(
            "<b>NOT VALID FOR TAX CREDIT OR LEGAL TAX USE</b>",
            ParagraphStyle(
                "DemoNotice",
                parent=styles["Normal"],
                textColor=colors.red,
                alignment=TA_CENTER,
                fontSize=9,
            )
        )
    )

    elements.append(Spacer(1, 8 * mm))

    invoice_info = [
        ["Invoice Number", escape(invoice.invoice_number),
         "Invoice Date", invoice.invoice_date.strftime("%d-%m-%Y")],
        ["Seller", escape(invoice.seller_name),
         "Seller State Code", escape(invoice.seller_state_code)],
        ["Seller GSTIN", escape(invoice.seller_gstin or "Not configured"),
         "Status", escape(invoice.status.upper())],
        ["Customer", escape(invoice.customer_name),
         "Place of Supply", escape(invoice.place_of_supply)],
        ["Customer GSTIN", escape(invoice.customer_gstin or "Not configured"),
         "State Code", escape(invoice.place_of_supply_state_code)],
    ]

    info_table = Table(
        invoice_info,
        colWidths=[32*mm, 52*mm, 32*mm, 52*mm]
    )

    info_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
    ]))

    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    line_data = [[
        "Product",
        "HSN",
        "Qty",
        "Rate",
        "Taxable",
        "GST %",
        "Tax",
        "Total",
    ]]

    for line in invoice.lines.all():

        total_tax = (
            line.cgst_amount
            + line.sgst_amount
            + line.igst_amount
        )

        line_data.append([
            Paragraph(escape(line.product_name), styles["Normal"]),
            line.hsn_code or "-",
            str(line.quantity),
            f"{line.unit_price:.2f}",
            f"{line.taxable_value:.2f}",
            f"{line.gst_rate:.2f}",
            f"{total_tax:.2f}",
            f"{line.total_amount:.2f}",
        ])

    line_table = Table(
        line_data,
        colWidths=[
            36*mm, 17*mm, 12*mm, 20*mm,
            24*mm, 14*mm, 19*mm, 23*mm
        ],
        repeatRows=1
    )

    line_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#315B73")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
    ]))

    elements.append(line_table)
    elements.append(Spacer(1, 8 * mm))

    tax_data = [
        ["Taxable Value", f"Rs. {invoice.taxable_value:.2f}"],
        ["CGST", f"Rs. {invoice.cgst_total:.2f}"],
        ["SGST", f"Rs. {invoice.sgst_total:.2f}"],
        ["IGST", f"Rs. {invoice.igst_total:.2f}"],
        ["GRAND TOTAL", f"Rs. {invoice.grand_total:.2f}"],
    ]

    tax_table = Table(
        tax_data,
        colWidths=[100*mm, 65*mm],
        hAlign="RIGHT"
    )

    tax_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
        ("BACKGROUND", (0, 4), (-1, 4), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    elements.append(tax_table)
    elements.append(Spacer(1, 12 * mm))

    elements.append(
        Paragraph(
            "This is a demonstration document generated by "
            "the Order-to-Audit Management System.",
            styles["Italic"]
        )
    )

    document.build(elements)

    return response
@group_required("Salesman")
def salesman_dashboard(request):

    orders = list(
        SalesOrder.objects.filter(
            created_by=request.user
        )
        .select_related("customer")
        .prefetch_related("items__product")
    )

    total_order_value = Decimal("0.00")
    customer_totals = {}

    for order in orders:
        total = order.total_amount
        total_order_value += total

        customer_name = order.customer.name

        customer_totals[customer_name] = (
            customer_totals.get(
                customer_name,
                Decimal("0.00")
            ) + total
        )

    customer_sales = [
        {
            "name": name,
            "total": total
        }
        for name, total in sorted(customer_totals.items())
    ]

    context = {
        "orders": orders,
        "order_count": len(orders),
        "total_order_value": total_order_value,
        "customer_sales": customer_sales,
    }

    return render(
        request,
        "sales/salesman_dashboard.html",
        context
    )
@group_required("Salesman")
def salesman_new_order(request):

    customers = Customer.objects.filter(
        is_active=True
    ).order_by("name")

    products = list(
        Product.objects.filter(
            is_active=True
        ).order_by("name")
    )

    product_options = [
        {
            "product": product,
            "available_stock": get_stock_balance(
                product=product
            ),
        }
        for product in products
    ]

    if request.method == "POST":

        customer_id = request.POST.get("customer_id")

        product_ids = request.POST.getlist("product_id")
        quantities = request.POST.getlist("quantity")

        notes = request.POST.get("notes", "").strip()

        customer = Customer.objects.filter(
            pk=customer_id,
            is_active=True
        ).first()

        errors = []

        if not customer:
            errors.append("Please select a valid customer.")

        if not product_ids or len(product_ids) != len(quantities):
            errors.append("Add at least one valid product.")

        product_map = {
            product.pk: product
            for product in products
        }

        order_items = []
        seen_products = set()

        for raw_id, raw_quantity in zip(
            product_ids,
            quantities
        ):

            try:
                product_id = int(raw_id)
                quantity = int(raw_quantity)
            except (ValueError, TypeError):
                errors.append("Invalid product or quantity.")
                continue

            product = product_map.get(product_id)

            if not product:
                errors.append("An invalid or inactive product was selected.")
                continue

            if product_id in seen_products:
                errors.append(
                    f"{product.name} was added more than once."
                )
                continue

            if quantity <= 0:
                errors.append(
                    f"Enter a positive quantity for {product.name}."
                )
                continue

            seen_products.add(product_id)

            order_items.append({
                "product": product,
                "quantity": quantity,
                "unit_price": product.selling_price,
            })

        if errors:
            for error in errors:
                messages.error(request, error)

            return redirect("sales:salesman_new_order")

        try:

            with transaction.atomic():

                order = SalesOrder.objects.create(
                    order_number=(
                        f"ORD-{timezone.localdate().year}-"
                        f"{uuid4().hex[:8].upper()}"
                    ),
                    customer=customer,
                    created_by=request.user,
                    order_date=timezone.localdate(),
                    notes=notes,
                )

                for item in order_items:

                    SalesOrderItem.objects.create(
                        order=order,
                        product=item["product"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                    )

                submit_order(order_id=order.pk)

                shortages = check_order_stock(order)

            messages.success(
                request,
                f"Order {order.order_number} submitted successfully."
            )

            if shortages:
                messages.warning(
                    request,
                    "Stock availability warning: "
                    + "; ".join(shortages)
                )

            return redirect("sales:salesman_dashboard")

        except ValidationError as exc:

            messages.error(
                request,
                "; ".join(exc.messages)
            )

            return redirect("sales:salesman_new_order")

    return render(
        request,
        "sales/salesman_order_form.html",
        {
            "customers": customers,
            "product_options": product_options,
        }
    )
def _credit_note_form_rows(invoice, selected_quantities=None):
    selected_quantities = selected_quantities or {}

    credit_totals = (
        CreditNoteLine.objects
        .filter(invoice_line__invoice=invoice)
        .values("invoice_line_id")
        .annotate(
            quantity=Sum("quantity"),
            taxable=Sum("taxable_value"),
            cgst=Sum("cgst_amount"),
            sgst=Sum("sgst_amount"),
            igst=Sum("igst_amount"),
        )
    )

    credit_map = {
        row["invoice_line_id"]: row
        for row in credit_totals
    }

    rows = []

    for line in invoice.lines.all():
        previous = credit_map.get(line.pk, {})

        credited_quantity = previous.get("quantity") or 0

        rows.append({
            "line": line,
            "credited_quantity": credited_quantity,
            "remaining_quantity": max(
                0, line.quantity - credited_quantity
            ),
            "credited_taxable": previous.get("taxable") or Decimal("0.00"),
            "credited_cgst": previous.get("cgst") or Decimal("0.00"),
            "credited_sgst": previous.get("sgst") or Decimal("0.00"),
            "credited_igst": previous.get("igst") or Decimal("0.00"),
            "selected_quantity": selected_quantities.get(line.pk, 0),
            "is_igst": (
                invoice.seller_state_code
                != invoice.place_of_supply_state_code
            ),
        })

    return rows


@group_required("Owner", "Factory Admin", "Accountant/Auditor")
def credit_note_list(request):
    can_issue = (
        request.user.is_superuser
        or request.user.groups.filter(
            name__in=["Owner", "Factory Admin"]
        ).exists()
    )

    invoices = Invoice.objects.filter(
        status=Invoice.Status.ISSUED
    ).order_by("-invoice_date", "-id") if can_issue else []

    credit_notes = (
        CreditNote.objects
        .select_related("invoice", "created_by")
        .order_by("-credit_note_date", "-id")[:100]
    )

    return render(
        request,
        "sales/credit_note_list.html",
        {
            "invoices": invoices,
            "credit_notes": credit_notes,
            "can_issue": can_issue,
        },
    )


@group_required("Owner", "Factory Admin")
def credit_note_create(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.prefetch_related("lines"),
        pk=invoice_id,
    )

    if invoice.status != Invoice.Status.ISSUED:
        messages.error(
            request,
            "Credit notes can only be issued for issued invoices.",
        )
        return redirect("sales:credit_note_list")

    selected_quantities = {}
    reason = ""
    raw_date = timezone.localdate().isoformat()

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        raw_date = request.POST.get("credit_note_date", "").strip()

        raw_ids = request.POST.getlist("invoice_line_id")
        raw_quantities = request.POST.getlist("quantity")

        errors = []
        requested_lines = []

        if len(raw_ids) != len(raw_quantities):
            errors.append("Invalid invoice line submission.")
        else:
            for raw_id, raw_quantity in zip(raw_ids, raw_quantities):
                try:
                    line_id = int(raw_id)
                    quantity = int(raw_quantity or "0")
                except (ValueError, TypeError):
                    errors.append("Enter valid whole-number quantities.")
                    continue

                selected_quantities[line_id] = quantity

                if quantity != 0:
                    requested_lines.append({
                        "invoice_line_id": line_id,
                        "quantity": quantity,
                    })

        credit_date = parse_date(raw_date) if raw_date else None

        if raw_date and credit_date is None:
            errors.append("Enter a valid credit-note date.")

        if not reason:
            errors.append("A reason is required.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            try:
                credit_note = issue_credit_note(
                    invoice_id=invoice.pk,
                    user=request.user,
                    reason=reason,
                    lines=requested_lines,
                    credit_note_date=credit_date,
                )

            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)

            else:
                messages.success(
                    request,
                    f"Credit note {credit_note.credit_note_number} issued successfully.",
                )
                return redirect(
                    "sales:credit_note_detail",
                    credit_note_id=credit_note.pk,
                )

    return render(
        request,
        "sales/credit_note_create.html",
        {
            "invoice": invoice,
            "rows": _credit_note_form_rows(
                invoice,
                selected_quantities,
            ),
            "reason": reason,
            "credit_note_date": raw_date,
        },
    )


@group_required("Owner", "Factory Admin", "Accountant/Auditor")
def credit_note_detail(request, credit_note_id):
    credit_note = get_object_or_404(
        CreditNote.objects
        .select_related("invoice", "created_by")
        .prefetch_related("lines"),
        pk=credit_note_id,
    )

    return render(
        request,
        "sales/credit_note_detail.html",
        {"credit_note": credit_note},
    )