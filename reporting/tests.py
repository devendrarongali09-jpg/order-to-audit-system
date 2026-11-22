from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


class ManagementDashboardTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="reporting_test",
            password="TestPassword123!"
        )

        factory_admin_group, created = Group.objects.get_or_create(
            name="Factory Admin"
        )
        self.user.groups.add(factory_admin_group)

        self.dashboard_url = reverse("reporting:management_dashboard")

    def test_dashboard_requires_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)

    def test_dashboard_loads_for_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "reporting/management_dashboard.html"
        )

    def test_dashboard_accepts_valid_date_filters(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "2026-11-21",
                "to_date": "2026-11-22",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["from_date"], "2026-11-21")
        self.assertEqual(response.context["to_date"], "2026-11-22")

    def test_dashboard_rejects_invalid_date(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "invalid-date",
                "to_date": "2026-11-22",
            }
        )

        self.assertEqual(response.status_code, 400)

    def test_dashboard_rejects_reversed_date_range(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.dashboard_url,
            {
                "from_date": "2026-11-22",
                "to_date": "2026-11-21",
            }
        )

        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_without_role_gets_forbidden(self):
        User = get_user_model()

        unauthorized_user = User.objects.create_user(
            username="unauthorized_test",
            password="TestPassword123!"
        )

        self.client.force_login(unauthorized_user)

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 403)