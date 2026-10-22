from rest_framework import serializers
from accounts.serializers import UserSerializer
from .models import ConsultationSession, BillingRecord


class SessionSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    provider = UserSerializer(read_only=True)

    class Meta:
        model = ConsultationSession
        fields = (
            'id', 'user', 'provider', 'state', 'per_minute_cost',
            'started_at', 'ended_at', 'billed_minutes', 'billed_amount',
            'ended_by', 'end_reason', 'talk_time_seconds', 'created_at', 'updated_at',
        )
        read_only_fields = fields


class CreateSessionSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=False, allow_null=True)


class AssignProviderSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField()


class BillingRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingRecord
        fields = ('id', 'minute_number', 'amount_charged', 'charged_at')
        read_only_fields = fields
