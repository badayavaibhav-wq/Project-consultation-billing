from django.urls import path
from .views import (
    SessionListCreateView,
    SessionDetailView,
    AssignProviderView,
    ProviderAcceptView,
    StartSessionView,
    EndSessionView,
    BillSessionView,
    SessionBillingHistoryView,
    CancelSessionView,
)

urlpatterns = [
    path('', SessionListCreateView.as_view(), name='session-list-create'),
    path('<uuid:pk>/', SessionDetailView.as_view(), name='session-detail'),
    path('<uuid:pk>/assign/', AssignProviderView.as_view(), name='session-assign'),
    path('<uuid:pk>/accept/', ProviderAcceptView.as_view(), name='session-accept'),
    path('<uuid:pk>/start/', StartSessionView.as_view(), name='session-start'),
    path('<uuid:pk>/end/', EndSessionView.as_view(), name='session-end'),
    path('<uuid:pk>/bill/', BillSessionView.as_view(), name='session-bill'),
    path('<uuid:pk>/billing/', SessionBillingHistoryView.as_view(), name='session-billing-history'),
    path('<uuid:pk>/cancel/', CancelSessionView.as_view(), name='session-cancel'),
]
