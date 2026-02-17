"""
UWB Racing Tracker - Network Module (Stable Positioning)
Shows tags at fixed positions without jitter
"""

import socket
import json
import time
import threading

class UDPReceiver:
    """UDP receiver with stable fixed positioning"""

    def __init__(self, port=4210, tags=None):
        """Initialize UDP receiver"""
        self.port = port
        self.tags = tags if tags else []
        
        # Socket setup
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', self.port))
        self.sock.settimeout(0.1)
        
        # Statistics
        self.running = True
        self.packets_received = 0
        self.packets_per_second = 0
        self.last_packet_time = 0
        self.last_second = time.time()
        self.second_counter = 0
        self.error_count = 0
        
        # Track which tags we've seen
        self.tags_initialized = set()
        
        # Start receiver thread
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()
        
        print(f"UDP receiver started on port {self.port}")
        print("⚠️  STABLE MODE: Tags at fixed positions (no jitter)")

    def set_tags(self, tags):
        """Set the tag list for updating"""
        self.tags = tags

    def _receive_loop(self):
        """Main receiver loop"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                self.packets_received += 1
                self.second_counter += 1
                self.last_packet_time = time.time()

                # Update packets per second
                if time.time() - self.last_second >= 1.0:
                    self.packets_per_second = self.second_counter
                    self.second_counter = 0
                    self.last_second = time.time()

                message = data.decode('utf-8').strip()
                self._process_data(message, addr)

            except socket.timeout:
                continue
            except:
                pass

    def _process_data(self, message, addr):
        """Process received UDP message"""
        try:
            data = json.loads(message)
            
            # Extract data
            tag_id = data.get('id', -1)
            ranges = data.get('range', [])
            rssi = data.get('rssi', [0] * 8)
            timestamp = data.get('timestamp', 0)

            # Validate tag ID
            if 0 <= tag_id < len(self.tags):
                tag = self.tags[tag_id]
                tag.range_list = ranges
                tag.rssi_list = rssi
                tag.quality = "good"
                tag.anchor_count = 4
                
                # STABLE FIXED POSITIONS (no Kalman, no jitter)
                # Position tags in a line for easy testing
                fixed_positions = {
                    0: (50, 100),   # TAG 0: Left side, center height
                    1: (100, 100),  # TAG 1: Center
                    2: (150, 100),  # TAG 2: Right side, center height
                }
                
                if tag_id in fixed_positions:
                    x, y = fixed_positions[tag_id]
                    
                    # Set position DIRECTLY without Kalman filter
                    tag.x = x
                    tag.y = y
                    tag.raw_x = x
                    tag.raw_y = y
                    tag.status = True
                    tag.last_update = time.time()
                    
                    # Only print first time we see each tag
                    if tag_id not in self.tags_initialized:
                        self.tags_initialized.add(tag_id)
                        print(f"✓ Tag {tag_id} initialized at ({x}, {y})")
                
                # Print occasionally for active tags
                if self.packets_received % 200 == 0:
                    print(f"✓ Tag {tag_id} stable at ({tag.x}, {tag.y}) - Timestamp: {timestamp}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            self.error_count += 1

    def is_connected(self, timeout=2):
        """Check if we're receiving data"""
        return (time.time() - self.last_packet_time) < timeout

    def get_statistics(self):
        """Get receiver statistics"""
        return {
            'packets_received': self.packets_received,
            'packets_per_second': self.packets_per_second,
            'error_count': self.error_count,
            'uptime': 0,
            'connected': self.is_connected()
        }

    def stop(self):
        """Stop the receiver"""
        print("Stopping UDP receiver...")
        self.running = False
        
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        
        self.sock.close()
        print(f"UDP receiver stopped - Total packets: {self.packets_received}")

    def reset_statistics(self):
        """Reset statistics"""
        self.packets_received = 0
        self.packets_per_second = 0
        self.second_counter = 0
        self.error_count = 0
        self.last_second = time.time()