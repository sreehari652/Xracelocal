from django.shortcuts import render
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def tag_manager_page(request):
    return render(request, 'race/tag_manager.html')


# @csrf_exempt
# @require_http_methods(["POST"])
# def broadcast_screen(request):
#     """
#     POST /broadcast-screen/

#     Receives a ScreenData JSON payload from xrace_backend and
#     broadcasts it to every frontend connected to the race_track
#     WebSocket group (ws://localhost:<port>/ws/race/).

#     Expected body (JSON):
#     {
#         "displayScreen": "leaderboard" | "group" | ...,
#         "groupData":         { ... },
#         "leaderboardData":   { ... },
#         "playersData":       { ... },
#         "playersDetailsData": [ ... ],
#         "qualifyData":       { ... }
#     }
#     """
#     try:
#         data = json.loads(request.body)
#     except json.JSONDecodeError:
#         return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

#     channel_layer = get_channel_layer()
#     async_to_sync(channel_layer.group_send)(
#         "race_track",
#         {
#             "type": "broadcast_message",  # maps to RaceTrackConsumer.broadcast_message
#             "data": data,
#         }
#     )

#     return JsonResponse({"success": True, "message": "Broadcast sent to race_track group"})


@csrf_exempt
def broadcast_screen(request):
    """
    POST /broadcast-screen/

    Receives a ScreenData JSON payload from xrace_backend and
    broadcasts it to every frontend connected to the race_track
    WebSocket group (ws://localhost:<port>/ws/race/).

    Expected body (JSON):
    {
        "displayScreen": "leaderboard" | "group" | ...,
        "groupData":         { ... },
        "leaderboardData":   { ... },
        "playersData":       { ... },
        "playersDetailsData": [ ... ],
        "qualifyData":       { ... }
    }
    """
    # ── Handle CORS Preflight ─────────────────────────────────────────
    if request.method == "OPTIONS":
        response = JsonResponse({})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken"
        return response

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Method not allowed"}, status=405)

    # ── Parse Body ───────────────────────────────────────────────────
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    if not data:
        return JsonResponse({"success": False, "error": "Empty payload"}, status=400)

    # ── Broadcast via Channel Layer ──────────────────────────────────
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "race_track",
            {
                "type": "broadcast_message",
                "data": data,
            }
        )
    except Exception as e:
        print(f"[broadcast_screen] Channel layer error: {e}")
        response = JsonResponse({"success": False, "error": f"Channel layer error: {str(e)}"}, status=500)
        response["Access-Control-Allow-Origin"] = "*"
        return response

    # ── Success ──────────────────────────────────────────────────────
    response = JsonResponse({
        "success": True,
        "message": "Broadcast sent to race_track group",
        "screen": data.get("displayScreen", "unknown"),
    })
    response["Access-Control-Allow-Origin"] = "*"
    return response