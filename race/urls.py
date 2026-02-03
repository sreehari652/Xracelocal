from django.urls import path
from . import views







urlpatterns = [
    path('',views.tag_manager_page,name='tag-manager-page'),

]