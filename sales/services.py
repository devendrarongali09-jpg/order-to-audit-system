
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import StockMovement
from inventory.services import get_stock_balance
from masterdata.models import Product, UnitOfMeasure

from .models import (
    SalesOrder,
    SalesOrderItem,
    InvoiceSequence,
    Invoice,
    InvoiceLine,
    Payment,
    Dispatch,
    DispatchItem,
)

def check_order_stock(order):
    """
    Check whether finished-goods stock covers every
    item in a sales order.
    """

    items = list(
        order.items.select_related("product").all()
    )

    if not items:
        raise ValidationError(
            "An order must contain at least one product."
        )

    shortages = []

    for item in items:

        available = get_stock_balance(
            product=item.product
        )

        if available < item.quantity:

            shortage = item.quantity - available

            shortages.append(
                f"{item.product.name}: "
                f"Requested {item.quantity}, "
                f"Available {available}, "
                f"Shortage {shortage}"
            )

    return shortages


@transaction.atomic
def submit_order(*, order_id):

    order = SalesOrder.objects.select_for_update().get(
        pk=order_id
    )

    if order.status != SalesOrder.Status.DRAFT:
        raise ValidationError(
            "Only draft orders can be submitted."
        )

    if not order.customer.is_active:
        raise ValidationError(
            "This customer is inactive."
        )

    items = list(
        order.items.select_related("product").all()
    )

    if not items:
        raise ValidationError(
            "Cannot submit an empty order."
        )

    for item in items:
        if not item.product.is_active:
            raise ValidationError(
                f"{item.product.name} is inactive."
            )

        if item.quantity <= 0:
            raise ValidationError(
                "Order quantities must be positive."
            )

        if item.unit_price < 0:
            raise ValidationError(
                "Selling price cannot be negative."
            )

    order.status = SalesOrder.Status.SUBMITTED
    order.submitted_at = timezone.now()

    order.save(
        update_fields=["status", "submitted_at", "updated_at"]
    )

    return order



@transaction.atomic
def approve_order(*, order_id, user):

    order = SalesOrder.objects.select_for_update().get(
        pk=order_id
    )

    if order.status != SalesOrder.Status.SUBMITTED:
        raise ValidationError(
            "Only submitted orders can be approved."
        )

    # Stock availability is checked when posting dispatch.
    # Approval does not reserve or deduct inventory.

    order.status = SalesOrder.Status.APPROVED
    order.approved_by = user
    order.approved_at = timezone.now()

    order.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "updated_at",
        ]
    )

    return order


@transaction.atomic
def reject_order(*, order_id, reason):

    order = SalesOrder.objects.select_for_update().get(
        pk=order_id
    )

    if order.status != SalesOrder.Status.SUBMITTED:
        raise ValidationError(
            "Only submitted orders can be rejected."
        )

    if not reason or not reason.strip():
        raise ValidationError(
            "A rejection reason is required."
        )

    order.status = SalesOrder.Status.REJECTED
    order.rejection_reason = reason.strip()

    order.save(
        update_fields=["status", "rejection_reason", "updated_at"]
    )

    return order

CENT = Decimal("0.01")


def get_financial_year(invoice_date):
    """
    Indian financial year: April to March.
    Example: November 2026 = 2026-27.
    """

    if invoice_date.month >= 4:
        start_year = invoice_date.year
    else:
        start_year = invoice_date.year - 1

    return f"{start_year}-{str(start_year + 1)[-2:]}"


def money(value):
    return Decimal(value).quantize(
        CENT,
        rounding=ROUND_HALF_UP
    )


