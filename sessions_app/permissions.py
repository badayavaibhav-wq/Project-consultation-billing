from rest_framework.permissions import BasePermission


class IsProviderUser(BasePermission):
    """Only users with role=provider can pass."""
    message = "This action requires a provider account."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_provider)


class IsRegularUser(BasePermission):
    """Only users with role=user can pass."""
    message = "This action requires a regular user account."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_regular_user)
