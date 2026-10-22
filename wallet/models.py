import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal


class Wallet(models.Model):
    """
    One wallet per user. Balance is stored as Decimal to avoid
    floating point weirdness when dealing with money.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet',
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet({self.user.username}) ₹{self.balance}"

    def has_sufficient_balance(self, amount):
        return self.balance >= Decimal(str(amount))

    def deduct(self, amount):
        """
        Deduct amount from wallet. Raises ValueError if balance would go negative.
        The caller is responsible for wrapping this in a select_for_update transaction.
        """
        amount = Decimal(str(amount))
        if self.balance < amount:
            raise ValueError(
                f"Insufficient balance: have ₹{self.balance}, need ₹{amount}"
            )
        self.balance -= amount
        self.save(update_fields=['balance', 'updated_at'])

    def credit(self, amount):
        """Add money to the wallet."""
        self.balance += Decimal(str(amount))
        self.save(update_fields=['balance', 'updated_at'])


class RechargeTransaction(models.Model):
    """
    Tracks a mock recharge request from initiation through callback.
    The idempotency key prevents double-crediting on duplicate callbacks.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recharge_transactions',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    reference = models.CharField(max_length=100, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # This flag is the guard against double-crediting
    wallet_credited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Recharge({self.reference}) ₹{self.amount} [{self.status}]"
