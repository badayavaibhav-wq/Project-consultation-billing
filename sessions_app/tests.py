"""
Test suite for the wallet-based per-minute session billing system.

Covers all 6 required scenarios:
  1. Insufficient balance to start session
  2. Happy path: recharge → create → accept → start → bill → end
  3. Balance runs out during session
  4. Invalid state transition
  5. Duplicate recharge callback
  6. Duplicate billing for the same minute
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from sessions_app.billing import run_billing
from sessions_app.models import BillingRecord, ConsultationSession
from wallet.models import RechargeTransaction, Wallet

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_tokens(user):
    """Return JWT access token string for the given user."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


def auth_headers(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {get_tokens(user)}"}


def create_user(username, role="user", password="testpass123"):
    return User.objects.create_user(username=username, password=password, role=role)


def give_wallet(user, balance):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    wallet.balance = Decimal(str(balance))
    wallet.save()
    return wallet


def advance_session_clock(session, minutes):
    """
    Helper to simulate time passing during a session by backdating started_at.
    We patch started_at in the DB directly so elapsed_minutes returns the right value.
    """
    session.started_at = timezone.now() - timedelta(minutes=minutes, seconds=1)
    session.save(update_fields=["started_at"])


def create_session_in_progress(user, provider, balance=500):
    """
    Utility to quickly get a session all the way to in_progress state
    with a funded wallet. Returns (session, wallet).
    """
    wallet = give_wallet(user, balance)

    session = ConsultationSession.objects.create(
        user=user,
        provider=provider,
        state=ConsultationSession.State.ACCEPTED_BY_PROVIDER,
    )

    # Start the session directly
    session.state = ConsultationSession.State.IN_PROGRESS
    session.started_at = timezone.now()
    session.per_minute_cost = Decimal("50.00")
    session.save()

    return session, wallet


# ===========================================================================
# 1. Wallet model unit tests
# ===========================================================================


class WalletModelTest(TestCase):
    def setUp(self):
        self.user = create_user("walletuser")
        self.wallet = give_wallet(self.user, 100)

    def test_deduct_reduces_balance(self):
        self.wallet.deduct(40)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("60.00"))

    def test_deduct_raises_on_insufficient_funds(self):
        with self.assertRaises(ValueError):
            self.wallet.deduct(200)

    def test_balance_never_goes_negative(self):
        with self.assertRaises(ValueError):
            self.wallet.deduct(101)
        self.wallet.refresh_from_db()
        self.assertGreaterEqual(self.wallet.balance, Decimal("0.00"))

    def test_credit_increases_balance(self):
        self.wallet.credit(50)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))


# ===========================================================================
# 2. Auth API tests
# ===========================================================================


