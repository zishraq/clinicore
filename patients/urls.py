"""URLs for the patients app."""

from django.urls import path

from patients import views

app_name = 'patients'

urlpatterns = [
    path('', views.patient_list, name='list'),
    path('search/', views.patient_search, name='search'),
    # Autocomplete and inline registration for the visit form (A1).
    path('suggestions/', views.patient_suggestions, name='suggestions'),
    path('quick-create/', views.patient_quick_create, name='quick_create'),
    path('new/', views.patient_create, name='create'),
    path('<int:pk>/', views.patient_detail, name='detail'),
    path('<int:pk>/edit/', views.patient_update, name='update'),
    path('<int:pk>/delete/', views.patient_delete, name='delete'),
    # The case record. One page, one Save (ADR 0020 §8). The clinical profile
    # that used to live at /clinical/ was absorbed into it and its URL is gone.
    path('<int:pk>/case/', views.case_record_edit, name='case_record'),
    # An HTMX add-row fragment is a URL, so it carries the same role gate as the
    # page (ADR 0012).
    path('case/row/<str:kind>/', views.case_record_row, name='case_record_row'),
]
