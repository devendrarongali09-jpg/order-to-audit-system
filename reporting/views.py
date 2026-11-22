
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.db.models import Sum, Q
from django.http import HttpResponseBadRequest

from inventory.models import StockMovement
from inventory.services import INCOMING_TYPES, OUTGOING_TYPES
from masterdata.models import Product, RawMaterial, Customer
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape
from sales.models import (
    SalesOrder,
    SalesOrderItem,
    Invoice,
    Payment,
    Dispatch,
    DispatchItem,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    LongTable,
    TableStyle,
    PageBreak,
)

from production.models import (
    ProductionRun,
    ProductionConsumption,
    ProductionCost,
)

from masterdata.models import Product
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

INCOMING_TYPES = {
    StockMovement.MovementType.OPENING_IN,
    StockMovement.MovementType.PURCHASE_IN,
    StockMovement.MovementType.PRODUCTION_OUTPUT,
    StockMovement.MovementType.RETURN_IN,
    StockMovement.MovementType.ADJUSTMENT_IN,
}

OUTGOING_TYPES = {
    StockMovement.MovementType.PRODUCTION_ISSUE,
    StockMovement.MovementType.DISPATCH,
    StockMovement.MovementType.ADJUSTMENT_OUT,
}


@login_required
def stock_movement_report(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    errors = []

    if raw_from and start_date is None:
        errors.append("Invalid starting date.")

    if raw_to and end_date is None:
        errors.append("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        errors.append("Ending date cannot be before starting date.")

    selected_product_id = None

    if raw_product:
        try:
            selected_product_id = int(raw_product)
        except ValueError:
            errors.append("Invalid product selection.")

    products_queryset = Product.objects.order_by("name")

    if selected_product_id is not None:

        if not products_queryset.filter(
            pk=selected_product_id
        ).exists():

            errors.append("Selected product does not exist.")

        products_queryset = products_queryset.filter(
            pk=selected_product_id
        )

    products = list(products_queryset)

    # Prepare summary for each product.
    summaries = {
        product.pk: {
            "product": product,
            "opening": 0,
            "incoming": 0,
            "outgoing": 0,
            "closing": 0,
            "unit_symbols": set(),
        }
        for product in products
    }

    movements = []

    if not errors:

        base_queryset = (
            StockMovement.objects
            .filter(product__isnull=False)
            .select_related(
                "product",
                "unit",
                "created_by",
            )
            .order_by("created_at", "pk")
        )

        if selected_product_id is not None:

            base_queryset = base_queryset.filter(
                product_id=selected_product_id
            )

        # Calculate opening balance before the selected period.
        if start_date:

            opening_movements = base_queryset.filter(
                created_at__date__lt=start_date
            )

            for movement in opening_movements:

                summary = summaries[movement.product_id]

                summary["opening"] += movement.signed_quantity

                summary["unit_symbols"].add(
                    movement.unit.symbol
                )

        # Fetch movements within the selected period.
        period_queryset = base_queryset

        if start_date:

            period_queryset = period_queryset.filter(
                created_at__date__gte=start_date
            )

        if end_date:

            period_queryset = period_queryset.filter(
                created_at__date__lte=end_date
            )

        movements = list(period_queryset)

        for movement in movements:

            summary = summaries[movement.product_id]

            summary["unit_symbols"].add(
                movement.unit.symbol
            )

            if movement.movement_type in INCOMING_TYPES:

                summary["incoming"] += movement.quantity

            elif movement.movement_type in OUTGOING_TYPES:

                summary["outgoing"] += movement.quantity

        # Calculate closing balance.
        for summary in summaries.values():

            summary["closing"] = (
                summary["opening"]
                + summary["incoming"]
                - summary["outgoing"]
            )

            summary["unit_label"] = ", ".join(
                sorted(summary["unit_symbols"])
            )

    context = {
        "products": Product.objects.order_by("name"),
        "summaries": list(summaries.values()),
        "movements": movements,
        "from_date": raw_from,
        "to_date": raw_to,
        "selected_product": raw_product,
        "errors": errors,
    }

    return render(
        request,
        "reporting/stock_movement_report.html",
        context,
    )

@login_required
def stock_movement_export_excel(request):

    # Read the same filters used by the stock report.
    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_product = None

    if raw_product:
        try:
            selected_product = Product.objects.get(pk=int(raw_product))
        except (ValueError, Product.DoesNotExist):
            return HttpResponseBadRequest("Invalid product.")

    products_queryset = Product.objects.order_by("name")

    if selected_product:
        products_queryset = products_queryset.filter(
            pk=selected_product.pk
        )

    # Prepare stock summaries.
    summaries = {
        product.pk: {
            "product": product,
            "opening": 0,
            "incoming": 0,
            "outgoing": 0,
            "closing": 0,
            "unit_symbols": set(),
        }
        for product in products_queryset
    }

    base_queryset = (
        StockMovement.objects
        .filter(product__isnull=False)
        .select_related("product", "unit", "created_by")
        .order_by("created_at", "pk")
    )

    if selected_product:
        base_queryset = base_queryset.filter(
            product_id=selected_product.pk
        )

    # Opening balance before the selected period.
    if start_date:

        opening_movements = base_queryset.filter(
            created_at__date__lt=start_date
        )

        for movement in opening_movements:

            summary = summaries[movement.product_id]

            summary["opening"] += movement.signed_quantity

            summary["unit_symbols"].add(
                movement.unit.symbol
            )

    # Movements within the selected period.
    period_queryset = base_queryset

    if start_date:
        period_queryset = period_queryset.filter(
            created_at__date__gte=start_date
        )

    if end_date:
        period_queryset = period_queryset.filter(
            created_at__date__lte=end_date
        )

    movements = list(period_queryset)

    for movement in movements:

        summary = summaries[movement.product_id]

        summary["unit_symbols"].add(
            movement.unit.symbol
        )

        if movement.movement_type in INCOMING_TYPES:
            summary["incoming"] += movement.quantity

        elif movement.movement_type in OUTGOING_TYPES:
            summary["outgoing"] += movement.quantity

    # Calculate closing balances.
    for summary in summaries.values():

        summary["closing"] = (
            summary["opening"]
            + summary["incoming"]
            - summary["outgoing"]
        )

        summary["unit_label"] = ", ".join(
            sorted(summary["unit_symbols"])
        )

    # Create Excel workbook.
    workbook = Workbook()

    # -----------------------------
    # SHEET 1: STOCK SUMMARY
    # -----------------------------

    ws = workbook.active
    ws.title = "Stock Summary"

    ws.append(["STOCK MOVEMENT AUDIT REPORT"])
    ws.append([
        "From Date",
        raw_from or "All",
        "To Date",
        raw_to or "All",
    ])
    ws.append([
        "Product",
        selected_product.name if selected_product else "All Products",
    ])

    ws.append([
        "Product",
        "SKU",
        "Unit",
        "Opening Stock",
        "Incoming",
        "Outgoing",
        "Closing Stock",
    ])

    for summary in summaries.values():

        product = summary["product"]

        ws.append([
            product.name,
            product.sku,
            summary["unit_label"],
            float(summary["opening"]),
            float(summary["incoming"]),
            float(summary["outgoing"]),
            float(summary["closing"]),
        ])

    # -----------------------------
    # SHEET 2: MOVEMENT LEDGER
    # -----------------------------

    ledger = workbook.create_sheet("Movement Ledger")

    ledger.append(["DETAILED STOCK MOVEMENT LEDGER"])
    ledger.append([
        "From Date",
        raw_from or "All",
        "To Date",
        raw_to or "All",
    ])

    ledger.append([
        "Date and Time",
        "Product",
        "SKU",
        "Movement Type",
        "Quantity",
        "Unit",
        "Reference Type",
        "Reference Number",
        "Created By",
    ])

    for movement in movements:

        created_at = timezone.localtime(
            movement.created_at
        ).replace(tzinfo=None)

        ledger.append([
            created_at,
            movement.product.name,
            movement.product.sku,
            movement.get_movement_type_display(),
            float(movement.quantity),
            movement.unit.symbol,
            movement.reference_type,
            movement.reference_number,
            (
                movement.created_by.username
                if movement.created_by else ""
            ),
        ])

    # -----------------------------
    # FORMAT BOTH WORKSHEETS
    # -----------------------------

    header_fill = PatternFill(
        fill_type="solid",
        fgColor="286D91",
    )

    white_font = Font(
        bold=True,
        color="FFFFFF",
    )

    title_font = Font(
        bold=True,
        size=16,
        color="286D91",
    )

    for sheet, header_row in (
        (ws, 4),
        (ledger, 3),
    ):

        sheet.freeze_panes = (
            f"A{header_row + 1}"
        )

        sheet.auto_filter.ref = (
            f"A{header_row}:{sheet.cell(header_row, sheet.max_column).column_letter}"
            f"{max(sheet.max_row, header_row)}"
        )

        for cell in sheet[header_row]:

            cell.fill = header_fill
            cell.font = white_font
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

        sheet.row_dimensions[header_row].height = 25

        for cell in sheet[1]:
            cell.font = title_font

        # Automatically adjust column widths.
        for column_cells in sheet.columns:

            max_length = 0

            column_letter = (
                column_cells[0].column_letter
            )

            for cell in column_cells:

                value = cell.value

                if value is not None:
                    max_length = max(
                        max_length,
                        len(str(value)),
                    )

            sheet.column_dimensions[
                column_letter
            ].width = min(max_length + 4, 35)

    # Format quantity columns.
    for row in ws.iter_rows(min_row=5, min_col=4, max_col=7):
        for cell in row:
            cell.number_format = "0.000"

    for row in ledger.iter_rows(min_row=4, min_col=5, max_col=5):
        for cell in row:
            cell.number_format = "0.000"

    for row in ledger.iter_rows(min_row=4, min_col=1, max_col=1):
        for cell in row:
            cell.number_format = "DD-MM-YYYY HH:MM"

    # Create downloadable response.
    output = BytesIO()

    workbook.save(output)

    output.seek(0)

    filename = (
        "stock_movement_audit_"
        + timezone.localtime().strftime("%Y%m%d_%H%M%S")
        + ".xlsx"
    )

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )

    return response

@login_required
def stock_movement_export_pdf(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_product = None

    if raw_product:
        try:
            selected_product = Product.objects.get(
                pk=int(raw_product)
            )
        except (ValueError, Product.DoesNotExist):
            return HttpResponseBadRequest("Invalid product.")

    products_queryset = Product.objects.order_by("name")

    if selected_product:
        products_queryset = products_queryset.filter(
            pk=selected_product.pk
        )

    summaries = {
        product.pk: {
            "product": product,
            "opening": 0,
            "incoming": 0,
            "outgoing": 0,
            "closing": 0,
            "units": set(),
        }
        for product in products_queryset
    }

    base_queryset = (
        StockMovement.objects
        .filter(product__isnull=False)
        .select_related("product", "unit", "created_by")
        .order_by("created_at", "pk")
    )

    if selected_product:
        base_queryset = base_queryset.filter(
            product_id=selected_product.pk
        )

    # Opening balance.
    if start_date:

        for movement in base_queryset.filter(
            created_at__date__lt=start_date
        ):

            summary = summaries[movement.product_id]

            summary["opening"] += movement.signed_quantity

            summary["units"].add(movement.unit.symbol)

    # Period movements.
    period_queryset = base_queryset

    if start_date:
        period_queryset = period_queryset.filter(
            created_at__date__gte=start_date
        )

    if end_date:
        period_queryset = period_queryset.filter(
            created_at__date__lte=end_date
        )

    movements = list(period_queryset)

    for movement in movements:

        summary = summaries[movement.product_id]

        summary["units"].add(movement.unit.symbol)

        if movement.movement_type in INCOMING_TYPES:
            summary["incoming"] += movement.quantity

        elif movement.movement_type in OUTGOING_TYPES:
            summary["outgoing"] += movement.quantity

    for summary in summaries.values():

        summary["closing"] = (
            summary["opening"]
            + summary["incoming"]
            - summary["outgoing"]
        )

    # Create PDF in memory.
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()

    story = []

    # Report heading.
    story.append(
        Paragraph(
            "STOCK MOVEMENT AUDIT REPORT",
            styles["Title"],
        )
    )

    story.append(Spacer(1, 6 * mm))

    product_name = (
        selected_product.name
        if selected_product
        else "All Products"
    )

    story.append(
        Paragraph(
            f"Product: {product_name}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"From: {raw_from or 'All'}"
            f" &nbsp;&nbsp; To: {raw_to or 'All'}",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # Stock summary.
    story.append(
        Paragraph(
            "1. STOCK SUMMARY",
            styles["Heading2"],
        )
    )

    summary_data = [[
        "Product",
        "SKU",
        "Unit",
        "Opening",
        "Incoming",
        "Outgoing",
        "Closing",
    ]]

    for summary in summaries.values():

        product = summary["product"]

        summary_data.append([
            product.name,
            product.sku,
            ", ".join(sorted(summary["units"])),
            f"{summary['opening']:.3f}",
            f"{summary['incoming']:.3f}",
            f"{summary['outgoing']:.3f}",
            f"{summary['closing']:.3f}",
        ])

    summary_table = Table(
        summary_data,
        repeatRows=1,
        colWidths=[
            42 * mm,
            32 * mm,
            25 * mm,
            25 * mm,
            25 * mm,
            25 * mm,
            25 * mm,
        ],
    )

    summary_table.setStyle(TableStyle([

        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#286D91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F6FA")]),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))

    story.append(summary_table)

    story.append(Spacer(1, 9 * mm))

    # Detailed movement ledger.
    story.append(
        Paragraph(
            "2. DETAILED STOCK MOVEMENT LEDGER",
            styles["Heading2"],
        )
    )

    ledger_data = [[
        "Date",
        "Product",
        "SKU",
        "Movement",
        "Quantity",
        "Unit",
        "Reference",
        "Created By",
    ]]

    for movement in movements:

        created_at = timezone.localtime(
            movement.created_at
        ).strftime("%d-%m-%Y %H:%M")

        ledger_data.append([
            created_at,
            movement.product.name,
            movement.product.sku,
            movement.get_movement_type_display(),
            f"{movement.quantity:.3f}",
            movement.unit.symbol,
            movement.reference_number,
            (
                movement.created_by.username
                if movement.created_by else "-"
            ),
        ])

    ledger_table = Table(
        ledger_data,
        repeatRows=1,
        colWidths=[
            29 * mm,
            39 * mm,
            29 * mm,
            33 * mm,
            22 * mm,
            16 * mm,
            38 * mm,
            25 * mm,
        ],
    )

    ledger_table.setStyle(TableStyle([

        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#286D91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F6FA")]),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story.append(ledger_table)

    # Build PDF.
    document.build(story)

    buffer.seek(0)

    filename = "stock_movement_audit.pdf"

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/pdf",
    )

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )

    return response

@login_required
def raw_material_movement_report(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_material_id = request.GET.get("raw_material", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    errors = []

    if raw_from and start_date is None:
        errors.append("Invalid starting date.")

    if raw_to and end_date is None:
        errors.append("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        errors.append("Ending date cannot be before starting date.")

    selected_material = None

    if raw_material_id:
        try:
            selected_material = RawMaterial.objects.get(
                pk=int(raw_material_id)
            )
        except (ValueError, RawMaterial.DoesNotExist):
            errors.append("Invalid raw material selection.")

    materials_queryset = RawMaterial.objects.order_by("name")

    if selected_material:
        materials_queryset = materials_queryset.filter(
            pk=selected_material.pk
        )

    materials = list(materials_queryset)

    # Initialize stock summaries.
    summaries = {
        material.pk: {
            "material": material,
            "opening": Decimal("0"),
            "incoming": Decimal("0"),
            "outgoing": Decimal("0"),
            "closing": Decimal("0"),
        }
        for material in materials
    }

    movements = []

    if not errors:

        base_queryset = (
            StockMovement.objects
            .filter(raw_material__isnull=False)
            .select_related(
                "raw_material",
                "unit",
                "created_by",
            )
            .order_by("created_at", "pk")
        )

        if selected_material:

            base_queryset = base_queryset.filter(
                raw_material_id=selected_material.pk
            )

        # Calculate opening stock before the selected period.
        if start_date:

            opening_movements = base_queryset.filter(
                created_at__date__lt=start_date
            )

            for movement in opening_movements:

                summary = summaries[movement.raw_material_id]

                summary["opening"] += movement.signed_quantity

        # Get movements within the selected period.
        period_queryset = base_queryset

        if start_date:

            period_queryset = period_queryset.filter(
                created_at__date__gte=start_date
            )

        if end_date:

            period_queryset = period_queryset.filter(
                created_at__date__lte=end_date
            )

        movements = list(period_queryset)

        for movement in movements:

            summary = summaries[movement.raw_material_id]

            if movement.movement_type in INCOMING_TYPES:

                summary["incoming"] += movement.quantity

            elif movement.movement_type in OUTGOING_TYPES:

                summary["outgoing"] += movement.quantity

        # Calculate closing stock.
        for summary in summaries.values():

            summary["closing"] = (
                summary["opening"]
                + summary["incoming"]
                - summary["outgoing"]
            )

    context = {
        "materials": RawMaterial.objects.order_by("name"),
        "summaries": list(summaries.values()),
        "movements": movements,
        "from_date": raw_from,
        "to_date": raw_to,
        "selected_material": raw_material_id,
        "errors": errors,
    }

    return render(
        request,
        "reporting/raw_material_report.html",
        context,
    )

@login_required
def raw_material_export_excel(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_material_id = request.GET.get("raw_material", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_material = None

    if raw_material_id:
        try:
            selected_material = RawMaterial.objects.get(
                pk=int(raw_material_id)
            )
        except (ValueError, RawMaterial.DoesNotExist):
            return HttpResponseBadRequest("Invalid raw material.")

    materials_queryset = RawMaterial.objects.order_by("name")

    if selected_material:
        materials_queryset = materials_queryset.filter(
            pk=selected_material.pk
        )

    summaries = {
        material.pk: {
            "material": material,
            "opening": Decimal("0"),
            "incoming": Decimal("0"),
            "outgoing": Decimal("0"),
            "closing": Decimal("0"),
        }
        for material in materials_queryset
    }

    base_queryset = (
        StockMovement.objects
        .filter(raw_material__isnull=False)
        .select_related("raw_material", "unit", "created_by")
        .order_by("created_at", "pk")
    )

    if selected_material:
        base_queryset = base_queryset.filter(
            raw_material_id=selected_material.pk
        )

    # Calculate opening stock.
    if start_date:

        for movement in base_queryset.filter(
            created_at__date__lt=start_date
        ):
            summary = summaries[movement.raw_material_id]
            summary["opening"] += movement.signed_quantity

    # Get movements within the selected period.
    period_queryset = base_queryset

    if start_date:
        period_queryset = period_queryset.filter(
            created_at__date__gte=start_date
        )

    if end_date:
        period_queryset = period_queryset.filter(
            created_at__date__lte=end_date
        )

    movements = list(period_queryset)

    for movement in movements:

        summary = summaries[movement.raw_material_id]

        if movement.movement_type in INCOMING_TYPES:
            summary["incoming"] += movement.quantity

        elif movement.movement_type in OUTGOING_TYPES:
            summary["outgoing"] += movement.quantity

    # Calculate closing stock.
    for summary in summaries.values():

        summary["closing"] = (
            summary["opening"]
            + summary["incoming"]
            - summary["outgoing"]
        )

    # Create workbook.
    workbook = Workbook()

    # SHEET 1: RAW MATERIAL SUMMARY
    ws = workbook.active
    ws.title = "Raw Material Summary"

    ws.append(["RAW MATERIAL AUDIT REPORT"])

    ws.append([
        "From Date",
        raw_from or "All",
        "To Date",
        raw_to or "All",
    ])

    ws.append([
        "Raw Material",
        selected_material.name if selected_material else "All Materials",
    ])

    ws.append([
        "Raw Material",
        "SKU",
        "Category",
        "Unit",
        "Opening Stock",
        "Incoming",
        "Outgoing",
        "Closing Stock",
    ])

    for summary in summaries.values():

        material = summary["material"]

        ws.append([
            material.name,
            material.sku,
            material.get_category_display(),
            material.unit.symbol,
            float(summary["opening"]),
            float(summary["incoming"]),
            float(summary["outgoing"]),
            float(summary["closing"]),
        ])

    # SHEET 2: RAW MATERIAL MOVEMENT LEDGER
    ledger = workbook.create_sheet("Movement Ledger")

    ledger.append(["DETAILED RAW MATERIAL MOVEMENT LEDGER"])

    ledger.append([
        "From Date",
        raw_from or "All",
        "To Date",
        raw_to or "All",
    ])

    ledger.append([
        "Date and Time",
        "Raw Material",
        "SKU",
        "Movement Type",
        "Quantity",
        "Unit",
        "Unit Cost",
        "Reference Type",
        "Reference Number",
        "Notes",
        "Created By",
    ])

    for movement in movements:

        created_at = timezone.localtime(
            movement.created_at
        ).replace(tzinfo=None)

        ledger.append([
            created_at,
            movement.raw_material.name,
            movement.raw_material.sku,
            movement.get_movement_type_display(),
            float(movement.quantity),
            movement.unit.symbol,
            (
                float(movement.unit_cost)
                if movement.unit_cost is not None
                else None
            ),
            movement.reference_type,
            movement.reference_number,
            movement.notes,
            (
                movement.created_by.username
                if movement.created_by else ""
            ),
        ])

    # Format workbook.
    header_fill = PatternFill(
        fill_type="solid",
        fgColor="286D91",
    )

    white_font = Font(
        bold=True,
        color="FFFFFF",
    )

    title_font = Font(
        bold=True,
        size=16,
        color="286D91",
    )

    for sheet, header_row in (
        (ws, 4),
        (ledger, 3),
    ):

        sheet.freeze_panes = f"A{header_row + 1}"

        sheet.auto_filter.ref = (
            f"A{header_row}:"
            f"{sheet.cell(header_row, sheet.max_column).column_letter}"
            f"{max(sheet.max_row, header_row)}"
        )

        for cell in sheet[header_row]:
            cell.fill = header_fill
            cell.font = white_font
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

        sheet.row_dimensions[header_row].height = 25

        for cell in sheet[1]:
            cell.font = title_font

        # Automatic column widths.
        for column_cells in sheet.columns:

            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:

                if cell.value is not None:
                    max_length = max(
                        max_length,
                        len(str(cell.value)),
                    )

            sheet.column_dimensions[column_letter].width = min(
                max_length + 4,
                40,
            )

    # Format summary quantities.
    for row in ws.iter_rows(
        min_row=5,
        min_col=5,
        max_col=8,
    ):
        for cell in row:
            cell.number_format = "0.000"

    # Format ledger date, quantity and unit cost.
    for row in ledger.iter_rows(
        min_row=4,
        min_col=1,
        max_col=1,
    ):
        for cell in row:
            cell.number_format = "DD-MM-YYYY HH:MM"

    for row in ledger.iter_rows(
        min_row=4,
        min_col=5,
        max_col=5,
    ):
        for cell in row:
            cell.number_format = "0.000"

    for row in ledger.iter_rows(
        min_row=4,
        min_col=7,
        max_col=7,
    ):
        for cell in row:
            cell.number_format = '"₹"#,##0.0000'

    # Generate downloadable Excel response.
    output = BytesIO()

    workbook.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        'attachment; filename="raw_material_audit.xlsx"'
    )

    return response

@login_required
def raw_material_export_pdf(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_material_id = request.GET.get("raw_material", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_material = None

    if raw_material_id:
        try:
            selected_material = RawMaterial.objects.get(
                pk=int(raw_material_id)
            )
        except (ValueError, RawMaterial.DoesNotExist):
            return HttpResponseBadRequest("Invalid raw material.")

    materials_queryset = RawMaterial.objects.order_by("name")

    if selected_material:
        materials_queryset = materials_queryset.filter(
            pk=selected_material.pk
        )

    summaries = {
        material.pk: {
            "material": material,
            "opening": Decimal("0"),
            "incoming": Decimal("0"),
            "outgoing": Decimal("0"),
            "closing": Decimal("0"),
        }
        for material in materials_queryset
    }

    base_queryset = (
        StockMovement.objects
        .filter(raw_material__isnull=False)
        .select_related("raw_material", "unit", "created_by")
        .order_by("created_at", "pk")
    )

    if selected_material:
        base_queryset = base_queryset.filter(
            raw_material_id=selected_material.pk
        )

    # Calculate opening balance.
    if start_date:

        opening_movements = base_queryset.filter(
            created_at__date__lt=start_date
        )

        for movement in opening_movements:

            summary = summaries[movement.raw_material_id]

            summary["opening"] += movement.signed_quantity

    # Get movements within selected period.
    period_queryset = base_queryset

    if start_date:
        period_queryset = period_queryset.filter(
            created_at__date__gte=start_date
        )

    if end_date:
        period_queryset = period_queryset.filter(
            created_at__date__lte=end_date
        )

    movements = list(period_queryset)

    for movement in movements:

        summary = summaries[movement.raw_material_id]

        if movement.movement_type in INCOMING_TYPES:
            summary["incoming"] += movement.quantity

        elif movement.movement_type in OUTGOING_TYPES:
            summary["outgoing"] += movement.quantity

    # Calculate closing stock.
    for summary in summaries.values():

        summary["closing"] = (
            summary["opening"]
            + summary["incoming"]
            - summary["outgoing"]
        )

    # Create PDF in memory.
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=8 * mm,
        leftMargin=8 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "AuditBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
    )

    header_style = ParagraphStyle(
        "AuditHeader",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        fontSize=7,
        leading=8,
    )

    story = []

    # Report heading.
    story.append(
        Paragraph(
            "RAW MATERIAL AUDIT REPORT",
            styles["Title"],
        )
    )

    story.append(Spacer(1, 4 * mm))

    product_name = (
        selected_material.name
        if selected_material
        else "All Raw Materials"
    )

    story.append(
        Paragraph(
            f"Raw Material: {escape(product_name)}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"From Date: {raw_from or 'All'}"
            f" &nbsp;&nbsp;&nbsp; To Date: {raw_to or 'All'}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"Generated: {timezone.localtime().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # -----------------------------
    # 1. STOCK SUMMARY
    # -----------------------------

    story.append(
        Paragraph(
            "1. RAW MATERIAL STOCK SUMMARY",
            styles["Heading2"],
        )
    )

    summary_data = [[
        "Raw Material",
        "SKU",
        "Category",
        "Unit",
        "Opening",
        "Incoming",
        "Outgoing",
        "Closing",
    ]]

    for summary in summaries.values():

        material = summary["material"]

        summary_data.append([
            Paragraph(escape(material.name), body_style),
            Paragraph(escape(material.sku), body_style),
            Paragraph(
                escape(material.get_category_display()),
                body_style,
            ),
            material.unit.symbol,
            f"{summary['opening']:.3f}",
            f"{summary['incoming']:.3f}",
            f"{summary['outgoing']:.3f}",
            f"{summary['closing']:.3f}",
        ])

    summary_table = LongTable(
        summary_data,
        repeatRows=1,
        colWidths=[
            43 * mm,
            32 * mm,
            28 * mm,
            18 * mm,
            31 * mm,
            31 * mm,
            31 * mm,
            31 * mm,
        ],
    )

    summary_table.setStyle(TableStyle([

        ("BACKGROUND", (0, 0), (-1, 0),
         colors.HexColor("#286D91")),

        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),

        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

        ("FONTSIZE", (0, 0), (-1, -1), 7),

        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),

        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F6FA")]),

        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),

        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

        ("LEFTPADDING", (0, 0), (-1, -1), 5),

        ("RIGHTPADDING", (0, 0), (-1, -1), 5),

        ("TOPPADDING", (0, 0), (-1, -1), 7),

        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    story.append(summary_table)

    story.append(Spacer(1, 9 * mm))

    # -----------------------------
    # 2. DETAILED MOVEMENT LEDGER
    # -----------------------------

    story.append(
        Paragraph(
            "2. DETAILED RAW MATERIAL MOVEMENT LEDGER",
            styles["Heading2"],
        )
    )

    ledger_data = [[
        Paragraph("Date", header_style),
        Paragraph("Material", header_style),
        Paragraph("SKU", header_style),
        Paragraph("Movement", header_style),
        Paragraph("Quantity", header_style),
        Paragraph("Unit", header_style),
        Paragraph("Unit Cost", header_style),
        Paragraph("Reference Type", header_style),
        Paragraph("Reference No.", header_style),
        Paragraph("Notes", header_style),
        Paragraph("Created By", header_style),
    ]]

    for movement in movements:

        created_at = timezone.localtime(
            movement.created_at
        ).strftime("%d-%m-%Y %H:%M")

        unit_cost = (
            f"Rs. {movement.unit_cost:.4f}"
            if movement.unit_cost is not None
            else "-"
        )

        ledger_data.append([
            Paragraph(escape(created_at), body_style),

            Paragraph(
                escape(movement.raw_material.name),
                body_style,
            ),

            Paragraph(
                escape(movement.raw_material.sku),
                body_style,
            ),

            Paragraph(
                escape(movement.get_movement_type_display()),
                body_style,
            ),

            f"{movement.quantity:.3f}",

            escape(movement.unit.symbol),

            unit_cost,

            Paragraph(
                escape(movement.reference_type),
                body_style,
            ),

            Paragraph(
                escape(movement.reference_number or "-"),
                body_style,
            ),

            Paragraph(
                escape(movement.notes or "-"),
                body_style,
            ),

            Paragraph(
                escape(
                    movement.created_by.username
                    if movement.created_by else "-"
                ),
                body_style,
            ),
        ])

    ledger_table = LongTable(
        ledger_data,
        repeatRows=1,
        colWidths=[
            24 * mm,
            32 * mm,
            23 * mm,
            27 * mm,
            17 * mm,
            12 * mm,
            23 * mm,
            24 * mm,
            25 * mm,
            38 * mm,
            23 * mm,
        ],
    )

    ledger_table.setStyle(TableStyle([

        ("BACKGROUND", (0, 0), (-1, 0),
         colors.HexColor("#286D91")),

        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),

        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

        ("FONTSIZE", (0, 0), (-1, -1), 7),

        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),

        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F2F6FA")]),

        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

        ("ALIGN", (4, 1), (6, -1), "RIGHT"),

        ("LEFTPADDING", (0, 0), (-1, -1), 4),

        ("RIGHTPADDING", (0, 0), (-1, -1), 4),

        ("TOPPADDING", (0, 0), (-1, -1), 6),

        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(ledger_table)

    # Page number footer.
    def add_page_number(canvas, doc):

        canvas.saveState()

        canvas.setFont("Helvetica", 8)

        canvas.setFillColor(colors.grey)

        canvas.drawRightString(
            landscape(A4)[0] - 10 * mm,
            7 * mm,
            f"Page {doc.page}",
        )

        canvas.restoreState()

    document.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )

    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/pdf",
    )

    response["Content-Disposition"] = (
        'attachment; filename="raw_material_audit.pdf"'
    )

    return response

@login_required
def sales_audit_report(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_customer = request.GET.get("customer", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    errors = []

    if raw_from and start_date is None:
        errors.append("Invalid starting date.")

    if raw_to and end_date is None:
        errors.append("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        errors.append("Ending date cannot be before starting date.")

    selected_customer = None

    if raw_customer:
        try:
            selected_customer = Customer.objects.get(
                pk=int(raw_customer)
            )
        except (ValueError, Customer.DoesNotExist):
            errors.append("Invalid customer selection.")

    # Fetch customers for the filter.
    customers = Customer.objects.order_by("name")

    # Fetch sales orders and related records efficiently.
    orders_queryset = (
        SalesOrder.objects
        .select_related(
            "customer",
            "created_by",
            "invoice",
        )
        .prefetch_related(
            "items__product",
            "invoice__payments__received_by",
            "dispatches__items__order_item__product",
            "dispatches__posted_by",
        )
        .order_by("-order_date", "-pk")
    )

    if start_date:
        orders_queryset = orders_queryset.filter(
            order_date__gte=start_date
        )

    if end_date:
        orders_queryset = orders_queryset.filter(
            order_date__lte=end_date
        )

    if selected_customer:
        orders_queryset = orders_queryset.filter(
            customer=selected_customer
        )

    orders = list(orders_queryset) if not errors else []

    # Initialize report data.
    report_rows = []
    payment_ledger = []
    dispatch_ledger = []

    total_order_value = Decimal("0.00")
    total_invoice_value = Decimal("0.00")
    total_received = Decimal("0.00")
    total_outstanding = Decimal("0.00")

    posted_dispatch_count = 0
    cancelled_invoice_count = 0

    for order in orders:

        # Calculate sales order value before GST.
        order_items = list(order.items.all())

        order_value = sum(
            (
                item.line_total
                for item in order_items
            ),
            Decimal("0.00"),
        )

        total_order_value += order_value

        # Get invoice if available.
        try:
            invoice = order.invoice
        except Invoice.DoesNotExist:
            invoice = None

        amount_paid = Decimal("0.00")
        balance_due = Decimal("0.00")
        payment_status = "Not invoiced"

        if invoice:

            payments = list(invoice.payments.all())

            # Only received payments count as collections.
            received_payments = [
                payment
                for payment in payments
                if payment.status == Payment.Status.RECEIVED
            ]

            amount_paid = sum(
                (
                    payment.amount
                    for payment in received_payments
                ),
                Decimal("0.00"),
            )

            total_received += amount_paid

            if invoice.status == Invoice.Status.ISSUED:

                total_invoice_value += invoice.grand_total

                balance_due = invoice.balance_due

                total_outstanding += balance_due

                payment_status = invoice.payment_status

            elif invoice.status == Invoice.Status.CANCELLED:

                cancelled_invoice_count += 1

                payment_status = "Invoice Cancelled"

            # Build detailed payment ledger.
            for payment in payments:

                payment_ledger.append({
                    "order_number": order.order_number,
                    "customer": order.customer.name,
                    "invoice_number": invoice.invoice_number,
                    "payment": payment,
                })

        # Calculate dispatch quantities.
        dispatches = list(order.dispatches.all())

        posted_dispatches = [
            dispatch
            for dispatch in dispatches
            if dispatch.status == Dispatch.Status.POSTED
        ]

        dispatched_quantity = sum(
            (
                item.quantity
                for dispatch in posted_dispatches
                for item in dispatch.items.all()
            ),
            0,
        )

        posted_dispatch_count += len(posted_dispatches)

        # Detailed dispatch ledger.
        dispatch_details = []

        for dispatch in dispatches:

            dispatch_items = list(dispatch.items.all())

            dispatch_quantity = sum(
                item.quantity
                for item in dispatch_items
            )

            dispatch_info = {
                "dispatch_number": dispatch.dispatch_number,
                "dispatch_date": dispatch.dispatch_date,
                "status": dispatch.get_status_display(),
                "status_code": dispatch.status,
                "quantity": dispatch_quantity,
                "posted_by": (
                    dispatch.posted_by.username
                    if dispatch.posted_by else "-"
                ),
            }

            dispatch_details.append(dispatch_info)

            dispatch_ledger.append({
                "order_number": order.order_number,
                "customer": order.customer.name,
                **dispatch_info,
            })

        # Complete order audit row.
        report_rows.append({
            "order": order,
            "items": order_items,
            "order_value": order_value,
            "invoice": invoice,
            "amount_paid": amount_paid,
            "balance_due": balance_due,
            "payment_status": payment_status,
            "dispatches": dispatch_details,
            "posted_dispatch_count": len(posted_dispatches),
            "dispatched_quantity": dispatched_quantity,
        })

    # Final report summary.
    summary = {
        "order_count": len(orders),
        "order_value": total_order_value,
        "invoice_value": total_invoice_value,
        "received": total_received,
        "outstanding": total_outstanding,
        "posted_dispatches": posted_dispatch_count,
        "cancelled_invoices": cancelled_invoice_count,
    }

    context = {
        "customers": customers,
        "selected_customer": raw_customer,
        "from_date": raw_from,
        "to_date": raw_to,
        "errors": errors,
        "report_rows": report_rows,
        "payment_ledger": payment_ledger,
        "dispatch_ledger": dispatch_ledger,
        "summary": summary,
    }

    return render(
        request,
        "reporting/sales_audit_report.html",
        context,
    )

@login_required
def sales_audit_export_excel(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_customer = request.GET.get("customer", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_customer = None

    if raw_customer:
        try:
            selected_customer = Customer.objects.get(
                pk=int(raw_customer)
            )
        except (ValueError, Customer.DoesNotExist):
            return HttpResponseBadRequest("Invalid customer.")

    orders_queryset = (
        SalesOrder.objects
        .select_related(
            "customer",
            "created_by",
            "invoice",
        )
        .prefetch_related(
            "items__product",
            "invoice__payments__received_by",
            "dispatches__items__order_item__product",
            "dispatches__posted_by",
        )
        .order_by("-order_date", "-pk")
    )

    if start_date:
        orders_queryset = orders_queryset.filter(
            order_date__gte=start_date
        )

    if end_date:
        orders_queryset = orders_queryset.filter(
            order_date__lte=end_date
        )

    if selected_customer:
        orders_queryset = orders_queryset.filter(
            customer=selected_customer
        )

    orders = list(orders_queryset)

    # Workbook totals.
    total_order_value = Decimal("0.00")
    total_invoice_value = Decimal("0.00")
    total_received = Decimal("0.00")
    total_outstanding = Decimal("0.00")
    posted_dispatch_count = 0
    cancelled_invoice_count = 0

    # Create workbook.
    workbook = Workbook()

    # =================================
    # SHEET 1: SALES SUMMARY
    # =================================

    summary_ws = workbook.active
    summary_ws.title = "Sales Summary"

    summary_ws.append(["SALES AUDIT REPORT"])

    summary_ws.append([
        "From Date",
        raw_from or "All",
        "To Date",
        raw_to or "All",
    ])

    summary_ws.append([
        "Customer",
        selected_customer.name if selected_customer else "All Customers",
    ])

    # Calculate summary.
    order_data = []
    invoice_data = []
    payment_data = []
    dispatch_data = []

    def safe_text(value):

        value = str(value or "")

        if value.startswith(("=", "+", "-", "@")):
            return "'" + value

        return value

    for order in orders:

        items = list(order.items.all())

        order_value = sum(
            (item.line_total for item in items),
            Decimal("0.00"),
        )

        total_order_value += order_value

        product_details = ", ".join(
            f"{safe_text(item.product.name)} x {item.quantity}"
            for item in items
        )

        invoice = None

        try:
            invoice = order.invoice
        except Invoice.DoesNotExist:
            pass

        amount_paid = Decimal("0.00")
        balance_due = Decimal("0.00")
        payment_status = "Not invoiced"

        if invoice:

            payments = list(invoice.payments.all())

            received_payments = [
                p for p in payments
                if p.status == Payment.Status.RECEIVED
            ]

            amount_paid = sum(
                (p.amount for p in received_payments),
                Decimal("0.00"),
            )

            total_received += amount_paid

            if invoice.status == Invoice.Status.ISSUED:

                total_invoice_value += invoice.grand_total

                balance_due = invoice.balance_due

                total_outstanding += balance_due

                payment_status = invoice.payment_status

            else:
                cancelled_invoice_count += 1
                payment_status = "Invoice Cancelled"

            invoice_data.append([
                safe_text(order.order_number),
                safe_text(order.customer.name),
                safe_text(invoice.invoice_number),
                invoice.invoice_date,
                safe_text(invoice.get_status_display()),
                float(invoice.grand_total),
                float(amount_paid),
                float(balance_due),
                safe_text(payment_status),
            ])

            for payment in payments:

                payment_data.append([
                    safe_text(order.order_number),
                    safe_text(order.customer.name),
                    safe_text(invoice.invoice_number),
                    payment.payment_date,
                    float(payment.amount),
                    safe_text(payment.get_method_display()),
                    safe_text(payment.get_status_display()),
                    safe_text(payment.transaction_reference),
                    safe_text(payment.received_by.username),
                    safe_text(payment.notes),
                ])

        # Sales order record.
        order_data.append([
            safe_text(order.order_number),
            safe_text(order.customer.name),
            order.order_date,
            safe_text(product_details),
            float(order_value),
            safe_text(order.get_status_display()),
            safe_text(invoice.invoice_number if invoice else "Not Generated"),
            safe_text(invoice.get_status_display() if invoice else "-"),
            safe_text(order.created_by.username),
        ])

        # Dispatch ledger.
        for dispatch in order.dispatches.all():

            dispatch_items = list(dispatch.items.all())

            dispatch_quantity = sum(
                item.quantity for item in dispatch_items
            )

            if dispatch.status == Dispatch.Status.POSTED:
                posted_dispatch_count += 1

            dispatch_data.append([
                safe_text(order.order_number),
                safe_text(order.customer.name),
                safe_text(dispatch.dispatch_number),
                dispatch.dispatch_date,
                dispatch_quantity,
                safe_text(dispatch.get_status_display()),
                safe_text(
                    dispatch.posted_by.username
                    if dispatch.posted_by else "-"
                ),
                safe_text(dispatch.notes),
            ])

    # Populate summary.
    summary_ws.append(["Metric", "Value"])

    metrics = [
        ("Total Sales Orders", len(orders)),
        ("Total Order Value", float(total_order_value)),
        ("Invoice Value (Issued)", float(total_invoice_value)),
        ("Payments Received", float(total_received)),
        ("Outstanding Balance", float(total_outstanding)),
        ("Posted Dispatches", posted_dispatch_count),
        ("Cancelled Invoices", cancelled_invoice_count),
    ]

    for metric, value in metrics:
        summary_ws.append([metric, value])

    # =================================
    # SHEET 2: SALES ORDERS
    # =================================

    orders_ws = workbook.create_sheet("Sales Orders")

    orders_ws.append(["SALES ORDER AUDIT"])

    orders_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    orders_ws.append([
        "Order Number",
        "Customer",
        "Order Date",
        "Products",
        "Order Value",
        "Order Status",
        "Invoice Number",
        "Invoice Status",
        "Created By",
    ])

    for row in order_data:
        orders_ws.append(row)

    # =================================
    # SHEET 3: INVOICE RECONCILIATION
    # =================================

    invoice_ws = workbook.create_sheet("Invoices")

    invoice_ws.append(["INVOICE RECONCILIATION"])

    invoice_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    invoice_ws.append([
        "Order Number",
        "Customer",
        "Invoice Number",
        "Invoice Date",
        "Invoice Status",
        "Grand Total",
        "Amount Paid",
        "Balance Due",
        "Payment Status",
    ])

    for row in invoice_data:
        invoice_ws.append(row)

    # =================================
    # SHEET 4: PAYMENT LEDGER
    # =================================

    payment_ws = workbook.create_sheet("Payment Ledger")

    payment_ws.append(["DETAILED PAYMENT LEDGER"])

    payment_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    payment_ws.append([
        "Order Number",
        "Customer",
        "Invoice Number",
        "Payment Date",
        "Amount",
        "Method",
        "Status",
        "Transaction Reference",
        "Received By",
        "Notes",
    ])

    for row in payment_data:
        payment_ws.append(row)

    # =================================
    # SHEET 5: DISPATCH LEDGER
    # =================================

    dispatch_ws = workbook.create_sheet("Dispatch Ledger")

    dispatch_ws.append(["DETAILED DISPATCH LEDGER"])

    dispatch_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    dispatch_ws.append([
        "Order Number",
        "Customer",
        "Dispatch Number",
        "Dispatch Date",
        "Quantity",
        "Status",
        "Posted By",
        "Notes",
    ])

    for row in dispatch_data:
        dispatch_ws.append(row)

    # =================================
    # PROFESSIONAL FORMATTING
    # =================================

    header_fill = PatternFill(
        fill_type="solid",
        fgColor="286D91",
    )

    white_font = Font(
        bold=True,
        color="FFFFFF",
    )

    title_font = Font(
        bold=True,
        size=16,
        color="286D91",
    )

    # Header rows in the five sheets.
    sheet_headers = [
        (summary_ws, 4),
        (orders_ws, 3),
        (invoice_ws, 3),
        (payment_ws, 3),
        (dispatch_ws, 3),
    ]

    for sheet, header_row in sheet_headers:

        sheet.freeze_panes = f"A{header_row + 1}"

        for cell in sheet[header_row]:

            cell.fill = header_fill
            cell.font = white_font

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

        sheet.row_dimensions[header_row].height = 25

        if sheet.max_row >= header_row:
            sheet.auto_filter.ref = (
                f"A{header_row}:"
                f"{sheet.cell(header_row, sheet.max_column).column_letter}"
                f"{sheet.max_row}"
            )

        for cell in sheet[1]:
            cell.font = title_font

        # Adjust column widths.
        for column_cells in sheet.columns:

            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:

                if cell.value is not None:
                    max_length = max(
                        max_length,
                        len(str(cell.value)),
                    )

            sheet.column_dimensions[column_letter].width = min(
                max_length + 4,
                45,
            )

    # Currency formatting.
    for row in summary_ws.iter_rows(min_row=5, min_col=2, max_col=2):
        for cell in row:
            if isinstance(cell.value, (int, float)) and (
                cell.row in (6, 7, 8, 9)
            ):
                cell.number_format = '"₹"#,##0.00'

    for sheet, columns in [
        (orders_ws, [5]),
        (invoice_ws, [6, 7, 8]),
        (payment_ws, [5]),
    ]:

        for column in columns:
            for row in sheet.iter_rows(
                min_row=4,
                min_col=column,
                max_col=column,
            ):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        cell.number_format = '"₹"#,##0.00'

    # Date formatting.
    for sheet, column, start_row in [
        (orders_ws, 3, 4),
        (invoice_ws, 4, 4),
        (payment_ws, 4, 4),
        (dispatch_ws, 4, 4),
    ]:

        for row in sheet.iter_rows(
            min_row=start_row,
            min_col=column,
            max_col=column,
        ):
            for cell in row:
                cell.number_format = "DD-MM-YYYY"

    # Generate downloadable workbook.
    output = BytesIO()

    workbook.save(output)
    output.seek(0)

    filename = "sales_audit_report.xlsx"

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )

    return response

@login_required
def sales_audit_export_pdf(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_customer = request.GET.get("customer", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_customer = None

    if raw_customer:
        try:
            selected_customer = Customer.objects.get(
                pk=int(raw_customer)
            )
        except (ValueError, Customer.DoesNotExist):
            return HttpResponseBadRequest("Invalid customer.")

    orders_queryset = (
        SalesOrder.objects
        .select_related(
            "customer",
            "created_by",
            "invoice",
        )
        .prefetch_related(
            "items__product",
            "invoice__payments__received_by",
            "dispatches__items__order_item__product",
            "dispatches__posted_by",
        )
        .order_by("-order_date", "-pk")
    )

    if start_date:
        orders_queryset = orders_queryset.filter(
            order_date__gte=start_date
        )

    if end_date:
        orders_queryset = orders_queryset.filter(
            order_date__lte=end_date
        )

    if selected_customer:
        orders_queryset = orders_queryset.filter(
            customer=selected_customer
        )

    orders = list(orders_queryset)

    # Report totals.
    total_order_value = Decimal("0.00")
    total_invoice_value = Decimal("0.00")
    total_received = Decimal("0.00")
    total_outstanding = Decimal("0.00")

    posted_dispatch_count = 0
    cancelled_invoice_count = 0

    # Prepare PDF styles.
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "SalesAuditBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
    )

    header_style = ParagraphStyle(
        "SalesAuditHeader",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        fontSize=7,
        leading=8,
    )

    story = []

    # Helper for safe text and table cells.
    def cell(value, style=body_style):
        return Paragraph(
            escape(str(value if value is not None else "-")),
            style,
        )

    def money(value):
        return f"Rs. {value:,.2f}"

    def make_table(data, widths):

        table = LongTable(
            data,
            colWidths=widths,
            repeatRows=1,
            hAlign="LEFT",
        )

        table.setStyle(TableStyle([

            ("BACKGROUND", (0, 0), (-1, 0),
             colors.HexColor("#286D91")),

            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),

            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

            ("FONTSIZE", (0, 0), (-1, -1), 7),

            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),

            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F2F6FA")]),

            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

            ("LEFTPADDING", (0, 0), (-1, -1), 4),

            ("RIGHTPADDING", (0, 0), (-1, -1), 4),

            ("TOPPADDING", (0, 0), (-1, -1), 6),

            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),

        ]))

        return table

    # ------------------------------------------------
    # REPORT HEADING
    # ------------------------------------------------

    story.append(
        Paragraph(
            "SALES AUDIT REPORT",
            styles["Title"],
        )
    )

    story.append(Spacer(1, 4 * mm))

    story.append(
        Paragraph(
            f"Customer: {escape(selected_customer.name if selected_customer else 'All Customers')}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"From Date: {raw_from or 'All'}"
            f" &nbsp;&nbsp;&nbsp; To Date: {raw_to or 'All'}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"Generated: {timezone.localtime().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 6 * mm))

    # ------------------------------------------------
    # COLLECT AUDIT DATA
    # ------------------------------------------------

    order_data = []
    invoice_data = []
    payment_data = []
    dispatch_data = []

    for order in orders:

        items = list(order.items.all())

        order_value = sum(
            (item.line_total for item in items),
            Decimal("0.00"),
        )

        total_order_value += order_value

        products = ", ".join(
            f"{item.product.name} x {item.quantity}"
            for item in items
        )

        invoice = None

        try:
            invoice = order.invoice
        except Invoice.DoesNotExist:
            pass

        amount_paid = Decimal("0.00")
        balance_due = Decimal("0.00")
        payment_status = "Not invoiced"

        if invoice:

            payments = list(invoice.payments.all())

            received_payments = [
                payment for payment in payments
                if payment.status == Payment.Status.RECEIVED
            ]

            amount_paid = sum(
                (payment.amount for payment in received_payments),
                Decimal("0.00"),
            )

            total_received += amount_paid

            if invoice.status == Invoice.Status.ISSUED:

                total_invoice_value += invoice.grand_total

                balance_due = invoice.balance_due

                total_outstanding += balance_due

                payment_status = invoice.payment_status

            elif invoice.status == Invoice.Status.CANCELLED:

                cancelled_invoice_count += 1

                payment_status = "Invoice Cancelled"

            invoice_data.append([
                cell(order.order_number),
                cell(order.customer.name),
                cell(invoice.invoice_number),
                cell(invoice.invoice_date.strftime("%d-%m-%Y")),
                cell(invoice.get_status_display()),
                cell(money(invoice.grand_total)),
                cell(money(amount_paid)),
                cell(money(balance_due)),
                cell(payment_status),
            ])

            for payment in payments:

                payment_data.append([
                    cell(order.order_number),
                    cell(order.customer.name),
                    cell(invoice.invoice_number),
                    cell(payment.payment_date.strftime("%d-%m-%Y")),
                    cell(money(payment.amount)),
                    cell(payment.get_method_display()),
                    cell(payment.get_status_display()),
                    cell(payment.transaction_reference),
                    cell(
                        payment.received_by.username
                        if payment.received_by else "-"
                    ),
                ])

        order_data.append([
            cell(order.order_number),
            cell(order.customer.name),
            cell(order.order_date.strftime("%d-%m-%Y")),
            cell(products),
            cell(money(order_value)),
            cell(order.get_status_display()),
            cell(
                invoice.invoice_number if invoice else "Not Generated"
            ),
            cell(order.created_by.username),
        ])

        # Dispatch records.
        for dispatch in order.dispatches.all():

            dispatch_items = list(dispatch.items.all())

            quantity = sum(
                item.quantity for item in dispatch_items
            )

            if dispatch.status == Dispatch.Status.POSTED:
                posted_dispatch_count += 1

            dispatch_data.append([
                cell(order.order_number),
                cell(order.customer.name),
                cell(dispatch.dispatch_number),
                cell(dispatch.dispatch_date.strftime("%d-%m-%Y")),
                cell(quantity),
                cell(dispatch.get_status_display()),
                cell(
                    dispatch.posted_by.username
                    if dispatch.posted_by else "-"
                ),
                cell(dispatch.notes),
            ])

    # ------------------------------------------------
    # 1. SALES SUMMARY
    # ------------------------------------------------

    story.append(
        Paragraph(
            "1. SALES AUDIT SUMMARY",
            styles["Heading2"],
        )
    )

    summary_data = [
        ["Metric", "Value", "Metric", "Value"],

        [
            "Total Sales Orders",
            str(len(orders)),
            "Total Order Value",
            money(total_order_value),
        ],

        [
            "Invoice Value (Issued)",
            money(total_invoice_value),
            "Payments Received",
            money(total_received),
        ],

        [
            "Outstanding Balance",
            money(total_outstanding),
            "Posted Dispatches",
            str(posted_dispatch_count),
        ],

        [
            "Cancelled Invoices",
            str(cancelled_invoice_count),
            "Customer Filter",
            selected_customer.name if selected_customer else "All Customers",
        ],
    ]

    story.append(
        make_table(
            [
                [cell(v, header_style) for v in row]
                if index == 0
                else [cell(v) for v in row]
                for index, row in enumerate(summary_data)
            ],
            [50 * mm, 55 * mm, 50 * mm, 65 * mm],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # ------------------------------------------------
    # 2. SALES ORDER AUDIT
    # ------------------------------------------------

    story.append(
        Paragraph(
            "2. SALES ORDER AUDIT",
            styles["Heading2"],
        )
    )

    order_headers = [
        "Order Number",
        "Customer",
        "Date",
        "Products",
        "Order Value",
        "Status",
        "Invoice Number",
        "Created By",
    ]

    order_table_data = [
        [cell(v, header_style) for v in order_headers]
    ]

    order_table_data.extend(order_data)

    story.append(
        make_table(
            order_table_data,
            [
                24 * mm,
                30 * mm,
                21 * mm,
                42 * mm,
                28 * mm,
                24 * mm,
                31 * mm,
                22 * mm,
            ],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # ------------------------------------------------
    # 3. INVOICE RECONCILIATION
    # ------------------------------------------------

    story.append(
        Paragraph(
            "3. INVOICE AND PAYMENT RECONCILIATION",
            styles["Heading2"],
        )
    )

    invoice_headers = [
        "Order",
        "Customer",
        "Invoice Number",
        "Date",
        "Status",
        "Grand Total",
        "Paid",
        "Balance",
        "Payment Status",
    ]

    invoice_table_data = [
        [cell(v, header_style) for v in invoice_headers]
    ]

    invoice_table_data.extend(invoice_data)

    story.append(
        make_table(
            invoice_table_data,
            [
                21 * mm,
                25 * mm,
                32 * mm,
                19 * mm,
                21 * mm,
                25 * mm,
                23 * mm,
                23 * mm,
                27 * mm,
            ],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # ------------------------------------------------
    # 4. PAYMENT LEDGER
    # ------------------------------------------------

    story.append(
        Paragraph(
            "4. DETAILED PAYMENT LEDGER",
            styles["Heading2"],
        )
    )

    payment_headers = [
        "Order Number",
        "Customer",
        "Invoice Number",
        "Payment Date",
        "Amount",
        "Method",
        "Status",
        "Transaction Reference",
        "Received By",
    ]

    payment_table_data = [
        [cell(v, header_style) for v in payment_headers]
    ]

    payment_table_data.extend(payment_data)

    story.append(
        make_table(
            payment_table_data,
            [
                23 * mm,
                25 * mm,
                31 * mm,
                21 * mm,
                22 * mm,
                24 * mm,
                20 * mm,
                37 * mm,
                23 * mm,
            ],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # ------------------------------------------------
    # 5. DISPATCH LEDGER
    # ------------------------------------------------

    story.append(
        Paragraph(
            "5. DISPATCH AUDIT LEDGER",
            styles["Heading2"],
        )
    )

    dispatch_headers = [
        "Order Number",
        "Customer",
        "Dispatch Number",
        "Dispatch Date",
        "Quantity",
        "Status",
        "Posted By",
        "Notes",
    ]

    dispatch_table_data = [
        [cell(v, header_style) for v in dispatch_headers]
    ]

    dispatch_table_data.extend(dispatch_data)

    story.append(
        make_table(
            dispatch_table_data,
            [
                25 * mm,
                30 * mm,
                32 * mm,
                23 * mm,
                19 * mm,
                24 * mm,
                25 * mm,
                48 * mm,
            ],
        )
    )

    # ------------------------------------------------
    # PDF FOOTER
    # ------------------------------------------------

    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=8 * mm,
        leftMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
    )

    def add_footer(canvas, doc):

        canvas.saveState()

        canvas.setFont("Helvetica", 8)

        canvas.setFillColor(colors.grey)

        canvas.drawString(
            8 * mm,
            7 * mm,
            "Sales Audit Report",
        )

        canvas.drawRightString(
            landscape(A4)[0] - 8 * mm,
            7 * mm,
            f"Page {doc.page}",
        )

        canvas.restoreState()

    document.build(
        story,
        onFirstPage=add_footer,
        onLaterPages=add_footer,
    )

    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/pdf",
    )

    response["Content-Disposition"] = (
        'attachment; filename="sales_audit_report.pdf"'
    )

    return response

@login_required
def production_audit_report(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()
    raw_status = request.GET.get("status", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    errors = []

    if raw_from and start_date is None:
        errors.append("Invalid starting date.")

    if raw_to and end_date is None:
        errors.append("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        errors.append("Ending date cannot be before starting date.")

    selected_product = None

    if raw_product:
        try:
            selected_product = Product.objects.get(
                pk=int(raw_product)
            )
        except (ValueError, Product.DoesNotExist):
            errors.append("Invalid product selection.")

    valid_statuses = {
        value for value, label in ProductionRun.Status.choices
    }

    if raw_status and raw_status not in valid_statuses:
        errors.append("Invalid production status.")

    products = Product.objects.order_by("name")

    # Fetch production runs.
    runs_queryset = (
        ProductionRun.objects
        .select_related(
            "product_recipe__product",
            "product_recipe__bom",
            "product_recipe__output_unit",
            "created_by",
            "cost_record",
        )
        .prefetch_related(
            "consumptions__raw_material__unit",
        )
        .order_by("-production_date", "-pk")
    )

    if start_date:
        runs_queryset = runs_queryset.filter(
            production_date__gte=start_date
        )

    if end_date:
        runs_queryset = runs_queryset.filter(
            production_date__lte=end_date
        )

    if selected_product:
        runs_queryset = runs_queryset.filter(
            product_recipe__product=selected_product
        )

    if raw_status:
        runs_queryset = runs_queryset.filter(
            status=raw_status
        )

    runs = list(runs_queryset) if not errors else []

    # Summary values.
    total_runs = len(runs)
    posted_runs = 0
    draft_runs = 0
    costed_runs = 0

    total_expected_output = Decimal("0")
    total_good_output = Decimal("0")
    total_wastage = Decimal("0")

    total_material_cost = Decimal("0.00")
    total_production_cost = Decimal("0.00")

    material_line_count = 0

    report_rows = []
    consumption_ledger = []
    cost_ledger = []

    for run in runs:

        product = run.product_recipe.product
        recipe = run.product_recipe

        expected_output = run.expected_output_quantity
        actual_output = run.actual_good_output
        wastage = run.output_wastage
        yield_variance = run.yield_variance_percent

        total_expected_output += expected_output
        total_good_output += actual_output
        total_wastage += wastage

        if run.status == ProductionRun.Status.POSTED:
            posted_runs += 1
        else:
            draft_runs += 1

        # Get consumption records.
        consumptions = list(run.consumptions.all())

        material_line_count += len(consumptions)

        for consumption in consumptions:

            material = consumption.raw_material

            consumption_ledger.append({
                "run_number": run.run_number,
                "production_date": run.production_date,
                "product": product.name,
                "material": material.name,
                "sku": material.sku,
                "planned": consumption.planned_quantity,
                "actual": consumption.actual_quantity,
                "variance": consumption.variance_quantity,
                "variance_percent": consumption.variance_percent,
                "unit": material.unit.symbol,
            })

        # Get production costing record, if available.
        try:
            cost_record = run.cost_record
        except ProductionCost.DoesNotExist:
            cost_record = None

        if cost_record:

            costed_runs += 1

            total_material_cost += cost_record.material_cost
            total_production_cost += cost_record.total_cost

            cost_ledger.append({
                "run": run,
                "product": product,
                "material_cost": cost_record.material_cost,
                "labour_cost": cost_record.labour_cost,
                "gas_power_cost": cost_record.gas_power_cost,
                "other_overhead": cost_record.other_overhead,
                "total_cost": cost_record.total_cost,
                "cost_per_packet": cost_record.cost_per_packet,
                "valuation_method": (
                    cost_record.get_valuation_method_display()
                ),
                "calculated_at": cost_record.calculated_at,
            })

        else:
            cost_record = None

        # Production run report.
        report_rows.append({
            "run": run,
            "product": product,
            "bom": recipe.bom,
            "expected_output": expected_output,
            "actual_output": actual_output,
            "output_wastage": wastage,
            "yield_variance": yield_variance,
            "output_unit": recipe.output_unit.symbol,
            "consumptions": consumptions,
            "cost_record": cost_record,
        })

    # Final summary.
    summary = {
        "total_runs": total_runs,
        "posted_runs": posted_runs,
        "draft_runs": draft_runs,
        "costed_runs": costed_runs,
        "expected_output": total_expected_output,
        "good_output": total_good_output,
        "wastage": total_wastage,
        "material_cost": total_material_cost,
        "total_cost": total_production_cost,
        "material_lines": material_line_count,
    }

    context = {
        "products": products,
        "selected_product": raw_product,
        "selected_status": raw_status,
        "status_choices": ProductionRun.Status.choices,
        "from_date": raw_from,
        "to_date": raw_to,
        "errors": errors,
        "report_rows": report_rows,
        "consumption_ledger": consumption_ledger,
        "cost_ledger": cost_ledger,
        "summary": summary,
    }

    return render(
        request,
        "reporting/production_audit_report.html",
        context,
    )

@login_required
def production_audit_export_excel(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()
    raw_status = request.GET.get("status", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_product = None

    if raw_product:
        try:
            selected_product = Product.objects.get(
                pk=int(raw_product)
            )
        except (ValueError, Product.DoesNotExist):
            return HttpResponseBadRequest("Invalid product.")

    valid_statuses = {
        value for value, label in ProductionRun.Status.choices
    }

    if raw_status and raw_status not in valid_statuses:
        return HttpResponseBadRequest("Invalid production status.")

    runs_queryset = (
        ProductionRun.objects
        .select_related(
            "product_recipe__product",
            "product_recipe__bom",
            "product_recipe__output_unit",
            "created_by",
            "cost_record",
        )
        .prefetch_related(
            "consumptions__raw_material__unit",
        )
        .order_by("-production_date", "-pk")
    )

    if start_date:
        runs_queryset = runs_queryset.filter(
            production_date__gte=start_date
        )

    if end_date:
        runs_queryset = runs_queryset.filter(
            production_date__lte=end_date
        )

    if selected_product:
        runs_queryset = runs_queryset.filter(
            product_recipe__product=selected_product
        )

    if raw_status:
        runs_queryset = runs_queryset.filter(
            status=raw_status
        )

    runs = list(runs_queryset)

    # Helper to prevent spreadsheet formula injection.
    def safe_text(value):

        value = str(value or "")

        if value.startswith(("=", "+", "-", "@")):
            return "'" + value

        return value

    # Workbook.
    workbook = Workbook()

    # ==========================================
    # SHEET 1: PRODUCTION SUMMARY
    # ==========================================

    summary_ws = workbook.active
    summary_ws.title = "Production Summary"

    summary_ws.append(["PRODUCTION AUDIT REPORT"])

    summary_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    summary_ws.append([
        "Product",
        selected_product.name if selected_product else "All Products",
    ])

    summary_ws.append([
        "Production Status",
        raw_status.title() if raw_status else "All Statuses",
    ])

    # Summary calculations.
    posted_runs = 0
    draft_runs = 0
    costed_runs = 0
    material_line_count = 0

    total_material_cost = Decimal("0.00")
    total_production_cost = Decimal("0.00")

    # Group output by product and unit.
    output_groups = {}

    run_data = []
    consumption_data = []
    cost_data = []

    for run in runs:

        product = run.product_recipe.product
        recipe = run.product_recipe

        expected = run.expected_output_quantity
        actual = run.actual_good_output
        wastage = run.output_wastage
        yield_variance = run.yield_variance_percent

        if run.status == ProductionRun.Status.POSTED:
            posted_runs += 1
        else:
            draft_runs += 1

        # Group output without combining unlike units.
        group_key = (
            product.pk,
            recipe.output_unit.symbol,
        )

        if group_key not in output_groups:

            output_groups[group_key] = {
                "product": product.name,
                "unit": recipe.output_unit.symbol,
                "runs": 0,
                "expected": Decimal("0"),
                "actual": Decimal("0"),
                "wastage": Decimal("0"),
            }

        group = output_groups[group_key]

        group["runs"] += 1
        group["expected"] += expected
        group["actual"] += actual
        group["wastage"] += wastage

        # Production run record.
        run_data.append([
            safe_text(run.run_number),
            run.production_date,
            safe_text(product.name),
            safe_text(recipe.bom.code),
            run.batch_count,
            float(expected),
            float(actual),
            float(wastage),
            float(yield_variance),
            safe_text(recipe.output_unit.symbol),
            safe_text(run.get_status_display()),
            safe_text(
                run.created_by.username
                if run.created_by else "-"
            ),
            safe_text(run.notes),
        ])

        # Material consumption records.
        for consumption in run.consumptions.all():

            material = consumption.raw_material

            material_line_count += 1

            consumption_data.append([
                safe_text(run.run_number),
                run.production_date,
                safe_text(product.name),
                safe_text(material.name),
                safe_text(material.sku),
                float(consumption.planned_quantity),
                float(consumption.actual_quantity),
                float(consumption.variance_quantity),
                float(consumption.variance_percent),
                safe_text(material.unit.symbol),
            ])

        # Production cost record.
        try:
            cost_record = run.cost_record
        except ProductionCost.DoesNotExist:
            cost_record = None

        if cost_record:

            costed_runs += 1

            total_material_cost += cost_record.material_cost
            total_production_cost += cost_record.total_cost

            cost_data.append([
                safe_text(run.run_number),
                run.production_date,
                safe_text(product.name),
                float(cost_record.material_cost),
                float(cost_record.labour_cost),
                float(cost_record.gas_power_cost),
                float(cost_record.other_overhead),
                float(cost_record.total_cost),
                float(cost_record.cost_per_packet),
                safe_text(
                    cost_record.get_valuation_method_display()
                ),
                cost_record.calculated_at.replace(tzinfo=None)
                if cost_record.calculated_at else None,
            ])

    # Summary sheet calculations.
    summary_ws.append(["Metric", "Value"])

    metrics = [
        ("Total Production Runs", len(runs)),
        ("Posted Runs", posted_runs),
        ("Draft Runs", draft_runs),
        ("Costed Runs", costed_runs),
        ("Consumption Records", material_line_count),
        ("Total Material Cost", float(total_material_cost)),
        ("Total Production Cost", float(total_production_cost)),
    ]

    for metric, value in metrics:
        summary_ws.append([metric, value])

    # Output by product and unit.
    summary_ws.append([])

    output_header_row = summary_ws.max_row + 1

    summary_ws.append([
        "Product",
        "Output Unit",
        "Run Count",
        "Expected Output",
        "Good Output",
        "Output Wastage",
    ])

    for group in output_groups.values():

        summary_ws.append([
            safe_text(group["product"]),
            safe_text(group["unit"]),
            group["runs"],
            float(group["expected"]),
            float(group["actual"]),
            float(group["wastage"]),
        ])

    # ==========================================
    # SHEET 2: PRODUCTION RUNS
    # ==========================================

    runs_ws = workbook.create_sheet("Production Runs")

    runs_ws.append(["PRODUCTION RUN AUDIT"])

    runs_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    runs_ws.append([
        "Run Number",
        "Production Date",
        "Product",
        "BOM Code",
        "Batch Count",
        "Expected Output",
        "Good Output",
        "Wastage",
        "Yield Variance %",
        "Output Unit",
        "Status",
        "Created By",
        "Notes",
    ])

    for row in run_data:
        runs_ws.append(row)

    # ==========================================
    # SHEET 3: MATERIAL CONSUMPTION
    # ==========================================

    consumption_ws = workbook.create_sheet("Material Consumption")

    consumption_ws.append(["RAW MATERIAL CONSUMPTION LEDGER"])

    consumption_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    consumption_ws.append([
        "Run Number",
        "Production Date",
        "Product",
        "Raw Material",
        "SKU",
        "Planned Quantity",
        "Actual Quantity",
        "Variance",
        "Variance %",
        "Unit",
    ])

    for row in consumption_data:
        consumption_ws.append(row)

    # ==========================================
    # SHEET 4: PRODUCTION COSTS
    # ==========================================

    cost_ws = workbook.create_sheet("Production Costs")

    cost_ws.append(["PRODUCTION COST AUDIT"])

    cost_ws.append([
        "From Date", raw_from or "All",
        "To Date", raw_to or "All",
    ])

    cost_ws.append([
        "Run Number",
        "Production Date",
        "Product",
        "Material Cost",
        "Labour Cost",
        "Gas / Power Cost",
        "Other Overhead",
        "Total Cost",
        "Cost Per Packet",
        "Valuation Method",
        "Calculated At",
    ])

    for row in cost_data:
        cost_ws.append(row)

    # ==========================================
    # PROFESSIONAL FORMATTING
    # ==========================================

    header_fill = PatternFill(
        fill_type="solid",
        fgColor="286D91",
    )

    white_font = Font(
        bold=True,
        color="FFFFFF",
    )

    title_font = Font(
        bold=True,
        size=16,
        color="286D91",
    )

    sheet_headers = [
        (runs_ws, 3),
        (consumption_ws, 3),
        (cost_ws, 3),
    ]

    for sheet, header_row in sheet_headers:

        sheet.freeze_panes = f"A{header_row + 1}"

        sheet.auto_filter.ref = (
            f"A{header_row}:"
            f"{sheet.cell(header_row, sheet.max_column).column_letter}"
            f"{sheet.max_row}"
        )

        for cell in sheet[header_row]:

            cell.fill = header_fill
            cell.font = white_font

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

        sheet.row_dimensions[header_row].height = 25

    # Summary sheet formatting.
    summary_ws.freeze_panes = "A7"

    for cell in summary_ws[6]:
        cell.fill = header_fill
        cell.font = white_font

    for cell in summary_ws[1]:
        cell.font = title_font

    for cell in summary_ws[output_header_row]:

        cell.fill = header_fill
        cell.font = white_font

        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
        )

    # Title formatting and column widths.
    for sheet in workbook.worksheets:

        for cell in sheet[1]:
            cell.font = title_font

        for column_cells in sheet.columns:

            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:

                if cell.value is not None:
                    max_length = max(
                        max_length,
                        len(str(cell.value)),
                    )

            sheet.column_dimensions[column_letter].width = min(
                max_length + 4,
                45,
            )

    # Quantity formats in Production Runs.
    for column in (6, 7, 8):
        for row in runs_ws.iter_rows(
            min_row=4,
            min_col=column,
            max_col=column,
        ):
            for cell in row:
                cell.number_format = "0.000"

    # Yield variance.
    for row in runs_ws.iter_rows(
        min_row=4,
        min_col=9,
        max_col=9,
    ):
        for cell in row:
            cell.number_format = "0.00"

    # Consumption quantity and variance formatting.
    for column in (6, 7, 8, 9):
        for row in consumption_ws.iter_rows(
            min_row=4,
            min_col=column,
            max_col=column,
        ):
            for cell in row:
                cell.number_format = "0.000"

    # Cost formatting.
    for column in range(4, 10):
        for row in cost_ws.iter_rows(
            min_row=4,
            min_col=column,
            max_col=column,
        ):
            for cell in row:
                cell.number_format = '"₹"#,##0.00'

    # Date formatting.
    for sheet, column in [
        (runs_ws, 2),
        (consumption_ws, 2),
        (cost_ws, 2),
    ]:

        for row in sheet.iter_rows(
            min_row=4,
            min_col=column,
            max_col=column,
        ):
            for cell in row:
                cell.number_format = "DD-MM-YYYY"

    # Cost calculation date.
    for row in cost_ws.iter_rows(
        min_row=4,
        min_col=11,
        max_col=11,
    ):
        for cell in row:
            cell.number_format = "DD-MM-YYYY HH:MM"

    # Generate Excel response.
    output = BytesIO()

    workbook.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        'attachment; filename="production_audit_report.xlsx"'
    )

    return response

@login_required
def production_audit_export_pdf(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    raw_product = request.GET.get("product", "").strip()
    raw_status = request.GET.get("status", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    selected_product = None

    if raw_product:
        try:
            selected_product = Product.objects.get(
                pk=int(raw_product)
            )
        except (ValueError, Product.DoesNotExist):
            return HttpResponseBadRequest("Invalid product.")

    valid_statuses = {
        value for value, label in ProductionRun.Status.choices
    }

    if raw_status and raw_status not in valid_statuses:
        return HttpResponseBadRequest("Invalid production status.")

    # Fetch production runs.
    runs_queryset = (
        ProductionRun.objects
        .select_related(
            "product_recipe__product",
            "product_recipe__bom",
            "product_recipe__output_unit",
            "created_by",
            "cost_record",
        )
        .prefetch_related(
            "consumptions__raw_material__unit",
        )
        .order_by("-production_date", "-pk")
    )

    if start_date:
        runs_queryset = runs_queryset.filter(
            production_date__gte=start_date
        )

    if end_date:
        runs_queryset = runs_queryset.filter(
            production_date__lte=end_date
        )

    if selected_product:
        runs_queryset = runs_queryset.filter(
            product_recipe__product=selected_product
        )

    if raw_status:
        runs_queryset = runs_queryset.filter(
            status=raw_status
        )

    runs = list(runs_queryset)

    # Calculate totals.
    posted_runs = 0
    draft_runs = 0
    costed_runs = 0
    material_line_count = 0

    total_material_cost = Decimal("0.00")
    total_production_cost = Decimal("0.00")

    output_groups = {}

    # Prepare PDF styles.
    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "ProductionAuditBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
    )

    header_style = ParagraphStyle(
        "ProductionAuditHeader",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        fontSize=7,
        leading=8,
    )

    story = []

    def cell(value, style=body_style):

        return Paragraph(
            escape(str(value if value is not None else "-")),
            style,
        )

    def make_table(data, widths):

        table = LongTable(
            data,
            colWidths=widths,
            repeatRows=1,
            hAlign="LEFT",
        )

        table.setStyle(TableStyle([

            ("BACKGROUND", (0, 0), (-1, 0),
             colors.HexColor("#286D91")),

            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),

            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

            ("FONTSIZE", (0, 0), (-1, -1), 7),

            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),

            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F2F6FA")]),

            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

            ("LEFTPADDING", (0, 0), (-1, -1), 4),

            ("RIGHTPADDING", (0, 0), (-1, -1), 4),

            ("TOPPADDING", (0, 0), (-1, -1), 6),

            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),

        ]))

        return table

    def money(value):
        return f"Rs. {value:,.2f}"

    # =====================================
    # REPORT HEADER
    # =====================================

    story.append(
        Paragraph(
            "PRODUCTION AUDIT REPORT",
            styles["Title"],
        )
    )

    story.append(Spacer(1, 4 * mm))

    story.append(
        Paragraph(
            "Product: "
            + escape(
                selected_product.name
                if selected_product else "All Products"
            ),
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"From Date: {raw_from or 'All'}"
            f" &nbsp;&nbsp;&nbsp; To Date: {raw_to or 'All'}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"Production Status: "
            f"{raw_status.title() if raw_status else 'All Statuses'}",
            styles["Normal"],
        )
    )

    story.append(
        Paragraph(
            f"Generated: {timezone.localtime().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 7 * mm))

    # =====================================
    # COLLECT REPORT DATA
    # =====================================

    run_data = []
    consumption_data = []
    cost_data = []

    for run in runs:

        product = run.product_recipe.product
        recipe = run.product_recipe

        expected = run.expected_output_quantity
        actual = run.actual_good_output
        wastage = run.output_wastage
        yield_variance = run.yield_variance_percent

        if run.status == ProductionRun.Status.POSTED:
            posted_runs += 1
        else:
            draft_runs += 1

        # Group output by product and unit.
        group_key = (
            product.pk,
            recipe.output_unit.symbol,
        )

        if group_key not in output_groups:

            output_groups[group_key] = {
                "product": product.name,
                "unit": recipe.output_unit.symbol,
                "run_count": 0,
                "expected": Decimal("0"),
                "actual": Decimal("0"),
                "wastage": Decimal("0"),
            }

        group = output_groups[group_key]

        group["run_count"] += 1
        group["expected"] += expected
        group["actual"] += actual
        group["wastage"] += wastage

        # Production run data.
        run_data.append([
            cell(run.run_number),
            cell(run.production_date.strftime("%d-%m-%Y")),
            cell(product.name),
            cell(recipe.bom.code),
            cell(run.batch_count),
            cell(f"{expected:.3f}"),
            cell(f"{actual:.3f}"),
            cell(f"{wastage:.3f}"),
            cell(f"{yield_variance:.2f}%"),
            cell(recipe.output_unit.symbol),
            cell(run.get_status_display()),
            cell(
                run.created_by.username
                if run.created_by else "-"
            ),
        ])

        # Material consumption.
        for consumption in run.consumptions.all():

            material = consumption.raw_material

            material_line_count += 1

            consumption_data.append([
                cell(run.run_number),
                cell(run.production_date.strftime("%d-%m-%Y")),
                cell(product.name),
                cell(material.name),
                cell(material.sku),
                cell(f"{consumption.planned_quantity:.3f}"),
                cell(f"{consumption.actual_quantity:.3f}"),
                cell(f"{consumption.variance_quantity:.3f}"),
                cell(f"{consumption.variance_percent:.2f}%"),
                cell(material.unit.symbol),
            ])

        # Production costing.
        try:
            cost_record = run.cost_record
        except ProductionCost.DoesNotExist:
            cost_record = None

        if cost_record:

            costed_runs += 1

            total_material_cost += cost_record.material_cost
            total_production_cost += cost_record.total_cost

            calculated_at = "-"

            if cost_record.calculated_at:

                calculated_at = timezone.localtime(
                    cost_record.calculated_at
                ).strftime("%d-%m-%Y %H:%M")

            cost_data.append([
                cell(run.run_number),
                cell(product.name),
                cell(money(cost_record.material_cost)),
                cell(money(cost_record.labour_cost)),
                cell(money(cost_record.gas_power_cost)),
                cell(money(cost_record.other_overhead)),
                cell(money(cost_record.total_cost)),
                cell(money(cost_record.cost_per_packet)),
                cell(
                    cost_record.get_valuation_method_display()
                ),
                cell(calculated_at),
            ])

    # =====================================
    # 1. PRODUCTION SUMMARY
    # =====================================

    story.append(
        Paragraph(
            "1. PRODUCTION SUMMARY",
            styles["Heading2"],
        )
    )

    summary_data = [
        [
            cell("Metric", header_style),
            cell("Value", header_style),
        ],

        [cell("Total Production Runs"), cell(len(runs))],

        [cell("Posted Runs"), cell(posted_runs)],

        [cell("Draft Runs"), cell(draft_runs)],

        [cell("Costed Runs"), cell(costed_runs)],

        [cell("Consumption Records"), cell(material_line_count)],

        [cell("Total Material Cost"), cell(money(total_material_cost))],

        [cell("Total Production Cost"), cell(money(total_production_cost))],
    ]

    story.append(
        make_table(
            summary_data,
            [85 * mm, 70 * mm],
        )
    )

    story.append(Spacer(1, 7 * mm))

    story.append(
        Paragraph(
            "Production Output by Product",
            styles["Heading3"],
        )
    )

    output_data = [[
        cell("Product", header_style),
        cell("Unit", header_style),
        cell("Runs", header_style),
        cell("Expected", header_style),
        cell("Good Output", header_style),
        cell("Wastage", header_style),
    ]]

    for group in output_groups.values():

        output_data.append([
            cell(group["product"]),
            cell(group["unit"]),
            cell(group["run_count"]),
            cell(f'{group["expected"]:.3f}'),
            cell(f'{group["actual"]:.3f}'),
            cell(f'{group["wastage"]:.3f}'),
        ])

    story.append(
        make_table(
            output_data,
            [
                55 * mm,
                25 * mm,
                25 * mm,
                38 * mm,
                38 * mm,
                35 * mm,
            ],
        )
    )

    # =====================================
    # 2. PRODUCTION RUN AUDIT
    # =====================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "2. PRODUCTION RUN AUDIT",
            styles["Heading2"],
        )
    )

    run_headers = [
        "Run Number",
        "Date",
        "Product",
        "BOM",
        "Batch",
        "Expected",
        "Good Output",
        "Wastage",
        "Yield %",
        "Unit",
        "Status",
        "Created By",
    ]

    run_table_data = [
        [cell(value, header_style) for value in run_headers]
    ]

    run_table_data.extend(run_data)

    story.append(
        make_table(
            run_table_data,
            [
                24 * mm,
                18 * mm,
                28 * mm,
                30 * mm,
                13 * mm,
                24 * mm,
                22 * mm,
                18 * mm,
                18 * mm,
                14 * mm,
                18 * mm,
                22 * mm,
            ],
        )
    )

    # =====================================
    # 3. MATERIAL CONSUMPTION LEDGER
    # =====================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "3. RAW MATERIAL CONSUMPTION LEDGER",
            styles["Heading2"],
        )
    )

    consumption_headers = [
        "Run Number",
        "Date",
        "Product",
        "Raw Material",
        "SKU",
        "Planned",
        "Actual",
        "Variance",
        "Variance %",
        "Unit",
    ]

    consumption_table_data = [
        [cell(value, header_style) for value in consumption_headers]
    ]

    consumption_table_data.extend(consumption_data)

    story.append(
        make_table(
            consumption_table_data,
            [
                22 * mm,
                18 * mm,
                28 * mm,
                32 * mm,
                25 * mm,
                22 * mm,
                22 * mm,
                18 * mm,
                18 * mm,
                14 * mm,
            ],
        )
    )

    # =====================================
    # 4. PRODUCTION COST ANALYSIS
    # =====================================

    story.append(PageBreak())

    story.append(
        Paragraph(
            "4. PRODUCTION COST ANALYSIS",
            styles["Heading2"],
        )
    )

    cost_headers = [
        "Run Number",
        "Product",
        "Material Cost",
        "Labour Cost",
        "Gas / Power",
        "Other Overhead",
        "Total Cost",
        "Cost / Packet",
        "Valuation Method",
        "Calculated At",
    ]

    cost_table_data = [
        [cell(value, header_style) for value in cost_headers]
    ]

    cost_table_data.extend(cost_data)

    story.append(
        make_table(
            cost_table_data,
            [
                22 * mm,
                30 * mm,
                23 * mm,
                22 * mm,
                24 * mm,
                24 * mm,
                23 * mm,
                24 * mm,
                30 * mm,
                25 * mm,
            ],
        )
    )

    # =====================================
    # PDF GENERATION AND FOOTER
    # =====================================

    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=8 * mm,
        leftMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
    )

    def add_footer(canvas, doc):

        canvas.saveState()

        canvas.setFont("Helvetica", 8)

        canvas.setFillColor(colors.grey)

        canvas.drawString(
            8 * mm,
            7 * mm,
            "Production Audit Report",
        )

        canvas.drawRightString(
            landscape(A4)[0] - 8 * mm,
            7 * mm,
            f"Page {doc.page}",
        )

        canvas.restoreState()

    document.build(
        story,
        onFirstPage=add_footer,
        onLaterPages=add_footer,
    )

    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/pdf",
    )

    response["Content-Disposition"] = (
        'attachment; filename="production_audit_report.pdf"'
    )

    return response

@login_required
def management_dashboard(request):

    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()

    start_date = parse_date(raw_from) if raw_from else None
    end_date = parse_date(raw_to) if raw_to else None

    if raw_from and start_date is None:
        return HttpResponseBadRequest("Invalid starting date.")

    if raw_to and end_date is None:
        return HttpResponseBadRequest("Invalid ending date.")

    if start_date and end_date and end_date < start_date:
        return HttpResponseBadRequest("Invalid date range.")

    ZERO = Decimal("0.00")

    # =========================================
    # 1. INVENTORY STOCK BALANCES
    # =========================================

    products = list(
        Product.objects.filter(is_active=True).order_by("name")
    )

    raw_materials = list(
        RawMaterial.objects.filter(is_active=True).order_by("name")
    )

    # Calculate stock as of the selected end date.
    # Without an end date, use the latest available stock.

    def calculate_balances(item_field, item_ids):

        if not item_ids:
            return {}

        movement_queryset = StockMovement.objects.filter(
            **{f"{item_field}_id__in": item_ids}
        )

        if end_date:
            movement_queryset = movement_queryset.filter(
                created_at__date__lte=end_date
            )

        grouped = (
            movement_queryset
            .values(f"{item_field}_id")
            .annotate(
                incoming=Sum(
                    "quantity",
                    filter=Q(movement_type__in=INCOMING_TYPES)
                ),
                outgoing=Sum(
                    "quantity",
                    filter=Q(movement_type__in=OUTGOING_TYPES)
                ),
            )
        )

        balances = {}

        for item in grouped:

            incoming = item["incoming"] or ZERO
            outgoing = item["outgoing"] or ZERO

            balances[item[f"{item_field}_id"]] = (
                incoming - outgoing
            )

        return balances

    product_balances = calculate_balances(
        "product",
        [p.pk for p in products],
    )

    raw_material_balances = calculate_balances(
        "raw_material",
        [m.pk for m in raw_materials],
    )

    # Finished goods stock.
    product_stock_rows = []

    total_finished_stock = ZERO

    for product in products:

        balance = product_balances.get(
            product.pk,
            ZERO,
        )

        total_finished_stock += balance

        product_stock_rows.append({
            "product": product,
            "balance": balance,
        })

    # Raw material stock and reorder alerts.
    raw_material_rows = []
    low_stock_materials = []

    for material in raw_materials:

        balance = raw_material_balances.get(
            material.pk,
            ZERO,
        )

        reorder_level = material.reorder_level or ZERO

        is_low_stock = balance <= reorder_level

        row = {
            "material": material,
            "balance": balance,
            "reorder_level": reorder_level,
            "is_low_stock": is_low_stock,
        }

        raw_material_rows.append(row)

        if is_low_stock:
            low_stock_materials.append(row)

    # =========================================
    # 2. PRODUCTION SUMMARY
    # =========================================

    production_queryset = ProductionRun.objects.filter()

    if start_date:
        production_queryset = production_queryset.filter(
            production_date__gte=start_date
        )

    if end_date:
        production_queryset = production_queryset.filter(
            production_date__lte=end_date
        )

    production_runs = list(
        production_queryset.select_related(
            "product_recipe__product",
            "product_recipe__output_unit",
            "created_by",
        ).order_by("-production_date", "-pk")
    )

    posted_runs = [
        run for run in production_runs
        if run.status == ProductionRun.Status.POSTED
    ]

    draft_runs = [
        run for run in production_runs
        if run.status == ProductionRun.Status.DRAFT
    ]

    total_expected_output = sum(
        (run.expected_output_quantity for run in posted_runs),
        ZERO,
    )

    total_good_output = sum(
        (run.actual_good_output for run in posted_runs),
        ZERO,
    )

    total_wastage = sum(
        (run.output_wastage for run in posted_runs),
        ZERO,
    )

    # Include only cost records belonging to posted runs.
    posted_run_ids = [
        run.pk for run in posted_runs
    ]

    production_costs = ProductionCost.objects.filter(
        production_run_id__in=posted_run_ids
    )

    cost_totals = production_costs.aggregate(
        material=Sum("material_cost"),
        total=Sum("total_cost"),
    )

    total_material_cost = cost_totals["material"] or ZERO
    total_production_cost = cost_totals["total"] or ZERO

    # =========================================
    # 3. SALES SUMMARY
    # =========================================

    orders_queryset = (
        SalesOrder.objects
        .select_related("customer", "created_by")
        .prefetch_related("items")
    )

    if start_date:
        orders_queryset = orders_queryset.filter(
            order_date__gte=start_date
        )

    if end_date:
        orders_queryset = orders_queryset.filter(
            order_date__lte=end_date
        )

    orders = list(
        orders_queryset.order_by("-order_date", "-pk")
    )

    total_order_value = sum(
        (
            Decimal(item.quantity) * item.unit_price
            for order in orders
            for item in order.items.all()
        ),
        ZERO,
    )

    # Only issued invoices contribute to invoice value
    # and outstanding balance.
    invoices_queryset = Invoice.objects.filter(
        status=Invoice.Status.ISSUED
    ).select_related("order", "order__customer")

    if start_date:
        invoices_queryset = invoices_queryset.filter(
            invoice_date__gte=start_date
        )

    if end_date:
        invoices_queryset = invoices_queryset.filter(
            invoice_date__lte=end_date
        )

    issued_invoices = list(invoices_queryset)

    total_invoice_value = sum(
        (invoice.grand_total for invoice in issued_invoices),
        ZERO,
    )

    total_outstanding = sum(
        (invoice.balance_due for invoice in issued_invoices),
        ZERO,
    )

    # Received payments only.
    payments_queryset = Payment.objects.filter(
        status=Payment.Status.RECEIVED
    )

    if start_date:
        payments_queryset = payments_queryset.filter(
            payment_date__gte=start_date
        )

    if end_date:
        payments_queryset = payments_queryset.filter(
            payment_date__lte=end_date
        )

    payment_totals = payments_queryset.aggregate(
        total=Sum("amount")
    )

    total_received = payment_totals["total"] or ZERO

    # =========================================
    # 4. DISPATCH SUMMARY
    # =========================================

    dispatches_queryset = Dispatch.objects.all()

    if start_date:
        dispatches_queryset = dispatches_queryset.filter(
            dispatch_date__gte=start_date
        )

    if end_date:
        dispatches_queryset = dispatches_queryset.filter(
            dispatch_date__lte=end_date
        )

    total_dispatches = dispatches_queryset.count()

    posted_dispatches = dispatches_queryset.filter(
        status=Dispatch.Status.POSTED
    ).count()

    draft_dispatches = dispatches_queryset.filter(
        status=Dispatch.Status.DRAFT
    ).count()

    # =========================================
    # 5. RECENT ACTIVITY
    # =========================================

    recent_orders = orders[:5]

    recent_production_runs = production_runs[:5]

    stock_activity_queryset = (
        StockMovement.objects
        .select_related(
            "product",
            "raw_material",
            "unit",
            "created_by",
        )
        .order_by("-created_at", "-pk")
    )

    if start_date:
        stock_activity_queryset = stock_activity_queryset.filter(
            created_at__date__gte=start_date
        )

    if end_date:
        stock_activity_queryset = stock_activity_queryset.filter(
            created_at__date__lte=end_date
        )

    recent_stock_movements = list(
        stock_activity_queryset[:8]
    )

    recent_dispatches = list(
        dispatches_queryset
        .select_related("sales_order", "posted_by")
        .order_by("-dispatch_date", "-pk")[:5]
    )

    # =========================================
    # 6. MANAGEMENT SUMMARY
    # =========================================

    summary = {
        # Inventory
        "product_count": len(products),
        "raw_material_count": len(raw_materials),
        "finished_stock": total_finished_stock,
        "low_stock_count": len(low_stock_materials),

        # Production
        "production_runs": len(production_runs),
        "posted_runs": len(posted_runs),
        "draft_runs": len(draft_runs),
        "good_output": total_good_output,
        "expected_output": total_expected_output,
        "wastage": total_wastage,
        "material_cost": total_material_cost,
        "production_cost": total_production_cost,

        # Sales
        "order_count": len(orders),
        "order_value": total_order_value,
        "invoice_value": total_invoice_value,
        "received": total_received,
        "outstanding": total_outstanding,

        # Dispatch
        "dispatch_count": total_dispatches,
        "posted_dispatches": posted_dispatches,
        "draft_dispatches": draft_dispatches,
    }

    context = {
        "from_date": raw_from,
        "to_date": raw_to,
        "summary": summary,
        "product_stock_rows": product_stock_rows,
        "raw_material_rows": raw_material_rows,
        "low_stock_materials": low_stock_materials,
        "recent_orders": recent_orders,
        "recent_production_runs": recent_production_runs,
        "recent_stock_movements": recent_stock_movements,
        "recent_dispatches": recent_dispatches,
        "stock_as_of": end_date,
    }

    return render(
        request,
        "reporting/management_dashboard.html",
        context,
    )
