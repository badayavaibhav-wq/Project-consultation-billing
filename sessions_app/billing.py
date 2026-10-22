"""
Billing service for per-minute session charges.
"""
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.utils import timezone

from wallet.models import Wallet
from .models import ConsultationSession, BillingRecord


def run_billing(session_id):
    """
    Bill all unbilled whole minutes for an in-progress session.

    Returns a dict describing what happened so callers (views, management
    commands, periodic tasks) can log or return it.
    """
    with transaction.atomic():
        try:
            session = ConsultationSession.objects.select_for_update().get(
                id=session_id,
                state=ConsultationSession.State.IN_PROGRESS,
            )
        except ConsultationSession.DoesNotExist:
            return {"error": "Session not found or not in progress."}

        wallet = Wallet.objects.select_for_update().get(user=session.user)

        minutes_due = session.billable_minutes_due
        if minutes_due == 0:
            return {
                "billed_minutes": 0,
                "amount_charged": "0.00",
                "session_state": session.state,
                "message": "No new minutes to bill.",
            }

        total_charged = Decimal("0.00")
        billed_count = 0
        session_ended_by_system = False

        for i in range(minutes_due):
            minute_number = session.billed_minutes + 1 + i
            cost = session.per_minute_cost

            if wallet.balance < cost:
                # Can't pay for this minute — end the session
                session.state = ConsultationSession.State.COMPLETED
                session.ended_at = timezone.now()
                session.ended_by = ConsultationSession.EndedBy.SYSTEM
                session.end_reason = "Insufficient Balance"
                session.save(update_fields=[
                    'state', 'ended_at', 'ended_by', 'end_reason', 'updated_at'
                ])
                session_ended_by_system = True
                break

            # Try to create the billing record — ignore if already exists
            try:
                BillingRecord.objects.create(
                    session=session,
                    minute_number=minute_number,
                    amount_charged=cost,
                )
            except IntegrityError:
                # Already billed this minute (duplicate call), skip
                continue

            wallet.deduct(cost)
            total_charged += cost
            billed_count += 1

        # Update session billing counters
        if billed_count > 0:
            session.billed_minutes += billed_count
            session.billed_amount += total_charged
            session.save(update_fields=['billed_minutes', 'billed_amount', 'updated_at'])

        return {
            "billed_minutes": billed_count,
            "amount_charged": str(total_charged),
            "wallet_balance": str(wallet.balance),
            "session_state": session.state,
            "ended_by_system": session_ended_by_system,
            "message": "Insufficient balance — session ended by system."
            if session_ended_by_system
            else f"Billed {billed_count} minute(s).",
        }
