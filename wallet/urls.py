from django.urls import path
from .views import (
    WalletDetailView,
    RechargeInitiateView,
    RechargeCallbackView,
    RechargeTransactionListView,
)

urlpatterns = [
    path('', WalletDetailView.as_view(), name='wallet-detail'),
    path('recharge/initiate/', RechargeInitiateView.as_view(), name='recharge-initiate'),
    path('recharge/callback/', RechargeCallbackView.as_view(), name='recharge-callback'),
    path('recharge/transactions/', RechargeTransactionListView.as_view(), name='recharge-transactions'),
]
