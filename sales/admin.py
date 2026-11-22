from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    SalesOrder,
    SalesOrderItem,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    Payment,
    Dispatch,
    DispatchItem,
)
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import Payment, Invoice
from .services import record_payment
from .services import (
    submit_order,
    approve_order,
    generate_invoice,
    post_dispatch,
    record_payment,
    reverse_payment,
)


class SalesOrderItemInline(admin.TabularInline):

    model = SalesOrderItem
    extra = 1

    fields = (
        "product",
        "quantity",
        "unit_price",
    )

    def get_readonly_fields(self, request, obj=None):

        if obj and obj.status != SalesOrder.Status.DRAFT:
            return ("product", "quantity", "unit_price")

        return ()

    def has_add_permission(self, request, obj=None):

        if obj and obj.status != SalesOrder.Status.DRAFT:
            return False

        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):

        if obj and obj.status != SalesOrder.Status.DRAFT:
            return False

        return super().has_delete_permission(request, obj)


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):

    list_display = (
        "order_number",
        "customer",
        "order_date",
        "status",
        "total_amount",
        "created_by",
    )

    list_filter = (
        "status",
        "order_date",
    )

    search_fields = (
        "order_number",
        "customer__name",
    )

    readonly_fields = (
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "submitted_at",
        "approved_at",
        "approved_by",
        "rejection_reason",
    )

    inlines = [SalesOrderItemInline]

    actions = [
        "submit_selected_orders",
        "approve_selected_orders",
        "generate_selected_invoices",
    ]
    def _is_salesman(self, request):
        if request.user.is_superuser:
            return False

        is_salesman = request.user.groups.filter(
            name="Salesman"
        ).exists()

        has_broader_role = request.user.groups.filter(
            name__in=(
                "Factory Admin",
                "Owner",
            )
        ).exists()

        return is_salesman and not has_broader_role

    def get_queryset(self, request):
        queryset = super().get_queryset(request)

        if self._is_salesman(request):
            queryset = queryset.filter(
                created_by=request.user
            )

        return queryset

    def has_view_permission(self, request, obj=None):
        allowed = super().has_view_permission(request, obj)

        if not allowed:
            return False

        if obj is not None and self._is_salesman(request):
            return obj.created_by_id == request.user.pk

        return True

    def has_change_permission(self, request, obj=None):
        allowed = super().has_change_permission(request, obj)

        if not allowed:
            return False

        if obj is not None and self._is_salesman(request):
            return (
                obj.created_by_id == request.user.pk
                and obj.status == SalesOrder.Status.DRAFT
            )

        return True

    def get_actions(self, request):
        actions = super().get_actions(request)

        if self._is_salesman(request):
            actions.pop("approve_selected_orders", None)
            actions.pop("generate_selected_invoices", None)

        return actions

    def save_model(self, request, obj, form, change):

        if self._is_salesman(request) and not change:
            obj.created_by = request.user

        elif not change and not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

    # ACTION 1: Submit orders

    @admin.action(description="Submit selected draft orders")
    def submit_selected_orders(self, request, queryset):

        for order in queryset:

            try:
                submit_order(order_id=order.pk)

            except ValidationError as exc:

                self.message_user(
                    request,
                    f"{order.order_number}: {'; '.join(exc.messages)}",
                    level=messages.ERROR,
                )

            else:

                self.message_user(
                    request,
                    f"{order.order_number} submitted successfully.",
                    level=messages.SUCCESS,
                )

    # ACTION 2: Approve orders

    @admin.action(description="Approve selected submitted orders")
    def approve_selected_orders(self, request, queryset):

        for order in queryset:

            try:
                approve_order(
                    order_id=order.pk,
                    user=request.user,
                )

            except ValidationError as exc:

                self.message_user(
                    request,
                    f"{order.order_number}: {'; '.join(exc.messages)}",
                    level=messages.ERROR,
                )

            else:

                self.message_user(
                    request,
                    f"{order.order_number} approved successfully.",
                    level=messages.SUCCESS,
                )

    # ACTION 3: Generate GST invoices

    @admin.action(
        description="Generate GST invoices for approved orders"
    )
    def generate_selected_invoices(self, request, queryset):

        for order in queryset:

            try:
                invoice = generate_invoice(
                    order_id=order.pk,
                    user=request.user,
                )

            except ValidationError as exc:

                self.message_user(
                    request,
                    f"{order.order_number}: {'; '.join(exc.messages)}",
                    level=messages.ERROR,
                )

            else:

                self.message_user(
                    request,
                    f"Invoice {invoice.invoice_number} generated "
                    f"successfully. Total: ₹{invoice.grand_total}",
                    level=messages.SUCCESS,
                )


