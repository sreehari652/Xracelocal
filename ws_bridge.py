#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════
UWB WebSocket Bridge - Production Ready (FIXED)
═══════════════════════════════════════════════════════════════════════════
"""

import asyncio
import websockets
import socket
import json
import threading
import signal
import sys
from datetime import datetime
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════
UDP_PORT = 4210  # Port where Anchor 0 sends data
WS_PORT = 8001   # Port where browsers connect

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════
connected_clients = set()
event_loop = None
running = True

# Statistics tracking
stats = {
    'udp_packets_total': 0,
    'udp_packets_valid': 0,
    'udp_packets_invalid': 0,
    'ws_messages_sent': 0,
    'ws_clients_total': 0,
    'tags_seen': set(),
    'start_time': datetime.now()
}

# Per-tag statistics
tag_stats = defaultdict(lambda: {
    'count': 0,
    'last_seen': None,
    'total_ranges': 0,
    'valid_ranges': 0
})

# ═══════════════════════════════════════════════════════════════════════════
# UDP RECEIVER
# ═══════════════════════════════════════════════════════════════════════════
def create_udp_socket():
    """Create and configure UDP socket"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT))
    sock.settimeout(0.1)
    return sock

def udp_receiver():
    """Background thread that receives UDP packets from Anchor 0"""
    global running
    
    udp_sock = create_udp_socket()
    print(f"[UDP] ✓ Listening on port {UDP_PORT}")
    
    packet_count = 0
    last_log_time = datetime.now()
    
    while running:
        try:
            # Receive UDP packet
            data, addr = udp_sock.recvfrom(2048)
            stats['udp_packets_total'] += 1
            packet_count += 1
            
            # Decode message
            message = data.decode('utf-8', errors='ignore').strip()
            
            # Try to parse as JSON
            try:
                uwb_data = json.loads(message)
                
                # Validate required fields
                if 'id' not in uwb_data or 'range' not in uwb_data:
                    stats['udp_packets_invalid'] += 1
                    if stats['udp_packets_invalid'] % 10 == 1:
                        print(f"[UDP] ⚠ Invalid format (missing id/range): {message[:80]}")
                    continue
                
                tag_id = uwb_data['id']
                ranges = uwb_data['range']
                
                # Validate range data
                if not isinstance(ranges, list) or len(ranges) < 8:
                    stats['udp_packets_invalid'] += 1
                    continue
                
                # Update statistics
                stats['udp_packets_valid'] += 1
                stats['tags_seen'].add(tag_id)
                
                tag_stats[tag_id]['count'] += 1
                tag_stats[tag_id]['last_seen'] = datetime.now()
                tag_stats[tag_id]['total_ranges'] += len(ranges)
                tag_stats[tag_id]['valid_ranges'] += sum(1 for r in ranges if r > 0)
                
                # Create WebSocket message
                ws_message = {
                    "type": "tag_position",
                    "tag_id": tag_id,
                    "range": ranges,
                    "timestamp": datetime.now().isoformat()
                }
                
                # Add optional fields if present
                for field in ['rssi', 'quality', 'anchors']:
                    if field in uwb_data:
                        ws_message[field] = uwb_data[field]
                
                # Broadcast to WebSocket clients
                if connected_clients and event_loop:
                    asyncio.run_coroutine_threadsafe(
                        broadcast(json.dumps(ws_message)),
                        event_loop
                    )
                
                # Periodic logging (every 10 packets)
                if packet_count % 10 == 0:
                    valid_ranges = sum(1 for r in ranges if r > 0)
                    print(f"[UDP→WS] Tag {tag_id}: {valid_ranges}/8 anchors "
                          f"| Packet #{stats['udp_packets_valid']}")
                
            except json.JSONDecodeError:
                # Not JSON - could be debug message
                if not message.startswith('['):
                    if stats['udp_packets_total'] % 50 == 1:
                        print(f"[UDP] Non-JSON message: {message[:60]}")
                continue
                
            except Exception as e:
                print(f"[UDP] ✗ Parse error: {e}")
                continue
                
        except socket.timeout:
            continue
            
        except Exception as e:
            if running:  # Only print if not shutting down
                print(f"[UDP] ✗ Error: {e}")
    
    udp_sock.close()
    print("[UDP] ✓ Receiver stopped")