class AuthAPITest(APITestCase):
    def test_register_user(self):
        url = reverse("auth-register")
        data = {"username": "newuser", "password": "pass1234", "role": "user"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_register_provider(self):
        url = reverse("auth-register")
        data = {"username": "newprovider", "password": "pass1234", "role": "provider"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "provider")

    def test_login_returns_tokens(self):
        create_user("loginuser")
        url = reverse("auth-login")
        response = self.client.post(url, {"username": "loginuser", "password": "testpass123"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


# ===========================================================================
# 3. Wallet API tests
# ===========================================================================


class WalletAPITest(APITestCase):
    def setUp(self):
        self.user = create_user("walletapiuser")
        self.wallet = give_wallet(self.user, 0)

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")

    def test_get_wallet_balance(self):
        self._auth()
        give_wallet(self.user, 200)
        response = self.client.get(reverse("wallet-detail"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data["balance"]), Decimal("200.00"))

    def test_unauthenticated_wallet_access_denied(self):
        response = self.client.get(reverse("wallet-detail"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ===========================================================================
# TEST SCENARIO 5: Duplicate recharge callback
# ===========================================================================


class DuplicateRechargeCallbackTest(APITestCase):
    """
    The same successful callback may arrive more than once.
    The wallet must be credited only once.
    """

    def setUp(self):
        self.user = create_user("rechargeusr")
        self.wallet = give_wallet(self.user, 0)
        self.reference = str(uuid.uuid4())

        RechargeTransaction.objects.create(
            user=self.user,
            amount=Decimal("500.00"),
            reference=self.reference,
            status=RechargeTransaction.Status.PENDING,
        )

    def _send_callback(self, ref, txn_status):
        return self.client.post(
            reverse("recharge-callback"),
            {"reference": ref, "status": txn_status},
            format="json",
        )

    def test_duplicate_success_callback_credits_once(self):
        """Sending the success callback twice must credit the wallet only once."""
        r1 = self._send_callback(self.reference, "success")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertTrue(r1.data["credited"])

        r2 = self._send_callback(self.reference, "success")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertFalse(r2.data["credited"])

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

    def test_full_recharge_flow_via_api(self):
        """Initiate → callback → verify balance."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        init_resp = self.client.post(
            reverse("recharge-initiate"),
            {"amount": "300"},
            format="json",
        )
        self.assertEqual(init_resp.status_code, status.HTTP_201_CREATED)

        ref = init_resp.data["transaction"]["reference"]
        cb_resp = self._send_callback(ref, "success")
        self.assertEqual(cb_resp.status_code, status.HTTP_200_OK)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("300.00"))

    def test_failed_callback_does_not_credit(self):
        r = self._send_callback(self.reference, "failed")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.data["credited"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0.00"))


# ===========================================================================
# TEST SCENARIO 1: Insufficient balance to start session
# ===========================================================================


class InsufficientBalanceStartTest(APITestCase):
    """
    A user with less than 5 × per_minute_cost cannot start a session.
    Per-minute cost = ₹50, so minimum required = ₹250.
    """

    def setUp(self):
        self.user = create_user("pooruser")
        self.provider = create_user("provider1", role="provider")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")

    def _create_session_ready_to_start(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ACCEPTED_BY_PROVIDER,
        )
        return session

    def test_cannot_start_with_zero_balance(self):
        give_wallet(self.user, 0)
        session = self._create_session_ready_to_start()
        response = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient balance", response.data["error"])

    def test_cannot_start_with_less_than_5_minutes_worth(self):
        # ₹200 < ₹250 (5 × ₹50)
        give_wallet(self.user, 200)
        session = self._create_session_ready_to_start()
        response = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient balance", response.data["error"])

    def test_can_start_with_exactly_5_minutes_worth(self):
        # ₹250 == ₹250 (exactly 5 × ₹50) — should succeed
        give_wallet(self.user, 250)
        session = self._create_session_ready_to_start()
        response = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["state"], "in_progress")

    def test_can_start_with_more_than_5_minutes_worth(self):
        give_wallet(self.user, 500)
        session = self._create_session_ready_to_start()
        response = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ===========================================================================
# TEST SCENARIO 2: Happy path
# ===========================================================================


class HappyPathTest(APITestCase):
    """
    Recharge → create session → provider accepts → start session
    → billing for 2 minutes → end session.
    Verify wallet is debited correctly (2 × ₹50 = ₹100).
    """

    def setUp(self):
        self.user = create_user("happyuser")
        self.provider = create_user("happyprovider", role="provider")
        self.wallet = give_wallet(self.user, 0)

    def test_full_happy_path(self):
        # Step 1: Recharge wallet
        recharge_ref = str(uuid.uuid4())
        RechargeTransaction.objects.create(
            user=self.user,
            amount=Decimal("500.00"),
            reference=recharge_ref,
            status=RechargeTransaction.Status.PENDING,
        )
        self.client.post(
            reverse("recharge-callback"),
            {"reference": recharge_ref, "status": "success"},
            format="json",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        # Step 2: Create session with provider
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ASSIGNED,
        )

        # Step 3: Provider accepts
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.provider)}")
        resp = self.client.post(reverse("session-accept", kwargs={"pk": session.id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["state"], "accepted_by_provider")

        # Step 4: User starts session
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        resp = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["state"], "in_progress")

        # Step 5: Simulate 2 minutes passing then trigger billing
        session.refresh_from_db()
        advance_session_clock(session, 2)

        billing_result = run_billing(session.id)
        self.assertEqual(billing_result["billed_minutes"], 2)
        self.assertEqual(Decimal(billing_result["amount_charged"]), Decimal("100.00"))

        # Step 6: End session
        resp = self.client.post(reverse("session-end", kwargs={"pk": session.id}))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["session"]["state"], "completed")

        # Verify wallet was debited exactly ₹100
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("400.00"))

        # Verify billing records exist
        records = BillingRecord.objects.filter(session=session)
        self.assertEqual(records.count(), 2)


# ===========================================================================
# TEST SCENARIO 3: Balance runs out during active session
# ===========================================================================


class BalanceRunsOutTest(APITestCase):
    """
    Wallet has ₹40, per-minute cost is ₹50.
    Billing cannot cover the next minute → session ends automatically.
    Wallet stays at ₹40. ended_by = system.
    """

    def setUp(self):
        self.user = create_user("brokeuser")
        self.provider = create_user("brokeprovider", role="provider")

    def test_system_ends_session_when_balance_insufficient(self):
        # Give exactly ₹40 — cannot pay ₹50/min
        session, wallet = create_session_in_progress(self.user, self.provider, balance=40)
        advance_session_clock(session, 1)

        result = run_billing(session.id)

        self.assertTrue(result["ended_by_system"])
        self.assertEqual(Decimal(result["amount_charged"]), Decimal("0.00"))

        session.refresh_from_db()
        self.assertEqual(session.state, ConsultationSession.State.COMPLETED)
        self.assertEqual(session.ended_by, ConsultationSession.EndedBy.SYSTEM)
        self.assertIn("Insufficient Balance", session.end_reason)

        wallet.refresh_from_db()
        self.assertGreaterEqual(wallet.balance, Decimal("0.00"))
        # Balance must remain ₹40 — nothing should have been deducted
        self.assertEqual(wallet.balance, Decimal("40.00"))

    def test_wallet_never_goes_negative_on_auto_end(self):
        session, wallet = create_session_in_progress(self.user, self.provider, balance=30)
        advance_session_clock(session, 3)

        run_billing(session.id)

        wallet.refresh_from_db()
        self.assertGreaterEqual(wallet.balance, Decimal("0.00"))

    def test_correct_ending_reason_recorded(self):
        session, wallet = create_session_in_progress(self.user, self.provider, balance=40)
        advance_session_clock(session, 1)

        run_billing(session.id)

        session.refresh_from_db()
        self.assertEqual(session.ended_by, ConsultationSession.EndedBy.SYSTEM)
        self.assertEqual(session.end_reason, "Insufficient Balance")

    def test_partial_billing_then_runs_out(self):
        """
        Wallet has ₹100 (can cover 2 minutes at ₹50 each, but not the 3rd).
        After 3 minutes elapsed, only 2 should be billed then session ends.
        """
        session, wallet = create_session_in_progress(self.user, self.provider, balance=100)
        advance_session_clock(session, 3)

        result = run_billing(session.id)

        self.assertEqual(result["billed_minutes"], 2)
        self.assertEqual(Decimal(result["amount_charged"]), Decimal("100.00"))
        self.assertTrue(result["ended_by_system"])

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("0.00"))


# ===========================================================================
# TEST SCENARIO 4: Invalid state transitions
# ===========================================================================


class InvalidStateTransitionTest(APITestCase):
    """
    Attempt invalid actions and verify they return HTTP 400 with a useful error.
    """

    def setUp(self):
        self.user = create_user("stateuser")
        self.provider = create_user("stateprovider", role="provider")
        give_wallet(self.user, 500)

    def test_cannot_end_unstarted_session(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ASSIGNED,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-end", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_end_unassigned_session(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            state=ConsultationSession.State.UNASSIGNED,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-end", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_start_session_not_accepted(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ASSIGNED,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-start", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("accepted_by_provider", response.data["error"])

    def test_cannot_accept_unassigned_session(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            state=ConsultationSession.State.UNASSIGNED,
            provider=self.provider,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.provider)}")
        response = self.client.post(reverse("session-accept", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_cancel_in_progress_session(self):
        session, _ = create_session_in_progress(self.user, self.provider, balance=500)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-cancel", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_state_machine_model_raises_on_invalid_transition(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            state=ConsultationSession.State.COMPLETED,
        )
        with self.assertRaises(ValueError) as ctx:
            session.transition_to(ConsultationSession.State.IN_PROGRESS)
        self.assertIn("Cannot transition", str(ctx.exception))

    def test_one_active_session_per_user(self):
        """A user cannot have two in_progress sessions at once."""
        # First session in progress
        create_session_in_progress(self.user, self.provider, balance=500)

        # Try to start a second one
        session2 = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ACCEPTED_BY_PROVIDER,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-start", kwargs={"pk": session2.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already have an active session", response.data["error"])


# ===========================================================================
# TEST SCENARIO 6: Duplicate billing for the same minute
# ===========================================================================


class DuplicateBillingTest(TestCase):
    """
    Triggering billing more than once for the same billable minute
    should only charge the user once.
    """

    def setUp(self):
        self.user = create_user("dupbilluser")
        self.provider = create_user("dupbillprovider", role="provider")

    def test_duplicate_billing_charges_once(self):
        session, wallet = create_session_in_progress(self.user, self.provider, balance=500)
        advance_session_clock(session, 1)

        # Call billing twice
        result1 = run_billing(session.id)
        result2 = run_billing(session.id)

        # First call should bill minute 1
        self.assertEqual(result1["billed_minutes"], 1)
        self.assertEqual(Decimal(result1["amount_charged"]), Decimal("50.00"))

        # Second call has nothing new to bill
        self.assertEqual(result2["billed_minutes"], 0)
        self.assertEqual(Decimal(result2["amount_charged"]), Decimal("0.00"))

        # Wallet debited exactly once
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("450.00"))

        # Only one billing record
        self.assertEqual(BillingRecord.objects.filter(session=session).count(), 1)

    def test_billing_record_unique_constraint(self):
        """DB-level protection: inserting the same minute twice raises IntegrityError."""
        from django.db import IntegrityError

        session, _ = create_session_in_progress(self.user, self.provider, balance=500)
        BillingRecord.objects.create(
            session=session,
            minute_number=1,
            amount_charged=Decimal("50.00"),
        )
        with self.assertRaises(IntegrityError):
            BillingRecord.objects.create(
                session=session,
                minute_number=1,
                amount_charged=Decimal("50.00"),
            )


# ===========================================================================
# Additional edge cases
# ===========================================================================


class BillingEdgeCasesTest(TestCase):
    def setUp(self):
        self.user = create_user("edgeuser")
        self.provider = create_user("edgeprovider", role="provider")

    def test_billing_non_in_progress_session_returns_error(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.COMPLETED,
        )
        result = run_billing(session.id)
        self.assertIn("error", result)

    def test_elapsed_minutes_zero_before_start(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            state=ConsultationSession.State.UNASSIGNED,
        )
        self.assertEqual(session.elapsed_minutes, 0)

    def test_per_minute_cost_locked_at_start(self):
        """Changing the settings constant after session starts should not affect billing."""
        session, wallet = create_session_in_progress(self.user, self.provider, balance=500)
        locked_cost = session.per_minute_cost

        # Simulate settings change — doesn't affect already-started session
        advance_session_clock(session, 1)
        run_billing(session.id)

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("500.00") - locked_cost)

    def test_talk_time_recorded_on_auto_end(self):
        session, wallet = create_session_in_progress(self.user, self.provider, balance=40)
        advance_session_clock(session, 1)
        run_billing(session.id)
        session.refresh_from_db()
        # Session should have ended_at populated
        self.assertIsNotNone(session.ended_at)

    def test_billing_view_returns_400_for_completed_session(self):
        from django.test import RequestFactory
        from rest_framework.test import APIClient

        session, _ = create_session_in_progress(self.user, self.provider, balance=500)
        session.state = ConsultationSession.State.COMPLETED
        session.save()

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = client.post(reverse("session-bill", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SessionPermissionTest(APITestCase):
    def setUp(self):
        self.user = create_user("permuser")
        self.provider = create_user("permprovider", role="provider")

    def test_provider_cannot_create_session(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.provider)}")
        response = self.client.post(reverse("session-list-create"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_cannot_accept_session(self):
        session = ConsultationSession.objects.create(
            user=self.user,
            provider=self.provider,
            state=ConsultationSession.State.ASSIGNED,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {get_tokens(self.user)}")
        response = self.client.post(reverse("session-accept", kwargs={"pk": session.id}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
