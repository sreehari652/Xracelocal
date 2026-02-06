"""
UWB Position Tracker - UDP Network Module
Handles UDP communication and data reception
"""

import socket
import json
import time
import threading
from config import (
    UDP_PORT, UDP_TIMEOUT, UDP_BUFFER_SIZE,
    PRINT_PACKET_LOGS
)


class UDPReceiver:
    """UDP receiver with statistics and threading"""

    def __init__(self, port=None, tags=None):
        """
        Initialize UDP receiver
        
        Args:
            port: UDP port to listen on (default from config)
            tags: List of tag objects to update
        """
        self.port = port if port is not None else UDP_PORT
        self.tags = tags if tags else []
        
        # Socket setup
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', self.port))
        self.sock.settimeout(UDP_TIMEOUT)
        
        # Statistics
        self.running = True
        self.packets_received = 0
        self.packets_per_second = 0
        self.last_packet_time = 0
        self.last_second = time.time()
        self.second_counter = 0
        self.error_count = 0
        
        # Start receiver thread
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()
        
        print(f"UDP receiver started on port {self.port}")

    def set_tags(self, tags):
        """
        Set the tag list for updating
        
        Args:
            tags: List of tag objects
        """
        self.tags = tags

    def _receive_loop(self):
        """Main receiver loop (runs in separate thread)"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(UDP_BUFFER_SIZE)
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
            except Exception as e:
                self.error_count += 1
                if PRINT_PACKET_LOGS:
                    print(f"UDP Error: {e}")

    def _process_data(self, message, addr):
        """
        Process received UDP message
        
        Args:
            message: JSON string message
            addr: Source address
        """
        try:
            data = json.loads(message)
            
            # Extract data
            tag_id = data.get('id', -1)
            ranges = data.get('range', [])
            rssi = data.get('rssi', [0] * 8)
            quality = data.get('quality', 'unknown')
            anchor_count = data.get('anchors', 0)

            # Validate tag ID
            if 0 <= tag_id < len(self.tags):
                tag = self.tags[tag_id]
                tag.range_list = ranges
                tag.rssi_list = rssi
                tag.quality = quality
                tag.anchor_count = anchor_count
                
                if PRINT_PACKET_LOGS:
                    print(f"Tag {tag_id}: {anchor_count} anchors, quality: {quality}")
                    
            else:
                if PRINT_PACKET_LOGS:
                    print(f"Warning: Tag ID {tag_id} out of range")

        except json.JSONDecodeError:
            # Non-JSON messages might be log messages
            if not message.startswith("["):
                if PRINT_PACKET_LOGS:
                    print(f"[LOG] {message}")
                    
        except Exception as e:
            self.error_count += 1
            if PRINT_PACKET_LOGS:
                print(f"Process error: {e}")

    def is_connected(self, timeout=2):
        """
        Check if we're receiving data
        
        Args:
            timeout: Seconds since last packet
            
        Returns:
            bool: True if connected
        """
        return (time.time() - self.last_packet_time) < timeout

    def get_statistics(self):
        """
        Get receiver statistics
        
        Returns:
            dict: Statistics dictionary
        """
        return {
            'packets_received': self.packets_received,
            'packets_per_second': self.packets_per_second,
            'error_count': self.error_count,
            'uptime': time.time() - self.last_second,
            'connected': self.is_connected()
        }

    def stop(self):
        """Stop the receiver and close socket"""
        print("Stopping UDP receiver...")
        self.running = False
        
        # Wait for thread to finish
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        
        self.sock.close()
        print("UDP receiver stopped")

    def reset_statistics(self):
        """Reset all statistics counters"""
        self.packets_received = 0
        self.packets_per_second = 0
        self.second_counter = 0
        self.error_count = 0
        self.last_second = time.time()
