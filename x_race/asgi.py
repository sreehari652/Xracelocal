# x_race/asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import race.routing   # 👈 IMPORTANT

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x_race.settings")

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            race.routing.websocket_urlpatterns
        )
    ),
})
