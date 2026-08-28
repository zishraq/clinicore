"""URLs for organization settings."""

from django.urls import path

from organizations import views

app_name = 'organizations'

urlpatterns = [
    path('billing/', views.billing_settings, name='billing_settings'),
    path('features/', views.feature_settings, name='feature_settings'),
    path('prescription/', views.prescription_settings, name='prescription_settings'),
    # Chambers. Editable by the clinic rather than in the Django admin: the
    # schedule note is the most volatile field on the printed sheet, and the
    # admin is for the developer, not the customer (SPEC §6.1).
    path('branches/', views.branch_list, name='branch_list'),
    path('branches/new/', views.branch_create, name='branch_create'),
    path('branches/<int:pk>/edit/', views.branch_update, name='branch_update'),
]
