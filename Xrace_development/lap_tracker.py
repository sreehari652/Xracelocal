"""
UWB Racing Tracker - Lap Tracking Module
Handles start/finish line crossing detection and lap counting
"""

import time
import math
from race_config import *


class LapTracker:
    """Tracks lap progress for a single car"""
    
    def __init__(self, car_id, car_name):
        """
        Initialize lap tracker
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        self.car_id = car_id
        self.car_name = car_name
        
        # Lap data
        self.current_lap = 0  # 0 = not started, 1+ = racing
        self.total_laps = 0
        self.is_racing = False
        self.race_started = False
        
        # Position tracking for line crossing
        self.last_x = None
        self.last_y = None
        self.crossed_start = False
        
        # Lap timing
        self.lap_start_time = None
        self.current_lap_time = 0
        self.last_lap_time = 0
        self.best_lap_time = float('inf')
        self.lap_times = []  # List of all lap times
        
        # Track which side of line the car is on
        self.on_start_side = None
        
    def update_position(self, x, y, current_time):
        """
        Update car position and check for line crossing
        
        Args:
            x: Current X position
            y: Current Y position
            current_time: Current timestamp
            
        Returns:
            bool: True if lap was completed
        """
        if self.last_x is None:
            self.last_x = x
            self.last_y = y
            self._update_side(x, y)
            return False
        
        # Check for line crossing
        lap_completed = self._check_line_crossing(x, y, current_time)
        
        # Update position
        self.last_x = x
        self.last_y = y
        
        # Update current lap time if racing
        if self.is_racing and self.lap_start_time:
            self.current_lap_time = current_time - self.lap_start_time
        
        return lap_completed
    
    def _update_side(self, x, y):
        """Determine which side of the start line the car is on"""
        if START_LINE_ORIENTATION == 'vertical':
            self.on_start_side = x < START_LINE_X
        else:  # horizontal
            self.on_start_side = y < START_LINE_Y
    
    def _check_line_crossing(self, x, y, current_time):
        """
        Check if car crossed the start/finish line
        
        Args:
            x: Current X position
            y: Current Y position  
            current_time: Current timestamp
            
        Returns:
            bool: True if line was crossed (lap completed)
        """
        # Determine current side
        if START_LINE_ORIENTATION == 'vertical':
            current_side = x < START_LINE_X
            distance_to_line = abs(x - START_LINE_X)
            # Check if Y is within line bounds
            within_bounds = START_LINE_Y1 <= y <= START_LINE_Y2
        else:  # horizontal
            current_side = y < START_LINE_Y
            distance_to_line = abs(y - START_LINE_Y)
            within_bounds = START_LINE_X1 <= x <= START_LINE_X2
        
        # Check if close enough to line and within bounds
        if distance_to_line > LINE_CROSSING_THRESHOLD or not within_bounds:
            self._update_side(x, y)
            return False
        
        # Check if crossed from one side to other
        if self.on_start_side is not None and self.on_start_side != current_side:
            # Line crossed!
            self._update_side(x, y)
            return self._handle_line_crossing(current_time, current_side)
        
        self._update_side(x, y)
        return False
    
    def _handle_line_crossing(self, current_time, crossed_to_side):
        """
        Handle start/finish line crossing
        
        Args:
            current_time: Current timestamp
            crossed_to_side: Which side the car crossed to
            
        Returns:
            bool: True if lap was completed
        """
        # First crossing - start racing
        if not self.race_started:
            self.race_started = True
            self.is_racing = True
            self.current_lap = 1
            self.lap_start_time = current_time
            
            if PRINT_LAP_EVENTS:
                print(f"🏁 {self.car_name} started racing! Lap 1/{TOTAL_LAPS}")
            
            return False
        
        # Subsequent crossings - lap completion
        if self.is_racing:
            # Check minimum lap time to prevent false triggers
            if self.lap_start_time:
                lap_time = current_time - self.lap_start_time
                
                if lap_time < MIN_LAP_TIME:
                    if PRINT_LAP_EVENTS:
                        print(f"⚠️  {self.car_name} lap time too short ({lap_time:.2f}s), ignoring")
                    return False
                
                # Valid lap completion
                self.last_lap_time = lap_time
                self.lap_times.append(lap_time)
                
                if lap_time < self.best_lap_time:
                    self.best_lap_time = lap_time
                
                self.total_laps += 1
                
                if PRINT_LAP_EVENTS:
                    print(f"✓ {self.car_name} completed lap {self.current_lap} - "
                          f"Time: {lap_time:.2f}s (Best: {self.best_lap_time:.2f}s)")
                
                # Start next lap if not finished
                if self.current_lap < TOTAL_LAPS:
                    self.current_lap += 1
                    self.lap_start_time = current_time
                    self.current_lap_time = 0
                else:
                    # Race finished
                    self.is_racing = False
                    if PRINT_LAP_EVENTS:
                        print(f"🏁 {self.car_name} FINISHED RACE! "
                              f"Total time: {sum(self.lap_times):.2f}s")
                
                return True
        
        return False
    
    def get_lap_info(self):
        """
        Get current lap information
        
        Returns:
            dict: Lap information
        """
        return {
            'car_id': self.car_id,
            'car_name': self.car_name,
            'current_lap': self.current_lap,
            'total_laps': TOTAL_LAPS,
            'is_racing': self.is_racing,
            'race_started': self.race_started,
            'current_lap_time': self.current_lap_time,
            'last_lap_time': self.last_lap_time,
            'best_lap_time': self.best_lap_time if self.best_lap_time != float('inf') else 0,
            'total_laps_completed': self.total_laps,
            'race_finished': self.total_laps >= TOTAL_LAPS,
            'lap_times': self.lap_times.copy()
        }
    
    def reset(self):
        """Reset lap tracker for new race"""
        self.current_lap = 0
        self.total_laps = 0
        self.is_racing = False
        self.race_started = False
        self.last_x = None
        self.last_y = None
        self.crossed_start = False
        self.lap_start_time = None
        self.current_lap_time = 0
        self.last_lap_time = 0
        self.best_lap_time = float('inf')
        self.lap_times = []
        self.on_start_side = None
        
        if PRINT_LAP_EVENTS:
            print(f"🔄 {self.car_name} lap tracker reset")


class RaceManager:
    """Manages the overall race state"""
    
    def __init__(self):
        """Initialize race manager"""
        self.lap_trackers = {}  # car_id -> LapTracker
        self.race_active = False
        self.race_start_time = None
        self.race_end_time = None
        
    def register_car(self, car_id, car_name):
        """
        Register a car for racing
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        if car_id not in self.lap_trackers:
            self.lap_trackers[car_id] = LapTracker(car_id, car_name)
            print(f"🏎️  Registered {car_name} for racing")
    
    def update_car_position(self, car_id, x, y, current_time):
        """
        Update car position and check for lap completion
        
        Args:
            car_id: Car identifier
            x: Current X position
            y: Current Y position
            current_time: Current timestamp
            
        Returns:
            dict: Event info if lap completed, None otherwise
        """
        if car_id not in self.lap_trackers:
            return None
        
        tracker = self.lap_trackers[car_id]
        lap_completed = tracker.update_position(x, y, current_time)
        
        # Update race state
        if not self.race_active and tracker.race_started:
            self.race_active = True
            self.race_start_time = current_time
            print(f"🏁 RACE STARTED!")
        
        # Check if race ended
        if self.race_active and self._all_cars_finished():
            self.race_active = False
            self.race_end_time = current_time
            print(f"🏁 RACE ENDED! Duration: {self.race_end_time - self.race_start_time:.2f}s")
        
        if lap_completed:
            return {
                'type': 'lap_completed',
                'car_id': car_id,
                'lap_info': tracker.get_lap_info()
            }
        
        return None
    
    def _all_cars_finished(self):
        """Check if all cars have finished the race"""
        if not self.lap_trackers:
            return False
        return all(t.total_laps >= TOTAL_LAPS for t in self.lap_trackers.values())
    
    def get_car_lap_info(self, car_id):
        """Get lap info for specific car"""
        if car_id in self.lap_trackers:
            return self.lap_trackers[car_id].get_lap_info()
        return None
    
    def get_all_lap_info(self):
        """Get lap info for all cars"""
        return {car_id: tracker.get_lap_info() 
                for car_id, tracker in self.lap_trackers.items()}
    
    def get_leaderboard(self):
        """
        Get current race leaderboard
        
        Returns:
            list: Sorted list of car info (by laps and time)
        """
        cars = []
        for tracker in self.lap_trackers.values():
            info = tracker.get_lap_info()
            info['total_time'] = sum(info['lap_times'])
            cars.append(info)
        
        # Sort by: most laps completed, then by total time
        cars.sort(key=lambda c: (-c['total_laps_completed'], c['total_time']))
        
        return cars
    
    def reset_race(self):
        """Reset all lap trackers for new race"""
        for tracker in self.lap_trackers.values():
            tracker.reset()
        
        self.race_active = False
        self.race_start_time = None
        self.race_end_time = None
        
        print("🔄 Race reset - ready for new race")
    
    def is_race_active(self):
        """Check if race is currently active"""
        return self.race_active
