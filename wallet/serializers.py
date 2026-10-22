from rest_framework import serializers
from .models import Wallet, RechargeTransaction


class WalletSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Wallet
        fields = ('id', 'username', 'balance', 'updated_at')
        read_only_fields = ('id', 'username', 'balance', 'updated_at')


class RechargeInitiateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=1)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive.")
        return value


class RechargeCallbackSerializer(serializers.Serializer):
    reference = serializers.CharField()
    status = serializers.ChoiceField(choices=['success', 'failed'])


class RechargeTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RechargeTransaction
        fields = ('id', 'amount', 'reference', 'status', 'wallet_credited', 'created_at', 'updated_at')
        read_only_fields = fields