class InvoiceLineInline(admin.TabularInline):

    model = InvoiceLine
    extra = 0
    can_delete = False

    readonly_fields = (
        "product",
        "product_name",
        "hsn_code",
        "quantity",
        "unit_price",
        "gst_rate",
        "taxable_value",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "total_amount",
    )



@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):

    list_display = (
        "invoice_number",
        "customer_name",
        "invoice_date",
        "taxable_value",
        "grand_total",
        "status",
        "pdf_download",
    )

    list_filter = (
        "status",
        "invoice_date",
    )

    search_fields = (
        "invoice_number",
        "customer_name",
    )

    readonly_fields = (
        "invoice_number",
        "order",
        "invoice_date",
        "seller_name",
        "seller_gstin",
        "seller_state_code",
        "customer_name",
        "customer_gstin",
        "customer_address",
        "place_of_supply",
        "place_of_supply_state_code",
        "taxable_value",
        "cgst_total",
        "sgst_total",
        "igst_total",
        "grand_total",
        "status",
        "issued_by",
        "issued_at",
        "cancelled_at",
    )

    inlines = [InvoiceLineInline]

    @admin.display(description="Download PDF")
    def pdf_download(self, obj):

        url = reverse(
            "sales:invoice_pdf",
            args=[obj.pk]
        )

        return format_html(
            '<a href="{}">Download PDF</a>',
            url
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False




class PaymentAdminForm(forms.ModelForm):

    class Meta:
        model = Payment
        fields = (
            "invoice",
            "payment_date",
            "amount",
            "method",
            "transaction_reference",
            "notes",
        )

    def clean(self):
        cleaned = super().clean()

        invoice = cleaned.get("invoice")
        amount = cleaned.get("amount")

        if invoice and amount:

            if invoice.status != Invoice.Status.ISSUED:
                raise forms.ValidationError(
                    "Only issued invoices accept payments."
                )

            if amount <= 0:
                raise forms.ValidationError(
                    "Payment amount must be positive."
                )

            if amount > invoice.balance_due:
                raise forms.ValidationError(
                    f"Payment exceeds outstanding balance "
                    f"of ₹{invoice.balance_due}."
                )

        return cleaned


# Register the ModelAdmin class, NOT the ModelForm.

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):

    form = PaymentAdminForm

    actions = ["reverse_selected_payments"]

    reversal_confirmation_template = (
        "admin/sales/payment/reverse_selected_confirmation.html"
    )

    list_display = (
        "invoice",
        "payment_date",
        "amount",
        "method",
        "status",
        "received_by",
        "reversed_by",
        "reversed_at",
    )

    list_filter = (
        "method",
        "status",
        "payment_date",
    )

    search_fields = (
        "invoice__invoice_number",
        "transaction_reference",
    )

    fields = (
        "invoice",
        "payment_date",
        "amount",
        "method",
        "transaction_reference",
        "notes",
        "status",
        "received_by",
        "created_at",
        "reversed_at",
        "reversed_by",
        "reversal_reason",
    )

    readonly_fields = (
        "status",
        "received_by",
        "created_at",
        "reversed_at",
        "reversed_by",
        "reversal_reason",
    )

    def _can_reverse_payments(self, request):
        return (
            request.user.is_superuser
            or request.user.groups.filter(
                name="Owner"
            ).exists()
        )

    def get_actions(self, request):
        actions = super().get_actions(request)

        if not self._can_reverse_payments(request):
            actions.pop("reverse_selected_payments", None)

        return actions

    def _render_reversal_confirmation(
        self,
        request,
        queryset,
        error_message=None,
    ):
        payments = list(
            queryset.select_related("invoice")
        )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Confirm payment reversal",
            "payments": payments,
            "action_name": "reverse_selected_payments",
            "action_index": request.POST.get("index", "0"),
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "reversal_reason": request.POST.get(
                "reversal_reason", ""
            ),
            "error_message": error_message,
            "changelist_url": reverse(
                "admin:sales_payment_changelist"
            ),
        }

        return TemplateResponse(
            request,
            self.reversal_confirmation_template,
            context,
        )

    @admin.action(
        description="Reverse selected payments",
        permissions=["view"],
    )
    def reverse_selected_payments(self, request, queryset):

        if not self._can_reverse_payments(request):
            raise PermissionDenied(
                "Only the Owner can reverse payments."
            )

        if "_confirm" not in request.POST:
            return self._render_reversal_confirmation(
                request,
                queryset,
            )

        reason = request.POST.get(
            "reversal_reason", ""
        ).strip()

        if not reason:
            return self._render_reversal_confirmation(
                request,
                queryset,
                error_message="A reversal reason is required.",
            )

        for payment in queryset:
            try:
                reversed_payment = reverse_payment(
                    payment_id=payment.pk,
                    user=request.user,
                    reason=reason,
                )

            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{payment.invoice.invoice_number}: "
                    f"{'; '.join(exc.messages)}",
                    level=messages.ERROR,
                )

            else:
                self.message_user(
                    request,
                    f"Payment of ₹{reversed_payment.amount} "
                    f"for {payment.invoice.invoice_number} "
                    f"reversed successfully.",
                    level=messages.SUCCESS,
                )

        return HttpResponseRedirect(
            reverse("admin:sales_payment_changelist")
        )

    def save_model(self, request, obj, form, change):

        if change:
            raise ValidationError(
                "Existing payments cannot be edited."
            )

        payment = record_payment(
            invoice_id=obj.invoice_id,
            amount=obj.amount,
            payment_date=obj.payment_date,
            method=obj.method,
            user=request.user,
            transaction_reference=obj.transaction_reference,
            notes=obj.notes,
        )

        obj.pk = payment.pk
        obj.created_at = payment.created_at
        obj.received_by = payment.received_by
        obj.status = payment.status

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
class DispatchItemInline(admin.TabularInline):

    model = DispatchItem
    extra = 1

    fields = (
        "order_item",
        "quantity",
    )

