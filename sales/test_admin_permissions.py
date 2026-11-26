from datetime import date

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase

from masterdata.models import Customer
from sales.admin import SalesOrderAdmin, SalesOrderItemInline
from sales.models import SalesOrder


User = get_user_model()


class SalesOrderAdminOwnershipTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

        self.admin = SalesOrderAdmin(
            SalesOrder,
            AdminSite()
        )

        # Give Salesman the necessary model permissions.
        content_type = ContentType.objects.get_for_model(
            SalesOrder
        )

        permissions = list(
            Permission.objects.filter(
                content_type=content_type,
                codename__in=[
                    "add_salesorder",
                    "view_salesorder",
                    "change_salesorder",
                ]
            )
        )

        self.assertEqual(len(permissions), 3)

        self.salesman_group = Group.objects.create(
            name="Salesman"
        )
        self.salesman_group.permissions.set(permissions)

        # Create two salesmen.
        self.salesman = User.objects.create_user(
            username="salesman_one",
            password="TestPassword123!"
        )

        self.other_salesman = User.objects.create_user(
            username="salesman_two",
            password="TestPassword123!"
        )

        self.salesman.groups.add(self.salesman_group)
        self.other_salesman.groups.add(self.salesman_group)

        # Create a Factory Admin.
        self.factory_group = Group.objects.create(
            name="Factory Admin"
        )
        self.factory_group.permissions.set(permissions)

        self.factory_admin = User.objects.create_user(
            username="factory_admin_test",
            password="TestPassword123!"
        )
        self.factory_admin.groups.add(self.factory_group)

        # Create a customer.
        self.customer = Customer.objects.create(
            name="Ownership Test Retailer",
            customer_type=Customer.CustomerType.RETAILER,
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
        )

        # Create orders belonging to different salesmen.
        self.own_order = self.create_order(
            "SO-OWN-001",
            self.salesman,
        )

        self.other_order = self.create_order(
            "SO-OTHER-001",
            self.other_salesman,
        )

        self.submitted_order = self.create_order(
            "SO-SUBMITTED-001",
            self.salesman,
            SalesOrder.Status.SUBMITTED,
        )

    def create_order(
        self,
        order_number,
        creator,
        status=SalesOrder.Status.DRAFT,
    ):
        return SalesOrder.objects.create(
            order_number=order_number,
            customer=self.customer,
            created_by=creator,
            order_date=date(2026, 11, 22),
            status=status,
        )

    def make_request(self, user):
        request = self.factory.get(
            "/admin/sales/salesorder/"
        )
        request.user = user
        return request

    def test_salesman_queryset_contains_only_own_orders(self):
        request = self.make_request(self.salesman)

        order_ids = set(
            self.admin.get_queryset(request).values_list(
                "pk",
                flat=True,
            )
        )

        self.assertEqual(
            order_ids,
            {
                self.own_order.pk,
                self.submitted_order.pk,
            }
        )

    def test_salesman_can_view_own_order(self):
        request = self.make_request(self.salesman)

        self.assertTrue(
            self.admin.has_view_permission(
                request,
                self.own_order,
            )
        )

    def test_salesman_cannot_view_another_salesmans_order(self):
        request = self.make_request(self.salesman)

        self.assertFalse(
            self.admin.has_view_permission(
                request,
                self.other_order,
            )
        )

    def test_salesman_can_change_own_draft_order(self):
        request = self.make_request(self.salesman)

        self.assertTrue(
            self.admin.has_change_permission(
                request,
                self.own_order,
            )
        )

    def test_salesman_cannot_change_another_salesmans_order(self):
        request = self.make_request(self.salesman)

        self.assertFalse(
            self.admin.has_change_permission(
                request,
                self.other_order,
            )
        )

    def test_salesman_cannot_change_submitted_order(self):
        request = self.make_request(self.salesman)

        self.assertFalse(
            self.admin.has_change_permission(
                request,
                self.submitted_order,
            )
        )

    def test_salesman_cannot_approve_or_generate_invoices(self):
        request = self.make_request(self.salesman)

        actions = self.admin.get_actions(request)

        self.assertIn("submit_selected_orders", actions)
        self.assertNotIn("approve_selected_orders", actions)
        self.assertNotIn("generate_selected_invoices", actions)

    def test_factory_admin_can_access_all_orders(self):
        request = self.make_request(self.factory_admin)

        order_ids = set(
            self.admin.get_queryset(request).values_list(
                "pk",
                flat=True,
            )
        )

        self.assertEqual(
            order_ids,
            {
                self.own_order.pk,
                self.other_order.pk,
                self.submitted_order.pk,
            }
        )

        self.assertTrue(
            self.admin.has_change_permission(
                request,
                self.other_order,
            )
        )

    def test_new_order_creator_is_logged_in_salesman(self):
        request = self.make_request(self.salesman)

        new_order = SalesOrder(
            order_number="SO-NEW-001",
            customer=self.customer,
            created_by=self.other_salesman,
            order_date=date(2026, 11, 22),
        )

        self.admin.save_model(
            request,
            new_order,
            form=None,
            change=False,
        )

        new_order.refresh_from_db()

        self.assertEqual(
            new_order.created_by,
            self.salesman,
        )

    def test_salesman_with_accountant_group_remains_restricted(self):
        accountant_group = Group.objects.create(
            name="Accountant/Auditor"
        )

        self.salesman.groups.add(accountant_group)

        request = self.make_request(self.salesman)

        order_ids = set(
            self.admin.get_queryset(request).values_list(
                "pk",
                flat=True,
            )
        )

        self.assertEqual(
            order_ids,
            {
                self.own_order.pk,
                self.submitted_order.pk,
            }
        )

        self.assertFalse(
            self.admin.has_change_permission(
                request,
                self.other_order,
            )
        )
    def test_factory_admin_can_adjust_submitted_order_quantity_and_price(self):
        request = self.make_request(self.factory_admin)
        inline = SalesOrderItemInline(SalesOrder, AdminSite())

        readonly_fields = inline.get_readonly_fields(
            request,
            self.submitted_order,
        )

        self.assertEqual(readonly_fields, ("product",))

    def test_salesman_cannot_adjust_submitted_order_items(self):
        request = self.make_request(self.salesman)
        inline = SalesOrderItemInline(SalesOrder, AdminSite())

        readonly_fields = inline.get_readonly_fields(
            request,
            self.submitted_order,
        )

        self.assertEqual(
            readonly_fields,
            ("product", "quantity", "unit_price"),
        )

    def test_factory_admin_cannot_adjust_invoiced_order_items(self):
        request = self.make_request(self.factory_admin)
        inline = SalesOrderItemInline(SalesOrder, AdminSite())

        invoiced_order = self.create_order(
            "SO-INVOICED-001",
            self.salesman,
            SalesOrder.Status.INVOICED,
        )

        readonly_fields = inline.get_readonly_fields(
            request,
            invoiced_order,
        )

        self.assertEqual(
            readonly_fields,
            ("product", "quantity", "unit_price"),
        )