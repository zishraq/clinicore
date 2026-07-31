"""URLs for the patients app."""

from django.urls import path

from patients import views

app_name = 'patients'

urlpatterns = [
    path('', views.patient_list, name='list'),
    path('search/', views.patient_search, name='search'),
    path('new/', views.patient_create, name='create'),
    path('<int:pk>/', views.patient_detail, name='detail'),
    path('<int:pk>/edit/', views.patient_update, name='update'),
    path('<int:pk>/delete/', views.patient_delete, name='delete'),
    path('<int:pk>/clinical/', views.clinical_profile_edit, name='clinical_profile'),
]
