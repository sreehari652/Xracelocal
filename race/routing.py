from django.urls import path
from .consumers import *
from django.urls import re_path

websocket_urlpatterns = [
    re_path(r'ws/race/$', RaceTrackConsumer.as_asgi()),
]