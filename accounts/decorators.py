
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def group_required(*group_names):
    """
    Allow access only to authenticated users belonging
    to at least one of the specified Django groups.

    Superusers retain full access.
    Unauthenticated users are redirected to login.
    Authenticated users without an allowed role receive 403.
    """

    if not group_names:
        raise ValueError(
            "At least one group name must be provided."
        )

    def decorator(view_func):

        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):

            user = request.user

            if not user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path()
                )

            if user.is_superuser:
                return view_func(request, *args, **kwargs)

            has_allowed_group = user.groups.filter(
                name__in=group_names
            ).exists()

            if not has_allowed_group:
                raise PermissionDenied(
                    "You do not have permission to access this page."
                )

            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator
