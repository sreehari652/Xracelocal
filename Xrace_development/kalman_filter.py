"""
UWB Position Tracker - Kalman Filter Module
Provides smooth position tracking using Kalman filtering
"""

from config import KALMAN_PROCESS_NOISE, KALMAN_MEASUREMENT_NOISE


class KalmanFilter:
    """Simple 2D Kalman filter for position smoothing"""

    def __init__(self, process_noise=None, measurement_noise=None):
        """
        Initialize Kalman filter
        
        Args:
            process_noise: How much we trust the model (default from config)
            measurement_noise: How much we trust measurements (default from config)
        """
        self.x = 0
        self.y = 0
        self.vx = 0
        self.vy = 0
        self.initialized = False

        # Process noise (how much we trust the model)
        self.q = process_noise if process_noise is not None else KALMAN_PROCESS_NOISE
        # Measurement noise (how much we trust the measurements)
        self.r = measurement_noise if measurement_noise is not None else KALMAN_MEASUREMENT_NOISE

    def update(self, measured_x, measured_y, dt=0.03):
        """
        Update filter with new measurement
        
        Args:
            measured_x: Measured x position
            measured_y: Measured y position
            dt: Time delta since last update
            
        Returns:
            tuple: (filtered_x, filtered_y)
        """
        if not self.initialized:
            self.x = measured_x
            self.y = measured_y
            self.initialized = True
            return self.x, self.y

        # Predict
        self.x += self.vx * dt
        self.y += self.vy * dt

        # Update
        k = self.r / (self.r + self.q)  # Kalman gain

        self.x = self.x + k * (measured_x - self.x)
        self.y = self.y + k * (measured_y - self.y)

        # Update velocity estimate
        self.vx = (measured_x - self.x) / dt
        self.vy = (measured_y - self.y) / dt

        return self.x, self.y

    def reset(self):
        """Reset the filter to uninitialized state"""
        self.x = 0
        self.y = 0
        self.vx = 0
        self.vy = 0
        self.initialized = False

    def get_velocity(self):
        """
        Get current velocity estimate
        
        Returns:
            tuple: (vx, vy) velocity in cm/s
        """
        return self.vx, self.vy

    def get_speed(self):
        """
        Get current speed (magnitude of velocity)
        
        Returns:
            float: Speed in cm/s
        """
        return (self.vx ** 2 + self.vy ** 2) ** 0.5