@transaction.atomic
def generate_invoice(
    *,
    order_id,
    user,
    seller_name=None,
    seller_gstin=None,
    seller_state_code=None
):
    seller_name = (
        settings.SELLER_LEGAL_NAME
        if seller_name is None
        else seller_name
    )

    seller_gstin = (
        settings.SELLER_GSTIN
        if seller_gstin is None
        else seller_gstin
    )

    seller_state_code = (
        settings.SELLER_STATE_CODE
        if seller_state_code is None
        else seller_state_code
    )

    order = SalesOrder.objects.select_for_update().get(
        pk=order_id
    )

    if order.status != SalesOrder.Status.APPROVED:
        raise ValidationError(
            "Only approved sales orders can be invoiced."
        )

    if Invoice.objects.filter(order=order).exists():
        raise ValidationError(
            "An invoice already exists for this order."
        )

    items = list(
        order.items.select_related("product").all()
    )

    if not items:
        raise ValidationError(
            "Cannot invoice an empty sales order."
        )

    invoice_date = timezone.localdate()

    financial_year = get_financial_year(invoice_date)

    sequence, created = InvoiceSequence.objects.get_or_create(
        financial_year=financial_year,
        defaults={"last_number": 0}
    )

    sequence = InvoiceSequence.objects.select_for_update().get(
        pk=sequence.pk
    )

    sequence.last_number += 1

    sequence.save(update_fields=["last_number"])

    invoice_number = (
        f"INV/{financial_year}/{sequence.last_number:04d}"
    )

    customer = order.customer

    is_intrastate = (
        seller_state_code == customer.state_code
    )

    line_data = []

    taxable_total = Decimal("0.00")
    cgst_total = Decimal("0.00")
    sgst_total = Decimal("0.00")
    igst_total = Decimal("0.00")

    for item in items:

        taxable = money(
            Decimal(item.quantity) * item.unit_price
        )

        gst_rate = item.product.gst_rate

        total_tax = money(
            taxable * gst_rate / Decimal("100")
        )

        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
        igst = Decimal("0.00")

        if is_intrastate:
            cgst = money(total_tax / Decimal("2"))
            sgst = total_tax - cgst
        else:
            igst = total_tax

        line_total = (
            taxable + cgst + sgst + igst
        )

        line_data.append({
            "product": item.product,
            "product_name": item.product.name,
            "hsn_code": item.product.hsn_code,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "gst_rate": gst_rate,
            "taxable_value": taxable,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
            "total_amount": line_total,
        })

        taxable_total += taxable
        cgst_total += cgst
        sgst_total += sgst
        igst_total += igst

    grand_total = (
        taxable_total
        + cgst_total
        + sgst_total
        + igst_total
    )

    invoice = Invoice.objects.create(
        invoice_number=invoice_number,
        order=order,
        invoice_date=invoice_date,
        seller_name=seller_name,
        seller_gstin=seller_gstin,
        seller_state_code=seller_state_code,
        customer_name=customer.name,
        customer_gstin=customer.gstin,
        customer_address=customer.address,
        place_of_supply=customer.state,
        place_of_supply_state_code=customer.state_code,
        taxable_value=taxable_total,
        cgst_total=cgst_total,
        sgst_total=sgst_total,
        igst_total=igst_total,
        grand_total=grand_total,
        issued_by=user,
    )

    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            **line
        )
        for line in line_data
    ])

    order.status = SalesOrder.Status.INVOICED

    order.save(
        update_fields=["status", "updated_at"]
    )

    return invoice

@transaction.atomic
def record_payment(
    *,
    invoice_id,
    amount,
    payment_date,
    method,
    user,
    transaction_reference="",
    notes=""
):

    invoice = Invoice.objects.select_for_update().get(
        pk=invoice_id
    )

    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError(
            "Payments can only be recorded against issued invoices."
        )

    amount = Decimal(str(amount))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Payment amount must be greater than zero."
        )

    amount = money(amount)

    if amount <= 0:
        raise ValidationError(
            "Payment amount must be at least ₹0.01."
        )

    if amount > invoice.balance_due:
        raise ValidationError(
            f"Overpayment is not allowed. "
            f"Outstanding balance: ₹{invoice.balance_due}."
        )

    payment = Payment.objects.create(
        invoice=invoice,
        payment_date=payment_date,
        amount=amount,
        method=method,
        transaction_reference=transaction_reference,
        notes=notes,
        received_by=user,
        status=Payment.Status.RECEIVED,
    )

    return payment
@transaction.atomic
def reverse_payment(*, payment_id, user, reason):
    """
    Reverse a received payment without deleting its audit record.

    Only Owner-group users and superusers may reverse payments.
    """

    if not user or not user.is_authenticated:
        raise PermissionDenied(
            "Authentication is required to reverse payments."
        )

    is_owner = (
        user.is_superuser
        or user.groups.filter(name="Owner").exists()
    )

    if not is_owner:
        raise PermissionDenied(
            "Only the Owner can reverse payments."
        )

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError(
            "A reason is required to reverse a payment."
        )

    # Read the invoice reference, then lock the invoice first.
    payment_reference = Payment.objects.only("invoice_id").get(
        pk=payment_id
    )

    Invoice.objects.select_for_update().get(
        pk=payment_reference.invoice_id
    )

    # Lock and retrieve the payment.
    payment = Payment.objects.select_for_update().get(
        pk=payment_id
    )

    if payment.status != Payment.Status.RECEIVED:
        raise ValidationError(
            "Only received payments can be reversed."
        )

    payment.status = Payment.Status.REVERSED
    payment.reversed_at = timezone.now()
    payment.reversed_by = user
    payment.reversal_reason = reason

    payment.save(
        update_fields=[
            "status",
            "reversed_at",
            "reversed_by",
            "reversal_reason",
        ]
    )

    return payment

