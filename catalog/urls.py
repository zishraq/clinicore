"""URLs for the catalog app."""

from django.urls import path

from catalog import views

app_name = 'catalog'

urlpatterns = [
    # Autocomplete endpoints used by the encounter form.
    path('suggestions/', views.suggestions, name='suggestions'),
    path('quick-add/', views.quick_add, name='quick_add'),
    # Settings CRUD.
    path('medicines/', views.product_list, name='product_list'),
    path('medicines/new/', views.product_create, name='product_create'),
    path('medicines/<int:pk>/edit/', views.product_update, name='product_update'),
    path(
        'medicines/<int:pk>/toggle/',
        views.product_toggle_active,
        name='product_toggle_active',
    ),
    path('advice/', views.advice_list, name='advice_list'),
    path('advice/new/', views.advice_create, name='advice_create'),
    path('advice/<int:pk>/edit/', views.advice_update, name='advice_update'),
    path(
        'advice/<int:pk>/toggle/',
        views.advice_toggle_active,
        name='advice_toggle_active',
    ),
]
