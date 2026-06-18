# import json
# from channels.generic.websocket import AsyncWebsocketConsumer
# from channels.generic.websocket import JsonWebsocketConsumer
# from channels.auth import get_user




# class RaceTrackConsumer(AsyncWebsocketConsumer):
#     async def connect(self):
#         await self.channel_layer.group_add("race_track", self.channel_name)
#         await self.accept()
        
#         # Send confirmation
#         await self.send(json.dumps({
#             "type": "connection",
#             "message": "Connected to race tracker"
#         }))

#     async def disconnect(self, close_code):
#         await self.channel_layer.group_discard("race_track", self.channel_name)

#     async def receive(self, text_data):
#         # Broadcast the received message to the group
#         try:
#             data = json.loads(text_data)
#             await self.channel_layer.group_send(
#                 "race_track",
#                 {
#                     "type": "broadcast_message",
#                     "data": data
#                 }
#             )
#         except Exception as e:
#             pass

#     async def broadcast_message(self, event):
#         await self.send(json.dumps(event["data"]))

#     # Called when udp_listener pushes data
#     async def tag_update(self, event):
#         await self.send(json.dumps(event["data"]))


import json
from channels.generic.websocket import AsyncWebsocketConsumer

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
        try:
            data = json.loads(text_data)
            msg_type = data.get("type")

            # ── 1. Intercept Screen Broadcasts from Admin ──
            if msg_type == "broadcast_message":
                await self.channel_layer.group_send(
                    "race_track",
                    {
                        "type": "push_screen_update", # Maps to def push_screen_update below
                        "payload": data.get("data", {}) # Extract the ScreenData
                    }
                )
            # ── 2. Handle other general commands (admin_start, reset) ──
            else:
                await self.channel_layer.group_send(
                    "race_track",
                    {
                        "type": "generic_echo",
                        "payload": data
                    }
                )
        except Exception as e:
            print(f"[WS Receive Error]: {e}")

    # # Handler for pushing the ScreenData to the public display
    # async def push_screen_update(self, event):
    #     await self.send(json.dumps({
    #         "type": "screen_update", 
    #         "data": event["payload"]
    #     }))

    # # Handler for generic echoes
    # async def generic_echo(self, event):
    #     await self.send(json.dumps(event["payload"]))

    # # Called when udp_listener pushes data
    # async def tag_update(self, event):
    #     await self.send(json.dumps(event["data"]))


        # ── Channel layer handlers (called by group_send) ──────────────────

    # ← THIS WAS MISSING — broadcast_screen view calls group_send with type "broadcast_message"
    # Django Channels maps "broadcast_message" → method "broadcast_message"
    async def broadcast_message(self, event):
        await self.send(json.dumps({
            "type": "screen_update",
            "data": event["data"]
        }))

    async def push_screen_update(self, event):
        await self.send(json.dumps({
            "type": "screen_update",
            "data": event["payload"]
        }))

    async def generic_echo(self, event):
        await self.send(json.dumps(event["payload"]))

    async def tag_update(self, event):
        await self.send(json.dumps(event["data"]))



# class ScreenConsumer(AsyncWebsocketConsumer):

#     async def connect(self):
#         await self.channel_layer.group_add(
#             "screen_updates",
#             self.channel_name
#         )
#         await self.accept()

#     async def disconnect(self, close_code):
#         await self.channel_layer.group_discard(
#             "screen_updates",
#             self.channel_name
#         )

#     async def screen_update(self, event):
#         await self.send(json.dumps({
#             "type": "screen_update",
#             "data": event["payload"]
#         }))



import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.cache import cache


class ScreenConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        await self.channel_layer.group_add(
            "screen_updates",
            self.channel_name
        )
        await self.accept()

        # ── Fetch the current active screen from the cache ────────────────
        # This ensures that when a user refreshes the page, they immediately
        # receive the screen they were viewing before the reload.
        current_screen = await database_sync_to_async(cache.get)('current_live_screen')

        if current_screen:
            # ✅ Send the screen they were viewing before the refresh
            await self.send(json.dumps({
                "type": "screen_update",
                "data": current_screen
            }))
        else:
            # ✅ Fallback: If no screen is cached (e.g., fresh server start),
            # default to the landing page as requested.
            await self.send(json.dumps({
                "type": "screen_update",
                "data": {
                    "displayScreen": "landing",
                    "landingData": {}
                }
            }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            "screen_updates",
            self.channel_name
        )

    async def screen_update(self, event):
        await self.send(json.dumps({
            "type": "screen_update",
            "data": event["payload"]
        }))