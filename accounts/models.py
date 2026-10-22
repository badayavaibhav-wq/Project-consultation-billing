from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model. Extends Django's built-in AbstractUser
    so we can attach a role to each account without much ceremony.
    """

    class Role(models.TextChoices):
        USER = 'user', 'User'
        PROVIDER = 'provider', 'Provider'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
    )

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def is_provider(self):
        return self.role == self.Role.PROVIDER

    @property
    def is_regular_user(self):
        return self.role == self.Role.USER
