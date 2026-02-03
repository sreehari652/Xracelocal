import socket
import json
import time
import threading
import asyncio

_started = False  # flag so it only starts once


def start_udp_listener():
    global _started
    if _started:
        return
    _started = True

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    print("[UWB] UDP listener started on port 4210")


def _run():
    from channels.layers import get_channel_layer

    UDP_PORT = 4210
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT))
    sock.settimeout(1.0)

    channel_layer = get_channel_layer()
    loop = asyncio.new_event_loop()

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            message = json.loads(data.decode('utf-8').strip())

            # message from UWB tag:
            # { "id": 0, "range": [100, 200, 150, 180], "rssi": [-60, -70, -65, -72] }

            payload = {
                "type": "tag_position",
                "tag_id": str(message["id"]),
                "range": message.get("range", []),
                "rssi": message.get("rssi", []),
                "timestamp": time.time(),
            }

            # push to ALL connected websockets — browser filters by active tag_ids
            loop.run_until_complete(
                channel_layer.group_send("race_track", {
                    "type": "tag_update",
                    "data": payload,
                })
            )

        except socket.timeout:
            continue
        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(f"[UWB] Error: {e}")