@transaction.atomic
def post_dispatch(*, dispatch_id, user):

    # Lock the dispatch record.
    dispatch = (
        Dispatch.objects
        .select_for_update()
        .select_related("sales_order")
        .get(pk=dispatch_id)
    )

    if dispatch.status != Dispatch.Status.DRAFT:
        raise ValidationError(
            "Only draft dispatches can be posted."
        )

    # Lock the sales order.
    order = (
        SalesOrder.objects
        .select_for_update()
        .get(pk=dispatch.sales_order_id)
    )

    if order.status != SalesOrder.Status.INVOICED:
        raise ValidationError(
            "Only invoiced sales orders can be dispatched."
        )

    # Confirm an issued invoice exists.
    invoice_exists = Invoice.objects.filter(
        order_id=order.pk,
        status=Invoice.Status.ISSUED
    ).exists()

    if not invoice_exists:
        raise ValidationError(
            "An issued invoice is required before dispatch."
        )

    items = list(
        dispatch.items.select_related(
            "order_item__product",
            "order_item__order"
        )
    )

    if not items:
        raise ValidationError(
            "Cannot post an empty dispatch."
        )

    # Check order quantities and previously posted dispatches.
    required_by_product = {}

    for item in items:

        order_item = item.order_item

        if order_item.order_id != order.pk:
            raise ValidationError(
                "Dispatch item does not belong to this sales order."
            )

        if item.quantity <= 0:
            raise ValidationError(
                "Dispatch quantity must be positive."
            )

        already_dispatched = (
            DispatchItem.objects
            .filter(
                order_item_id=order_item.pk,
                dispatch__status=Dispatch.Status.POSTED
            )
            .aggregate(total=Sum("quantity"))["total"] or 0
        )

        if already_dispatched + item.quantity > order_item.quantity:
            raise ValidationError(
                f"Dispatch quantity exceeds the remaining "
                f"ordered quantity for {order_item.product.name}."
            )

        product_id = order_item.product_id

        required_by_product[product_id] = (
            required_by_product.get(product_id, 0)
            + item.quantity
        )

    # Lock products to serialize concurrent dispatches.
    locked_products = list(
        Product.objects
        .select_for_update()
        .filter(pk__in=required_by_product.keys())
        .order_by("pk")
    )

    products = {
        product.pk: product
        for product in locked_products
    }

    # Validate stock availability.
    for product_id, required_quantity in required_by_product.items():

        product = products.get(product_id)

        if product is None:
            raise ValidationError(
                "A dispatch product could not be found."
            )

        available = get_stock_balance(product=product)

        if Decimal(str(available)) < Decimal(required_quantity):
            raise ValidationError(
                f"Insufficient stock for {product.name}. "
                f"Available: {available}. "
                f"Requested: {required_quantity}."
            )

    # Find the correct count unit.
    count_unit = UnitOfMeasure.objects.get(
        symbol__iexact="piece",
        dimension=UnitOfMeasure.Dimension.COUNT
    )

    # Create outgoing stock movements.
    for item in items:

        product = products[item.order_item.product_id]

        movement = StockMovement(
            product=product,
            movement_type=StockMovement.MovementType.DISPATCH,
            quantity=Decimal(item.quantity),
            unit=count_unit,
            unit_cost=None,
            reference_type="dispatch",
            reference_number=dispatch.dispatch_number,
            notes=(
                f"Sales dispatch {dispatch.dispatch_number} "
                f"for order {order.order_number}"
            ),
            created_by=user,
        )

        # Enforce StockMovement model validation.
        movement.full_clean()
        movement.save()

    # Mark dispatch as posted.
    dispatch.status = Dispatch.Status.POSTED
    dispatch.posted_by = user
    dispatch.posted_at = timezone.now()

    dispatch.save(
        update_fields=[
            "status",
            "posted_by",
            "posted_at",
        ]
    )

    # Check whether every order line has been fully dispatched.
    all_dispatched = True

    order_items = SalesOrderItem.objects.filter(order=order)

    if not order_items.exists():
        raise ValidationError(
            "Sales order has no items."
        )

    for order_item in order_items:

        dispatched_quantity = (
            DispatchItem.objects
            .filter(
                order_item=order_item,
                dispatch__status=Dispatch.Status.POSTED
            )
            .aggregate(total=Sum("quantity"))["total"] or 0
        )

        if dispatched_quantity < order_item.quantity:
            all_dispatched = False
            break

    if all_dispatched:
        order.status = SalesOrder.Status.DISPATCHED
        order.save(update_fields=["status"])

    return dispatch
