"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('', include('accounts.urls')),
    path('patients/', include('patients.urls')),
    path('clinical/', include('clinical.urls')),
    path('catalog/', include('catalog.urls')),
    path('billing/', include('billing.urls')),
    path('settings/', include('organizations.urls')),
]
