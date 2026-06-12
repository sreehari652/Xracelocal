import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.generic.websocket import JsonWebsocketConsumer
from channels.auth import get_user




class RaceTrackConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("race_track", self.channel_name)
        await self.accept()
        
        # Send confirmation
        await self.send(json.dumps({
            "type": "connection",
            "message": "Connected to race tracker"
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("race_track", self.channel_name)

    async def receive(self, text_data):
        # Broadcast the received message to the group
        try:
            data = json.loads(text_data)
            await self.channel_layer.group_send(
                "race_track",
                {
                    "type": "broadcast_message",
                    "data": data
                }
            )
        except Exception as e:
            pass

    async def broadcast_message(self, event):
        await self.send(json.dumps(event["data"]))

    # Called when udp_listener pushes data
    async def tag_update(self, event):
        await self.send(json.dumps(event["data"]))