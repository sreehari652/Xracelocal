"""
UWB Racing Tracker - Speed Calculation Module
Handles speed tracking and calculation for racing cars
"""

import time
import math
from collections import deque
from race_config import *


class SpeedTracker:
    """Tracks speed for a single car"""
    
    def __init__(self, car_id, car_name):
        """
        Initialize speed tracker
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        self.car_id = car_id
        self.car_name = car_name
        
        # Position history for speed calculation
        self.position_history = deque(maxlen=SPEED_AVERAGE_SAMPLES)
        
        # Speed data
        self.instantaneous_speed = 0  # cm/s
        self.average_speed = 0  # cm/s
        self.max_speed = 0  # cm/s
        
        # Lap-based speed tracking
        self.lap_speeds = []  # Average speed for each completed lap
        self.current_lap_speeds = []  # Speed samples for current lap
        
        # Update timing
        self.last_update_time = 0
    
    def update(self, x, y, current_time):
        """
        Update speed calculation with new position
        
        Args:
            x: Current X position in cm
            y: Current Y position in cm
            current_time: Current timestamp
        """
        # Add to position history
        self.position_history.append({
            'x': x,
            'y': y,
            'time': current_time
        })
        
        # Need at least 2 points to calculate speed
        if len(self.position_history) < 2:
            return
        
        # Calculate instantaneous speed (last 2 points)
        self._calculate_instantaneous_speed()
        
        # Calculate average speed (all points in buffer)
        self._calculate_average_speed()
        
        # Track max speed
        if self.instantaneous_speed > self.max_speed:
            self.max_speed = self.instantaneous_speed
        
        # Add to current lap speeds
        self.current_lap_speeds.append(self.instantaneous_speed)
        
        self.last_update_time = current_time
        
        if PRINT_SPEED_UPDATES:
            print(f"{self.car_name} - Instant: {self.get_speed_display('instantaneous')}, "
                  f"Avg: {self.get_speed_display('average')}")
    
    def _calculate_instantaneous_speed(self):
        """Calculate instantaneous speed from last 2 positions"""
        if len(self.position_history) < 2:
            return
        
        p1 = self.position_history[-2]
        p2 = self.position_history[-1]
        
        # Calculate distance
        dx = p2['x'] - p1['x']
        dy = p2['y'] - p1['y']
        distance = math.sqrt(dx * dx + dy * dy)
        
        # Calculate time difference
        dt = p2['time'] - p1['time']
        
        # Avoid division by zero
        if dt > 0:
            self.instantaneous_speed = distance / dt  # cm/s
        else:
            self.instantaneous_speed = 0
    
    def _calculate_average_speed(self):
        """Calculate average speed from position history"""
        if len(self.position_history) < 2:
            return
        
        # Calculate total distance traveled
        total_distance = 0
        for i in range(1, len(self.position_history)):
            p1 = self.position_history[i-1]
            p2 = self.position_history[i]
            
            dx = p2['x'] - p1['x']
            dy = p2['y'] - p1['y']
            total_distance += math.sqrt(dx * dx + dy * dy)
        
        # Calculate total time
        total_time = (self.position_history[-1]['time'] - 
                     self.position_history[0]['time'])
        
        # Calculate average speed
        if total_time > 0:
            self.average_speed = total_distance / total_time  # cm/s
        else:
            self.average_speed = 0
    
    def on_lap_complete(self):
        """Called when a lap is completed"""
        if self.current_lap_speeds:
            # Calculate average speed for this lap
            lap_avg_speed = sum(self.current_lap_speeds) / len(self.current_lap_speeds)
            self.lap_speeds.append(lap_avg_speed)
            
            if PRINT_LAP_EVENTS:
                print(f"📊 {self.car_name} Lap {len(self.lap_speeds)} "
                      f"Avg Speed: {self._convert_speed(lap_avg_speed):.2f} {SPEED_DISPLAY_UNIT}")
            
            # Clear for next lap
            self.current_lap_speeds = []
    
    def _convert_speed(self, speed_cm_s):
        """
        Convert speed from cm/s to display unit
        
        Args:
            speed_cm_s: Speed in cm/s
            
        Returns:
            float: Speed in display unit
        """
        if SPEED_DISPLAY_UNIT == 'cm/s':
            return speed_cm_s
        elif SPEED_DISPLAY_UNIT == 'm/s':
            return speed_cm_s / 100
        elif SPEED_DISPLAY_UNIT == 'km/h':
            return (speed_cm_s / 100) * 3.6  # m/s to km/h
        else:
            return speed_cm_s
    
    def get_speed_display(self, speed_type='instantaneous'):
        """
        Get formatted speed for display
        
        Args:
            speed_type: 'instantaneous', 'average', or 'max'
            
        Returns:
            str: Formatted speed string
        """
        if speed_type == 'instantaneous':
            speed = self.instantaneous_speed
        elif speed_type == 'average':
            speed = self.average_speed
        elif speed_type == 'max':
            speed = self.max_speed
        else:
            speed = self.instantaneous_speed
        
        converted = self._convert_speed(speed)
        return f"{converted:.1f} {SPEED_DISPLAY_UNIT}"
    
    def get_current_speed(self):
        """Get current instantaneous speed in cm/s"""
        return self.instantaneous_speed
    
    def get_average_speed(self):
        """Get average speed in cm/s"""
        return self.average_speed
    
    def get_lap_average_speed(self, lap_number):
        """
        Get average speed for a specific lap
        
        Args:
            lap_number: Lap number (1-indexed)
            
        Returns:
            float: Average speed for that lap in display units, or 0 if not available
        """
        if 0 < lap_number <= len(self.lap_speeds):
            speed_cm_s = self.lap_speeds[lap_number - 1]
            return self._convert_speed(speed_cm_s)
        return 0
    
    def get_speed_info(self):
        """
        Get comprehensive speed information
        
        Returns:
            dict: Speed information
        """
        return {
            'car_id': self.car_id,
            'car_name': self.car_name,
            'instantaneous': self._convert_speed(self.instantaneous_speed),
            'average': self._convert_speed(self.average_speed),
            'max': self._convert_speed(self.max_speed),
            'unit': SPEED_DISPLAY_UNIT,
            'lap_speeds': [self._convert_speed(s) for s in self.lap_speeds],
            'current_lap_avg': (self._convert_speed(sum(self.current_lap_speeds) / 
                               len(self.current_lap_speeds)) 
                               if self.current_lap_speeds else 0)
        }
    
    def reset(self):
        """Reset speed tracker"""
        self.position_history.clear()
        self.instantaneous_speed = 0
        self.average_speed = 0
        self.max_speed = 0
        self.lap_speeds = []
        self.current_lap_speeds = []
        self.last_update_time = 0


class SpeedManager:
    """Manages speed tracking for all cars"""
    
    def __init__(self):
        """Initialize speed manager"""
        self.speed_trackers = {}  # car_id -> SpeedTracker
    
    def register_car(self, car_id, car_name):
        """
        Register a car for speed tracking
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        if car_id not in self.speed_trackers:
            self.speed_trackers[car_id] = SpeedTracker(car_id, car_name)
            print(f"📊 Speed tracking enabled for {car_name}")
    
    def update_car_position(self, car_id, x, y, current_time):
        """
        Update car position for speed calculation
        
        Args:
            car_id: Car identifier
            x: Current X position
            y: Current Y position
            current_time: Current timestamp
        """
        if car_id in self.speed_trackers:
            self.speed_trackers[car_id].update(x, y, current_time)
    
    def on_lap_complete(self, car_id):
        """
        Notify that a car completed a lap
        
        Args:
            car_id: Car identifier
        """
        if car_id in self.speed_trackers:
            self.speed_trackers[car_id].on_lap_complete()
    
    def get_car_speed_info(self, car_id):
        """Get speed info for specific car"""
        if car_id in self.speed_trackers:
            return self.speed_trackers[car_id].get_speed_info()
        return None
    
    def get_current_speed(self, car_id):
        """Get current instantaneous speed for a car"""
        if car_id in self.speed_trackers:
            return self.speed_trackers[car_id].get_current_speed()
        return 0
    
    def get_all_speeds(self):
        """Get speed info for all cars"""
        return {car_id: tracker.get_speed_info() 
                for car_id, tracker in self.speed_trackers.items()}
    
    def reset_all(self):
        """Reset all speed trackers"""
        for tracker in self.speed_trackers.values():
            tracker.reset()
        print("🔄 All speed trackers reset")
