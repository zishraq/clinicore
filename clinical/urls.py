"""URLs for the clinical app."""

from django.urls import path

from clinical import views

app_name = 'clinical'

urlpatterns = [
    path('encounters/', views.encounter_list, name='encounter_list'),
    path('encounters/new/', views.encounter_create, name='encounter_create'),
    path('encounters/item-row/', views.prescription_item_row, name='item_row'),
    path('encounters/<int:pk>/', views.encounter_detail, name='encounter_detail'),
    path('encounters/<int:pk>/edit/', views.encounter_update, name='encounter_update'),
    path(
        'encounters/<int:pk>/history/',
        views.encounter_history,
        name='encounter_history',
    ),
    path(
        'encounters/<int:pk>/finalize/',
        views.encounter_finalize,
        name='encounter_finalize',
    ),
    path(
        'encounters/<int:pk>/prescription/print/',
        views.prescription_print,
        name='prescription_print',
    ),
]
