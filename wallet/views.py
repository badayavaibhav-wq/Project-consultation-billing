import uuid
from django.db import transaction
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Wallet, RechargeTransaction
from .serializers import (
    WalletSerializer,
    RechargeInitiateSerializer,
    RechargeCallbackSerializer,
    RechargeTransactionSerializer,
)


class WalletDetailView(generics.RetrieveAPIView):
    """
    GET /api/wallet/
    Returns the authenticated user's wallet balance.
    """
    serializer_class = WalletSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
        return wallet


class RechargeInitiateView(APIView):
    """
    POST /api/wallet/recharge/initiate/
    Creates a pending recharge transaction and returns a reference
    that the mock payment gateway would use in its callback.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = RechargeInitiateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        amount = serializer.validated_data['amount']
        reference = str(uuid.uuid4())

        txn = RechargeTransaction.objects.create(
            user=request.user,
            amount=amount,
            reference=reference,
            status=RechargeTransaction.Status.PENDING,
        )

        return Response(
            {
                "message": "Recharge initiated. Use the reference to complete payment.",
                "transaction": RechargeTransactionSerializer(txn).data,
            },
            status=status.HTTP_201_CREATED,
        )


class RechargeCallbackView(APIView):
    """
    POST /api/wallet/recharge/callback/
    Simulates the webhook from a payment gateway.

    Idempotency guarantee: even if the same 'success' callback arrives
    multiple times, the wallet is credited only once thanks to the
    wallet_credited flag updated inside a select_for_update transaction.
    """
    # In a real system this would be secured by a shared secret / signature.
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RechargeCallbackSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        reference = serializer.validated_data['reference']
        callback_status = serializer.validated_data['status']

        try:
            with transaction.atomic():
                # Lock the row so concurrent duplicate callbacks can't slip through
                txn = RechargeTransaction.objects.select_for_update().get(
                    reference=reference
                )

                if txn.status == RechargeTransaction.Status.SUCCESS:
                    # Already processed — return 200 so the gateway doesn't retry
                    return Response(
                        {"message": "Transaction already processed.", "credited": False},
                        status=status.HTTP_200_OK,
                    )

                txn.status = callback_status
                txn.save(update_fields=['status', 'updated_at'])

                if callback_status == 'success' and not txn.wallet_credited:
                    wallet, _ = Wallet.objects.select_for_update().get_or_create(
                        user=txn.user
                    )
                    wallet.credit(txn.amount)
                    txn.wallet_credited = True
                    txn.save(update_fields=['wallet_credited', 'updated_at'])

                    return Response(
                        {
                            "message": f"Wallet credited ₹{txn.amount}.",
                            "new_balance": str(wallet.balance),
                            "credited": True,
                        },
                        status=status.HTTP_200_OK,
                    )

                return Response(
                    {"message": f"Transaction marked as {callback_status}.", "credited": False},
                    status=status.HTTP_200_OK,
                )

        except RechargeTransaction.DoesNotExist:
            return Response(
                {"error": "Transaction not found."},
                status=status.HTTP_404_NOT_FOUND,
            )


class RechargeTransactionListView(generics.ListAPIView):
    """
    GET /api/wallet/recharge/transactions/
    Lists all recharge transactions for the authenticated user.
    """
    serializer_class = RechargeTransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RechargeTransaction.objects.filter(user=self.request.user)
