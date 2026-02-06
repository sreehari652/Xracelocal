"""
UWB Racing Tracker - Collision Detection Module
Handles collision detection, initiator determination, and scoring
"""

import time
import math
from race_config import *


class CollisionEvent:
    """Represents a single collision event"""
    
    def __init__(self, car1_id, car2_id, initiator_id, lap_number, timestamp):
        """
        Initialize collision event
        
        Args:
            car1_id: First car ID
            car2_id: Second car ID
            initiator_id: ID of car that initiated collision
            lap_number: Lap when collision occurred
            timestamp: When collision occurred
        """
        self.car1_id = car1_id
        self.car2_id = car2_id
        self.initiator_id = initiator_id
        self.victim_id = car2_id if initiator_id == car1_id else car1_id
        self.lap_number = lap_number
        self.timestamp = timestamp
        self.processed = False
    
    def get_summary(self):
        """Get collision summary"""
        return {
            'car1_id': self.car1_id,
            'car2_id': self.car2_id,
            'initiator_id': self.initiator_id,
            'victim_id': self.victim_id,
            'lap': self.lap_number,
            'timestamp': self.timestamp
        }


class CarCollisionTracker:
    """Tracks collisions for a single car"""
    
    def __init__(self, car_id, car_name):
        """
        Initialize collision tracker for a car
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        self.car_id = car_id
        self.car_name = car_name
        
        # Collision statistics
        self.total_collisions = 0
        self.collisions_initiated = 0  # Collisions caused by this car
        self.collisions_received = 0   # Collisions this car was victim of
        
        # Lap-based collision tracking
        self.collisions_per_lap = {}  # lap_number -> count
        self.collision_history = []   # List of CollisionEvent objects
        
        # Points system
        self.points = 0
        self.points_history = []  # List of (lap, points_change, reason)
        
        # Current collision tracking (to avoid duplicate detections)
        self.active_collisions = {}  # other_car_id -> timestamp
        
        # Visual indicator
        self.last_collision_time = 0
        self.is_in_collision = False
    
    def check_collision(self, other_car_id, distance, current_time, current_lap):
        """
        Check if collision occurred with another car
        
        Args:
            other_car_id: Other car's ID
            distance: Distance to other car
            current_time: Current timestamp
            current_lap: Current lap number
            
        Returns:
            bool: True if new collision detected
        """
        # Check if within collision distance
        if distance <= COLLISION_DISTANCE_THRESHOLD:
            # Check if this is a new collision or continuation
            if other_car_id in self.active_collisions:
                last_time = self.active_collisions[other_car_id]
                if current_time - last_time < COLLISION_TIME_WINDOW:
                    # Same collision event continuing
                    self.active_collisions[other_car_id] = current_time
                    return False
            
            # New collision!
            self.active_collisions[other_car_id] = current_time
            self.last_collision_time = current_time
            self.is_in_collision = True
            
            return True
        else:
            # Not in collision range
            if other_car_id in self.active_collisions:
                del self.active_collisions[other_car_id]
            
            # Update visual indicator
            if current_time - self.last_collision_time > COLLISION_FLASH_DURATION:
                self.is_in_collision = False
        
        return False
    
    def record_collision(self, collision_event):
        """
        Record a collision event
        
        Args:
            collision_event: CollisionEvent object
        """
        self.collision_history.append(collision_event)
        self.total_collisions += 1
        
        # Track initiator vs victim
        if collision_event.initiator_id == self.car_id:
            self.collisions_initiated += 1
        else:
            self.collisions_received += 1
        
        # Track per lap
        lap = collision_event.lap_number
        if lap not in self.collisions_per_lap:
            self.collisions_per_lap[lap] = 0
        self.collisions_per_lap[lap] += 1
    
    def apply_collision_points(self, points_change, lap_number, reason):
        """
        Apply points change from collision
        
        Args:
            points_change: Points to add (positive) or subtract (negative)
            lap_number: Lap when points were applied
            reason: Reason for point change
        """
        self.points += points_change
        self.points_history.append((lap_number, points_change, reason))
        
        if PRINT_COLLISION_EVENTS:
            sign = "+" if points_change > 0 else ""
            print(f"  💰 {self.car_name}: {sign}{points_change} points ({reason}) "
                  f"- Total: {self.points}")
    
    def get_lap_collisions(self, lap_number):
        """Get number of collisions in a specific lap"""
        return self.collisions_per_lap.get(lap_number, 0)
    
    def get_collision_info(self):
        """Get comprehensive collision information"""
        return {
            'car_id': self.car_id,
            'car_name': self.car_name,
            'total_collisions': self.total_collisions,
            'initiated': self.collisions_initiated,
            'received': self.collisions_received,
            'points': self.points,
            'collisions_per_lap': self.collisions_per_lap.copy(),
            'is_in_collision': self.is_in_collision
        }
    
    def reset(self):
        """Reset collision tracker"""
        self.total_collisions = 0
        self.collisions_initiated = 0
        self.collisions_received = 0
        self.collisions_per_lap = {}
        self.collision_history = []
        self.points = 0
        self.points_history = []
        self.active_collisions = {}
        self.last_collision_time = 0
        self.is_in_collision = False


class CollisionDetector:
    """Manages collision detection for all cars"""
    
    def __init__(self):
        """Initialize collision detector"""
        self.car_trackers = {}  # car_id -> CarCollisionTracker
        self.pending_lap_collisions = {}  # lap_number -> list of CollisionEvents
        self.speed_manager = None  # Will be set externally
    
    def register_car(self, car_id, car_name):
        """
        Register a car for collision tracking
        
        Args:
            car_id: Car identifier
            car_name: Car display name
        """
        if car_id not in self.car_trackers:
            self.car_trackers[car_id] = CarCollisionTracker(car_id, car_name)
            print(f"💥 Collision tracking enabled for {car_name}")
    
    def set_speed_manager(self, speed_manager):
        """Set speed manager for determining collision initiator"""
        self.speed_manager = speed_manager
    
    def update(self, cars_data, current_time):
        """
        Update collision detection for all cars
        
        Args:
            cars_data: Dict of car_id -> {'x': x, 'y': y, 'lap': lap_number}
            current_time: Current timestamp
            
        Returns:
            list: List of new CollisionEvent objects
        """
        new_collisions = []
        car_ids = list(cars_data.keys())
        
        # Check all pairs of cars
        for i in range(len(car_ids)):
            for j in range(i + 1, len(car_ids)):
                car1_id = car_ids[i]
                car2_id = car_ids[j]
                
                car1_data = cars_data[car1_id]
                car2_data = cars_data[car2_id]
                
                # Calculate distance
                dx = car1_data['x'] - car2_data['x']
                dy = car1_data['y'] - car2_data['y']
                distance = math.sqrt(dx * dx + dy * dy)
                
                # Check for collision from both cars' perspectives
                car1_tracker = self.car_trackers[car1_id]
                car2_tracker = self.car_trackers[car2_id]
                
                collision1 = car1_tracker.check_collision(
                    car2_id, distance, current_time, car1_data['lap']
                )
                collision2 = car2_tracker.check_collision(
                    car1_id, distance, current_time, car2_data['lap']
                )
                
                # If new collision detected
                if collision1 or collision2:
                    # Determine initiator
                    initiator_id = self._determine_initiator(
                        car1_id, car2_id, car1_data, car2_data
                    )
                    
                    # Use the lap number of the initiator
                    lap_number = (car1_data['lap'] if initiator_id == car1_id 
                                 else car2_data['lap'])
                    
                    # Create collision event
                    collision_event = CollisionEvent(
                        car1_id, car2_id, initiator_id, lap_number, current_time
                    )
                    
                    new_collisions.append(collision_event)
                    
                    # Record in both cars' trackers
                    car1_tracker.record_collision(collision_event)
                    car2_tracker.record_collision(collision_event)
                    
                    # Add to pending lap collisions
                    if lap_number not in self.pending_lap_collisions:
                        self.pending_lap_collisions[lap_number] = []
                    self.pending_lap_collisions[lap_number].append(collision_event)
                    
                    # Log collision
                    if PRINT_COLLISION_EVENTS:
                        initiator_name = (car1_tracker.car_name if initiator_id == car1_id 
                                        else car2_tracker.car_name)
                        victim_name = (car2_tracker.car_name if initiator_id == car1_id 
                                     else car1_tracker.car_name)
                        
                        print(f"💥 COLLISION in Lap {lap_number}! "
                              f"{initiator_name} collided with {victim_name}")
        
        return new_collisions
    
    def _determine_initiator(self, car1_id, car2_id, car1_data, car2_data):
        """
        Determine which car initiated the collision
        
        Args:
            car1_id: First car ID
            car2_id: Second car ID
            car1_data: First car data dict
            car2_data: Second car data dict
            
        Returns:
            int: ID of initiating car
        """
        if COLLISION_INITIATOR_METHOD == 'speed' and self.speed_manager:
            # Faster car is considered initiator
            speed1 = self.speed_manager.get_current_speed(car1_id)
            speed2 = self.speed_manager.get_current_speed(car2_id)
            
            speed_diff = abs(speed1 - speed2)
            
            if speed_diff > COLLISION_SPEED_DIFF_THRESHOLD:
                return car1_id if speed1 > speed2 else car2_id
            else:
                # Speeds too similar, use position (rear car is initiator)
                return self._determine_rear_car(car1_id, car2_id, car1_data, car2_data)
        
        elif COLLISION_INITIATOR_METHOD == 'rear':
            # Car from behind is initiator
            return self._determine_rear_car(car1_id, car2_id, car1_data, car2_data)
        
        else:
            # Default: car with lower ID
            return min(car1_id, car2_id)
    
    def _determine_rear_car(self, car1_id, car2_id, car1_data, car2_data):
        """Determine which car is behind (simplified - assumes linear track)"""
        # This is simplified - you might want to use track position or direction
        # For now, using X position (assumes horizontal track direction)
        if car1_data['x'] < car2_data['x']:
            return car1_id
        else:
            return car2_id
    
    def process_lap_complete(self, car_id, lap_number):
        """
        Process collisions when a car completes a lap
        
        Args:
            car_id: Car that completed the lap
            lap_number: Lap that was completed
        """
        if lap_number not in self.pending_lap_collisions:
            return
        
        # Process all collisions from this lap involving this car
        for collision in self.pending_lap_collisions[lap_number]:
            if collision.processed:
                continue
            
            if collision.car1_id == car_id or collision.car2_id == car_id:
                # Apply points
                initiator_tracker = self.car_trackers[collision.initiator_id]
                victim_tracker = self.car_trackers[collision.victim_id]
                
                # Penalty for initiator, reward for victim
                initiator_tracker.apply_collision_points(
                    COLLISION_POINTS_PENALTY, lap_number,
                    f"Initiated collision with {victim_tracker.car_name}"
                )
                
                victim_tracker.apply_collision_points(
                    COLLISION_POINTS_REWARD, lap_number,
                    f"Hit by {initiator_tracker.car_name}"
                )
                
                collision.processed = True
    
    def get_car_collision_info(self, car_id):
        """Get collision info for specific car"""
        if car_id in self.car_trackers:
            return self.car_trackers[car_id].get_collision_info()
        return None
    
    def get_all_collision_info(self):
        """Get collision info for all cars"""
        return {car_id: tracker.get_collision_info() 
                for car_id, tracker in self.car_trackers.items()}
    
    def get_points_leaderboard(self):
        """
        Get leaderboard sorted by points
        
        Returns:
            list: Sorted list of cars by points
        """
        cars = []
        for tracker in self.car_trackers.values():
            cars.append({
                'car_id': tracker.car_id,
                'car_name': tracker.car_name,
                'points': tracker.points,
                'total_collisions': tracker.total_collisions,
                'initiated': tracker.collisions_initiated,
                'received': tracker.collisions_received
            })
        
        cars.sort(key=lambda c: -c['points'])  # Sort by points descending
        return cars
    
    def reset_all(self):
        """Reset all collision trackers"""
        for tracker in self.car_trackers.values():
            tracker.reset()
        self.pending_lap_collisions = {}
        print("🔄 All collision trackers reset")
