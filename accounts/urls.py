"""URLs for the accounts app."""

from django.urls import path

from accounts import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.ClinicoreLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path(
        'organizations/<int:organization_id>/switch/',
        views.switch_organization,
        name='switch_organization',
    ),
]
