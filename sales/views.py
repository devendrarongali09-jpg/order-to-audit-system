
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

from .models import Invoice


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
