"""URLs for the inventory app."""

from django.urls import path

from inventory import views

app_name = 'inventory'

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('products/<int:pk>/', views.product_stock, name='product_stock'),
    path('batches/options/', views.batch_options, name='batch_options'),
    path('batches/<int:pk>/adjust/', views.adjustment_create, name='adjustment_create'),
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('receipts/new/', views.receipt_create, name='receipt_create'),
    path('receipts/row/', views.receipt_row, name='receipt_row'),
    path('receipts/<int:pk>/', views.receipt_detail, name='receipt_detail'),
]
