
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/reports/management-dashboard/", permanent=False), name="home"),

    # Django administration
    path("admin/", admin.site.urls),

    # User authentication
    path("accounts/", include("django.contrib.auth.urls")),

    # Sales application
    path("sales/", include("sales.urls")),

    # Audit and reporting application
    path("reports/", include("reporting.urls")),

]
