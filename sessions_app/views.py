from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from wallet.models import Wallet
from .billing import run_billing
from .models import ConsultationSession, BillingRecord
from .permissions import IsProviderUser, IsRegularUser
from .serializers import (
    SessionSerializer,
    CreateSessionSerializer,
    AssignProviderSerializer,
    BillingRecordSerializer,
)

User = get_user_model()

PER_MINUTE_COST = Decimal(str(getattr(settings, 'SESSION_PER_MINUTE_COST', 50)))
MIN_BALANCE_MULTIPLIER = getattr(settings, 'SESSION_MIN_BALANCE_MULTIPLIER', 5)


class SessionListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/sessions/         - List sessions for the current user.
    POST /api/sessions/         - Create a new session (user role only).
    """
    serializer_class = SessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_provider:
            return ConsultationSession.objects.filter(provider=user)
        return ConsultationSession.objects.filter(user=user)

    def create(self, request, *args, **kwargs):
        if not request.user.is_regular_user:
            return Response(
                {"error": "Only users (not providers) can create sessions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        provider = None
        provider_id = serializer.validated_data.get('provider_id')
        if provider_id:
            try:
                provider = User.objects.get(id=provider_id, role='provider')
            except User.DoesNotExist:
                return Response(
                    {"error": "Provider not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        initial_state = (
            ConsultationSession.State.ASSIGNED
            if provider
            else ConsultationSession.State.UNASSIGNED
        )

        session = ConsultationSession.objects.create(
            user=request.user,
            provider=provider,
            state=initial_state,
        )

        return Response(
            SessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )


class SessionDetailView(generics.RetrieveAPIView):
    """GET /api/sessions/<id>/"""
    serializer_class = SessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_provider:
            return ConsultationSession.objects.filter(provider=user)
        return ConsultationSession.objects.filter(user=user)


class AssignProviderView(APIView):
    """
    POST /api/sessions/<id>/assign/
    Assigns a provider to an unassigned session (user action).
    """
    permission_classes = [permissions.IsAuthenticated, IsRegularUser]

    def post(self, request, pk):
        serializer = AssignProviderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            session = ConsultationSession.objects.get(id=pk, user=request.user)
        except ConsultationSession.DoesNotExist:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.state != ConsultationSession.State.UNASSIGNED:
            return Response(
                {"error": f"Cannot assign provider when session is in '{session.state}' state."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            provider = User.objects.get(
                id=serializer.validated_data['provider_id'],
                role='provider',
            )
        except User.DoesNotExist:
            return Response({"error": "Provider not found."}, status=status.HTTP_400_BAD_REQUEST)

        session.provider = provider
        session.state = ConsultationSession.State.ASSIGNED
        session.save(update_fields=['provider', 'state', 'updated_at'])

        return Response(SessionSerializer(session).data)


class ProviderAcceptView(APIView):
    """
    POST /api/sessions/<id>/accept/
    Provider accepts the session. Moves from assigned → accepted_by_provider.
    """
    permission_classes = [permissions.IsAuthenticated, IsProviderUser]

    def post(self, request, pk):
        try:
            session = ConsultationSession.objects.get(id=pk, provider=request.user)
        except ConsultationSession.DoesNotExist:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            session.transition_to(ConsultationSession.State.ACCEPTED_BY_PROVIDER)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SessionSerializer(session).data)


class StartSessionView(APIView):
    """
    POST /api/sessions/<id>/start/
    User starts the session. Moves from accepted_by_provider → in_progress.

    Checks:
    - Session must be accepted_by_provider.
    - User must not have another in_progress session.
    - Wallet balance must be >= 5 * per_minute_cost.
    """
    permission_classes = [permissions.IsAuthenticated, IsRegularUser]

    def post(self, request, pk):
        try:
            session = ConsultationSession.objects.get(id=pk, user=request.user)
        except ConsultationSession.DoesNotExist:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.state != ConsultationSession.State.ACCEPTED_BY_PROVIDER:
            return Response(
                {"error": f"Cannot start session from state '{session.state}'. Expected 'accepted_by_provider'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # One active session at a time
        active_exists = ConsultationSession.objects.filter(
            user=request.user,
            state=ConsultationSession.State.IN_PROGRESS,
        ).exists()
        if active_exists:
            return Response(
                {"error": "You already have an active session in progress."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Minimum balance check
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        min_required = PER_MINUTE_COST * MIN_BALANCE_MULTIPLIER
        if wallet.balance < min_required:
            return Response(
                {
                    "error": (
                        f"Insufficient balance to start session. "
                        f"Minimum required: ₹{min_required} "
                        f"(5 × ₹{PER_MINUTE_COST} per minute). "
                        f"Current balance: ₹{wallet.balance}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            session.state = ConsultationSession.State.IN_PROGRESS
            session.started_at = timezone.now()
            session.per_minute_cost = PER_MINUTE_COST
            session.save(update_fields=['state', 'started_at', 'per_minute_cost', 'updated_at'])

        return Response(SessionSerializer(session).data)


class EndSessionView(APIView):
    """
    POST /api/sessions/<id>/end/
    Either the user or provider can end the session.
    Triggers final billing before marking the session completed.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        user = request.user

        if user.is_provider:
            try:
                session = ConsultationSession.objects.get(id=pk, provider=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
            ender = ConsultationSession.EndedBy.PROVIDER
        else:
            try:
                session = ConsultationSession.objects.get(id=pk, user=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
            ender = ConsultationSession.EndedBy.USER

        if session.state != ConsultationSession.State.IN_PROGRESS:
            return Response(
                {"error": f"Cannot end a session that is in '{session.state}' state. Session must be 'in_progress'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Bill any remaining minutes first
            billing_result = run_billing(session.id)

            # Reload session because billing might have ended it already
            session.refresh_from_db()

            if session.state == ConsultationSession.State.IN_PROGRESS:
                # System didn't end it, so we mark it completed manually
                session.state = ConsultationSession.State.COMPLETED
                session.ended_at = timezone.now()
                session.ended_by = ender
                session.end_reason = f"Ended by {ender}"
                session.save(update_fields=['state', 'ended_at', 'ended_by', 'end_reason', 'updated_at'])

        return Response(
            {
                "session": SessionSerializer(session).data,
                "billing": billing_result,
            }
        )


class BillSessionView(APIView):
    """
    POST /api/sessions/<id>/bill/
    Manually trigger billing for any unbilled minutes.
    Useful for periodic billing during a long session.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        user = request.user

        if user.is_provider:
            try:
                session = ConsultationSession.objects.get(id=pk, provider=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            try:
                session = ConsultationSession.objects.get(id=pk, user=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.state != ConsultationSession.State.IN_PROGRESS:
            return Response(
                {"error": "Session is not in progress. Nothing to bill."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = run_billing(session.id)
        session.refresh_from_db()

        return Response({
            "billing_result": result,
            "session": SessionSerializer(session).data,
        })


class SessionBillingHistoryView(generics.ListAPIView):
    """
    GET /api/sessions/<id>/billing/
    Returns all billing records for a session.
    """
    serializer_class = BillingRecordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        pk = self.kwargs['pk']
        if user.is_provider:
            session_filter = {'provider': user}
        else:
            session_filter = {'user': user}

        try:
            session = ConsultationSession.objects.get(id=pk, **session_filter)
            return BillingRecord.objects.filter(session=session)
        except ConsultationSession.DoesNotExist:
            return BillingRecord.objects.none()


class CancelSessionView(APIView):
    """
    POST /api/sessions/<id>/cancel/
    Cancel a session before it has started.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        user = request.user

        if user.is_provider:
            try:
                session = ConsultationSession.objects.get(id=pk, provider=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            try:
                session = ConsultationSession.objects.get(id=pk, user=user)
            except ConsultationSession.DoesNotExist:
                return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            session.transition_to(ConsultationSession.State.CANCELLED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SessionSerializer(session).data)
