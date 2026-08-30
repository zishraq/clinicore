"""Dashboard and other cross-app pages."""

from django.urls import path

from core import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    # Under settings/ because that is where an operator looks for it, though the
    # view lives here beside ``core.backups``: what it reports is the state of
    # the server, which is not one organization's configuration. The team screen
    # sits the same way, in ``accounts`` under a Settings link.
    path('settings/backups/', views.backup_settings, name='backup_settings'),
    # Kept at the root, not under a prefix: SECURE_REDIRECT_EXEMPT in
    # config/settings.py matches '^healthz/?$', and a container healthcheck
    # should not have to know the app's URL layout.
    path('healthz', views.healthz, name='healthz'),
]
