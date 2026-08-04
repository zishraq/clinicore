"""URLs for organization settings."""

from django.urls import path

from organizations import views

app_name = 'organizations'

urlpatterns = [
    path('billing/', views.billing_settings, name='billing_settings'),
]
