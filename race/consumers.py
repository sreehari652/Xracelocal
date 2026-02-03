import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.generic.websocket import JsonWebsocketConsumer
from channels.auth import get_user




class RaceTrackConsumer(JsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("race_track", self.channel_name)
        print("🔥 HIT CONNECT METHOD")
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("race_track", self.channel_name)

    # called by channel layer when udp_listener pushes data
    async def tag_update(self, event):
        await self.send_json(event["data"])