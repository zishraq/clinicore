"""Dashboard and other cross-app pages."""

from django.urls import path

from core import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    # Kept at the root, not under a prefix: SECURE_REDIRECT_EXEMPT in
    # config/settings.py matches '^healthz/?$', and a container healthcheck
    # should not have to know the app's URL layout.
    path('healthz', views.healthz, name='healthz'),
]