# ═══════════════════════════════════════════════════════════════════════════
# WEBSOCKET SERVER
# ═══════════════════════════════════════════════════════════════════════════
async def broadcast(message):
    """Broadcast message to all connected WebSocket clients"""
    if not connected_clients:
        return
    
    stats['ws_messages_sent'] += 1
    
    # Send to all clients (filter out closed connections)
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send(message)
        except Exception as e:
            print(f"[WS] ⚠ Broadcast error: {e}")
            disconnected.add(client)
    
    # Remove disconnected clients
    connected_clients.difference_update(disconnected)

async def handle_client(websocket):
    """Handle individual WebSocket client connection - FIXED SIGNATURE"""
    client_addr = websocket.remote_address
    client_id = f"{client_addr[0]}:{client_addr[1]}"
    
    print(f"\n[WS] ✓ Client connected: {client_id}")
    
    # Register client
    connected_clients.add(websocket)
    stats['ws_clients_total'] += 1
    print(f"[WS] Active clients: {len(connected_clients)}")
    
    try:
        # Send welcome message
        welcome = {
            "type": "connection",
            "status": "connected",
            "message": "Connected to UWB tracking system",
            "timestamp": datetime.now().isoformat(),
            "server_info": {
                "udp_port": UDP_PORT,
                "ws_port": WS_PORT,
                "uptime_seconds": (datetime.now() - stats['start_time']).total_seconds()
            },
            "stats": {
                "packets_received": stats['udp_packets_valid'],
                "tags_seen": sorted(list(stats['tags_seen']))
            }
        }
        await websocket.send(json.dumps(welcome))
        
        # Handle incoming messages
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                
                if msg_type == 'ping':
                    # Respond to ping
                    await websocket.send(json.dumps({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    }))
                
                elif msg_type == 'get_stats':
                    # Send detailed statistics
                    await websocket.send(json.dumps({
                        "type": "stats",
                        "udp_packets_total": stats['udp_packets_total'],
                        "udp_packets_valid": stats['udp_packets_valid'],
                        "udp_packets_invalid": stats['udp_packets_invalid'],
                        "ws_messages_sent": stats['ws_messages_sent'],
                        "ws_clients_connected": len(connected_clients),
                        "ws_clients_total": stats['ws_clients_total'],
                        "tags_seen": sorted(list(stats['tags_seen'])),
                        "uptime_seconds": (datetime.now() - stats['start_time']).total_seconds(),
                        "tag_details": {
                            str(tid): {
                                'packets': info['count'],
                                'last_seen': info['last_seen'].isoformat() if info['last_seen'] else None,
                                'avg_valid_ranges': round(info['valid_ranges'] / max(1, info['count']), 2)
                            }
                            for tid, info in tag_stats.items()
                        },
                        "timestamp": datetime.now().isoformat()
                    }))
                
                else:
                    print(f"[WS] Unknown message type '{msg_type}' from {client_id}")
                    
            except json.JSONDecodeError:
                print(f"[WS] ⚠ Invalid JSON from {client_id}")
            except Exception as e:
                print(f"[WS] ⚠ Message handler error: {e}")
    
    except websockets.exceptions.ConnectionClosed:
        print(f"[WS] ✗ Client closed connection: {client_id}")
    except Exception as e:
        print(f"[WS] ✗ Client error: {e}")
    finally:
        # Cleanup
        connected_clients.discard(websocket)
        print(f"[WS] ✗ Client disconnected: {client_id}")
        print(f"[WS] Active clients: {len(connected_clients)}")

