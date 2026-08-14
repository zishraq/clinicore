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
    # Your own account. Reachable by anyone signed in, whatever their role.
    path('profile/', views.profile, name='profile'),
    path('profile/password/', views.password_change, name='password_change'),
    # The team screen. Administrator-only, checked in the views.
    path('team/', views.member_list, name='member_list'),
    path('team/add/', views.member_create, name='member_create'),
    path('team/<int:pk>/edit/', views.member_update, name='member_update'),
    path(
        'team/<int:pk>/password/',
        views.member_reset_password,
        name='member_reset_password',
    ),
    path(
        'team/<int:pk>/access/',
        views.member_toggle_active,
        name='member_toggle_active',
    ),
]
