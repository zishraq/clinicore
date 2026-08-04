"""URLs for the billing app."""

from django.urls import path

from billing import views

app_name = 'billing'

urlpatterns = [
    path('', views.invoice_list, name='invoice_list'),
    path('new/', views.invoice_create, name='invoice_create'),
    path('line-row/', views.invoice_line_row, name='line_row'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/edit/', views.invoice_update, name='invoice_update'),
    path('<int:pk>/void/', views.invoice_void, name='invoice_void'),
    path('<int:pk>/payments/', views.payment_create, name='payment_create'),
    path(
        '<int:pk>/payments/<int:payment_pk>/void/',
        views.payment_void,
        name='payment_void',
    ),
    path('<int:pk>/receipt/', views.receipt_print, name='receipt_print'),
]