async def stats_reporter():
    """Periodically print statistics report"""
    while running:
        await asyncio.sleep(60)  # Every 60 seconds
        
        if not running:
            break
        
        uptime = (datetime.now() - stats['start_time']).total_seconds()
        
        print(f"\n{'═'*70}")
        print(f"STATISTICS REPORT - Uptime: {uptime:.0f}s ({uptime/60:.1f} min)")
        print(f"{'═'*70}")
        print(f"UDP Packets:     {stats['udp_packets_total']:,} total, "
              f"{stats['udp_packets_valid']:,} valid, "
              f"{stats['udp_packets_invalid']:,} invalid")
        print(f"WS Messages:     {stats['ws_messages_sent']:,} sent")
        print(f"WS Clients:      {len(connected_clients)} active, "
              f"{stats['ws_clients_total']} total connections")
        print(f"Tags Tracked:    {sorted(stats['tags_seen'])}")
        
        if tag_stats:
            print(f"\nPer-Tag Statistics:")
            for tid in sorted(tag_stats.keys()):
                info = tag_stats[tid]
                age = (datetime.now() - info['last_seen']).total_seconds() if info['last_seen'] else 999
                avg_valid = info['valid_ranges'] / max(1, info['count'])
                print(f"  Tag {tid:2d}: {info['count']:5d} packets, "
                      f"{avg_valid:.1f} avg anchors, "
                      f"last seen {age:5.1f}s ago")
        
        print(f"{'═'*70}\n")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN SERVER
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    """Start the WebSocket server and UDP receiver"""
    global event_loop, running
    
    event_loop = asyncio.get_event_loop()
    
    # Print banner
    print(f"\n{'═'*70}")
    print(f"UWB WEBSOCKET BRIDGE SERVER")
    print(f"{'═'*70}")
    print(f"Started:         {stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"UDP Port:        {UDP_PORT} (receiving from Anchor 0)")
    print(f"WebSocket Port:  {WS_PORT} (browsers connect here)")
    print(f"{'═'*70}\n")
    
    # Start UDP receiver thread
    udp_thread = threading.Thread(target=udp_receiver, daemon=True, name="UDP-Receiver")
    udp_thread.start()
    
    # Start statistics reporter
    asyncio.create_task(stats_reporter())
    
    # Start WebSocket server
    try:
        async with websockets.serve(handle_client, "0.0.0.0", WS_PORT):
            print(f"[WS] ✓ Server started on ws://0.0.0.0:{WS_PORT}")
            print(f"[WS] Access locally:  ws://localhost:{WS_PORT}")
            print(f"[WS] Access on LAN:   ws://<your-ip>:{WS_PORT}")
            print(f"\n{'═'*70}")
            print(f"✓ READY! Access at http://127.0.0.1:8000/tag-manager/")
            print(f"{'═'*70}\n")
            
            # Run forever
            await asyncio.Future()
            
    except OSError as e:
        if e.errno == 48 or e.errno == 98:  # Address already in use
            print(f"\n{'═'*70}")
            print(f"✗ ERROR: Port {WS_PORT} is already in use!")
            print(f"{'═'*70}")
            print(f"Solutions:")
            print(f"  1. Stop other instance: ps aux | grep ws_bridge")
            print(f"  2. Change WS_PORT in this script")
            print(f"  3. Wait a moment and try again")
            print(f"{'═'*70}\n")
        else:
            print(f"\n✗ Network error: {e}")
        running = False

# ═══════════════════════════════════════════════════════════════════════════
# SHUTDOWN HANDLER
# ═══════════════════════════════════════════════════════════════════════════
def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    global running
    
    print(f"\n\n{'═'*70}")
    print(f"SHUTTING DOWN...")
    print(f"{'═'*70}")
    
    running = False
    
    # Print final statistics
    uptime = (datetime.now() - stats['start_time']).total_seconds()
    print(f"Runtime:            {uptime:.0f}s ({uptime/60:.1f} minutes)")
    print(f"UDP Packets:        {stats['udp_packets_total']:,} total")
    print(f"  Valid:            {stats['udp_packets_valid']:,}")
    print(f"  Invalid:          {stats['udp_packets_invalid']:,}")
    print(f"WS Messages:        {stats['ws_messages_sent']:,}")
    print(f"WS Clients:         {stats['ws_clients_total']} total connections")
    print(f"Tags Tracked:       {sorted(stats['tags_seen'])}")
    print(f"{'═'*70}\n")
    
    sys.exit(0)

# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Register signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Run the server
        asyncio.run(main())
        
    except KeyboardInterrupt:
        signal_handler(None, None)
        
    except Exception as e:
        print(f"\n{'═'*70}")
        print(f"✗ FATAL ERROR")
        print(f"{'═'*70}")
        print(f"{e}\n")
        import traceback
        traceback.print_exc()
        print(f"{'═'*70}\n")