@admin.register(Dispatch)
class DispatchAdmin(admin.ModelAdmin):

    list_display = (
        "dispatch_number",
        "sales_order",
        "dispatch_date",
        "status",
        "created_by",
        "posted_at",
    )

    list_filter = (
        "status",
        "dispatch_date",
    )

    search_fields = (
        "dispatch_number",
        "sales_order__order_number",
    )

    date_hierarchy = "dispatch_date"

    inlines = [
        DispatchItemInline,
    ]

    readonly_fields = (
        "status",
        "created_by",
        "posted_by",
        "posted_at",
        "created_at",
    )

    fields = (
        "dispatch_number",
        "sales_order",
        "dispatch_date",
        "status",
        "notes",
        "created_by",
        "posted_by",
        "posted_at",
        "created_at",
    )

    def formfield_for_foreignkey(
        self, db_field, request, **kwargs
    ):

        if db_field.name == "sales_order":
            kwargs["queryset"] = SalesOrder.objects.filter(
                status=SalesOrder.Status.INVOICED
            )

        return super().formfield_for_foreignkey(
            db_field, request, **kwargs
        )

    def save_model(self, request, obj, form, change):

        if not change and not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(
            request, obj, form, change
        )

    @admin.action(description="Post selected draft dispatches")
    def post_selected_dispatches(
        self, request, queryset
    ):

        success_count = 0
        errors = []

        for dispatch in queryset.order_by("pk"):

            try:
                post_dispatch(
                    dispatch_id=dispatch.pk,
                    user=request.user,
                )

                success_count += 1

            except ValidationError as exc:

                errors.append(
                    f"{dispatch.dispatch_number}: "
                    f"{'; '.join(exc.messages)}"
                )

        if success_count:
            self.message_user(
                request,
                f"{success_count} dispatch(es) posted successfully.",
                level=messages.SUCCESS,
            )

        for error in errors:
            self.message_user(
                request,
                error,
                level=messages.ERROR,
            )

    actions = [
        "post_selected_dispatches",
    ]
