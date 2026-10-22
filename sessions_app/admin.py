from django.contrib import admin
from .models import ConsultationSession, BillingRecord


@admin.register(ConsultationSession)
class ConsultationSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'provider', 'state', 'per_minute_cost', 'billed_amount', 'ended_by', 'created_at')
    list_filter = ('state', 'ended_by')
    search_fields = ('user__username', 'provider__username')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(BillingRecord)
class BillingRecordAdmin(admin.ModelAdmin):
    list_display = ('session', 'minute_number', 'amount_charged', 'charged_at')
    list_filter = ('charged_at',)
    readonly_fields = ('charged_at',)
