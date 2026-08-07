"""URLs for the scheduling app."""

from django.urls import path

from scheduling import views

app_name = 'scheduling'

urlpatterns = [
    path('', views.day_view, name='day'),
    # The polled fragment. Separate from the page so a five-second refresh
    # costs one queryset rather than a whole render.
    path('rows/', views.day_rows, name='day_rows'),
    path('new/', views.appointment_create, name='create'),
    path('<int:pk>/arrived/', views.mark_arrived, name='mark_arrived'),
    path('<int:pk>/no-show/', views.appointment_no_show, name='no_show'),
    path('<int:pk>/cancel/', views.appointment_cancel, name='cancel'),
]
