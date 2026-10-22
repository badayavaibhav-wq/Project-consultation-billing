import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone


class ConsultationSession(models.Model):
    """
    Core session model. Tracks the full lifecycle of a 1:1 consultation.

    State machine:
        unassigned → assigned → accepted_by_provider → in_progress → completed

    Cancellations can happen at: unassigned, assigned, accepted_by_provider.
    A session in_progress can only move to completed (never cancelled).

    Billing state is tracked with billed_minutes and billed_amount so we can
    resume safely and avoid double-billing the same minute.
    """

    class State(models.TextChoices):
        UNASSIGNED = 'unassigned', 'Unassigned'
        ASSIGNED = 'assigned', 'Assigned'
        ACCEPTED_BY_PROVIDER = 'accepted_by_provider', 'Accepted by Provider'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    class EndedBy(models.TextChoices):
        USER = 'user', 'User'
        PROVIDER = 'provider', 'Provider'
        SYSTEM = 'system', 'System'

    # Allowed forward transitions
    VALID_TRANSITIONS = {
        State.UNASSIGNED: [State.ASSIGNED, State.CANCELLED],
        State.ASSIGNED: [State.ACCEPTED_BY_PROVIDER, State.CANCELLED],
        State.ACCEPTED_BY_PROVIDER: [State.IN_PROGRESS, State.CANCELLED],
        State.IN_PROGRESS: [State.COMPLETED],
        State.COMPLETED: [],
        State.CANCELLED: [],
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sessions_as_user',
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sessions_as_provider',
    )
    state = models.CharField(
        max_length=30,
        choices=State.choices,
        default=State.UNASSIGNED,
    )

    # Per-minute cost is locked in at session start so price changes don't
    # affect an ongoing session.
    per_minute_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Billing bookkeeping
    billed_minutes = models.IntegerField(default=0)
    billed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    ended_by = models.CharField(
        max_length=20,
        choices=EndedBy.choices,
        null=True,
        blank=True,
    )
    end_reason = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Session({self.id}) {self.user.username} [{self.state}]"

    def can_transition_to(self, new_state):
        return new_state in self.VALID_TRANSITIONS.get(self.state, [])

    def transition_to(self, new_state):
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"Cannot transition from '{self.state}' to '{new_state}'."
            )
        self.state = new_state
        self.save(update_fields=['state', 'updated_at'])

    @property
    def elapsed_minutes(self):
        """
        Number of whole minutes elapsed since session started.
        This is used to figure out how many minutes should have been billed.
        """
        if not self.started_at:
            return 0
        end = self.ended_at or timezone.now()
        delta = end - self.started_at
        return int(delta.total_seconds() // 60)

    @property
    def billable_minutes_due(self):
        """Minutes that have elapsed but haven't been billed yet."""
        return max(0, self.elapsed_minutes - self.billed_minutes)

    @property
    def talk_time_seconds(self):
        if not self.started_at or not self.ended_at:
            return 0
        return int((self.ended_at - self.started_at).total_seconds())


class BillingRecord(models.Model):
    """
    Audit trail for each minute billed. The unique_together constraint on
    (session, minute_number) is the database-level guard against duplicate billing.
    """
    session = models.ForeignKey(
        ConsultationSession,
        on_delete=models.CASCADE,
        related_name='billing_records',
    )
    minute_number = models.IntegerField()
    amount_charged = models.DecimalField(max_digits=10, decimal_places=2)
    charged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'minute_number')
        ordering = ['minute_number']

    def __str__(self):
        return f"Billing(session={self.session_id}, minute={self.minute_number}, ₹{self.amount_charged})"
