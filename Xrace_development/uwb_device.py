"""
UWB Position Tracker - UWB Device Classes
Defines Tag and Anchor objects
"""

import time
from collections import deque
from kalman_filter import KalmanFilter
from positioning import PositioningAlgorithms
from config import (
    RED, BLACK, TRAIL_LENGTH,
    QUALITY_EXCELLENT_ANCHORS, QUALITY_GOOD_ANCHORS
)


class UWBDevice:
    """Base class for UWB devices (Anchors and Tags)"""

    def __init__(self, name, device_type):
        """
        Initialize UWB device
        
        Args:
            name: Device identifier
            device_type: 0 for Anchor, 1 for Tag
        """
        self.name = name
        self.type = device_type
        self.x = 0
        self.y = 0
        self.raw_x = 0
        self.raw_y = 0
        self.status = False
        self.last_update = 0

        # Set color based on type
        if self.type == 1:  # Tag
            self.color = RED
        else:  # Anchor
            self.color = BLACK

    def set_location(self, x, y):
        """
        Set device location
        
        Args:
            x: X coordinate in cm
            y: Y coordinate in cm
        """
        self.x = x
        self.y = y
        self.raw_x = x
        self.raw_y = y
        self.status = True
        self.last_update = time.time()

    def is_active(self, timeout=5):
        """
        Check if device is currently active
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            bool: True if active
        """
        return self.status and (time.time() - self.last_update) < timeout

    def get_position(self):
        """
        Get current position
        
        Returns:
            tuple: (x, y) coordinates
        """
        return self.x, self.y


class Anchor(UWBDevice):
    """Anchor device - fixed position reference point"""

    def __init__(self, anchor_id, x, y):
        """
        Initialize anchor
        
        Args:
            anchor_id: Anchor ID number
            x: X position in cm
            y: Y position in cm
        """
        super().__init__(f"ANC {anchor_id}", 0)
        self.id = anchor_id
        self.set_location(x, y)


class Tag(UWBDevice):
    """Tag device - tracked mobile object"""

    def __init__(self, tag_id):
        """
        Initialize tag
        
        Args:
            tag_id: Tag ID number
        """
        super().__init__(f"TAG {tag_id}", 1)
        self.id = tag_id
        
        # Range measurements
        self.range_list = []
        self.rssi_list = []
        
        # Position tracking
        self.history = deque(maxlen=TRAIL_LENGTH)
        self.kalman = KalmanFilter()
        
        # Statistics
        self.update_count = 0
        self.quality = "unknown"
        self.anchor_count = 0
        
    def set_location(self, x, y):
        """
        Set tag location with Kalman filtering
        
        Args:
            x: Raw X coordinate
            y: Raw Y coordinate
        """
        self.raw_x = x
        self.raw_y = y

        # Apply Kalman filter for smoothing
        self.x, self.y = self.kalman.update(x, y)
        self.status = True
        self.last_update = time.time()

        # Add to history for trail visualization
        self.history.append((int(self.x), int(self.y), time.time()))

    def update_measurements(self, range_list, rssi_list=None, anchors=None):
        """
        Update range and RSSI measurements
        
        Args:
            range_list: List of distances to each anchor
            rssi_list: List of RSSI values (optional)
            anchors: List of anchor objects for calculation
        """
        self.range_list = range_list
        self.rssi_list = rssi_list if rssi_list else [0] * len(range_list)
        
        if anchors:
            self.calculate_position(anchors)

    def calculate_position(self, anchors):
        """
        Calculate position using trilateration
        
        Args:
            anchors: List of anchor objects
        """
        # Get valid anchor measurements
        valid_anchors = PositioningAlgorithms.get_valid_anchors(
            self.range_list, self.rssi_list, anchors
        )
        
        self.anchor_count = len(valid_anchors)
        
        # Calculate position based on number of valid anchors
        if len(valid_anchors) >= QUALITY_EXCELLENT_ANCHORS:
            # Use all 4+ anchors with weighted least squares
            x, y = PositioningAlgorithms.weighted_multilateration(valid_anchors)
            self.quality = "excellent"
            
        elif len(valid_anchors) >= QUALITY_GOOD_ANCHORS:
            # Use best 3 anchors
            valid_anchors.sort(key=lambda a: a['weight'], reverse=True)
            best_3 = valid_anchors[:3]
            
            x, y = PositioningAlgorithms.trilaterate_3points(
                best_3[0]['anchor'].x, best_3[0]['anchor'].y, best_3[0]['range'],
                best_3[1]['anchor'].x, best_3[1]['anchor'].y, best_3[1]['range'],
                best_3[2]['anchor'].x, best_3[2]['anchor'].y, best_3[2]['range']
            )
            self.quality = "good"
            
        elif len(valid_anchors) >= 2:
            # Fallback to 2 anchors
            x, y = PositioningAlgorithms.two_circles(
                valid_anchors[0]['anchor'].x, valid_anchors[0]['anchor'].y,
                valid_anchors[1]['anchor'].x, valid_anchors[1]['anchor'].y,
                valid_anchors[0]['range'], valid_anchors[1]['range']
            )
            self.quality = "fair"
        else:
            self.quality = "poor"
            return

        self.set_location(x, y)
        self.update_count += 1

    def reset_history(self):
        """Clear position history and reset Kalman filter"""
        self.history.clear()
        self.kalman.reset()
        self.update_count = 0

    def get_speed(self):
        """
        Get current speed estimate
        
        Returns:
            float: Speed in cm/s
        """
        return self.kalman.get_speed()

    def get_trail_points(self):
        """
        Get historical position points for trail drawing
        
        Returns:
            list: List of (x, y, timestamp) tuples
        """
        return list(self.history)
