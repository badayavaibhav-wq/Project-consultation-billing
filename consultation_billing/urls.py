from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/wallet/', include('wallet.urls')),
    path('api/sessions/', include('sessions_app.urls')),
]
