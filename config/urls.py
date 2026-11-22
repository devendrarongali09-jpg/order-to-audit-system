
from django.contrib import admin
from django.urls import path, include

urlpatterns = [

    # Django administration
    path("admin/", admin.site.urls),

    # User authentication
    path("accounts/", include("django.contrib.auth.urls")),

    # Sales application
    path("sales/", include("sales.urls")),

    # Audit and reporting application
    path("reports/", include("reporting.urls")),

]
