from django.contrib import admin
from .models import Wallet, RechargeTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'updated_at')
    search_fields = ('user__username',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(RechargeTransaction)
class RechargeTransactionAdmin(admin.ModelAdmin):
    list_display = ('reference', 'user', 'amount', 'status', 'wallet_credited', 'created_at')
    list_filter = ('status', 'wallet_credited')
    search_fields = ('reference', 'user__username')
    readonly_fields = ('id', 'created_at', 'updated_at')
