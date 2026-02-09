"""
UWB Position Tracker - Configuration
All constants and configuration parameters
"""

# ============== Display Configuration ==============
SCREEN_X = 1400
SCREEN_Y = 900
DISPLAY_TITLE = "UWB Enhanced Tracker - 3 Tags, 4 Anchors"
FPS = 60
REFRESH_RATE = 0.033  # 30 FPS for display updates

# ============== Colors ==============
RED = [255, 0, 0]
BLACK = [0, 0, 0]
WHITE = [255, 255, 255]
GREEN = [0, 255, 0]
BLUE = [0, 100, 255]
GRAY = [100, 100, 100]  # Darker gray for visibility on white
LIGHT_GRAY = [220, 220, 220]
DARK_GRAY = [50, 50, 50]  # For text on white background
YELLOW = [255, 200, 0]
ORANGE = [255, 165, 0]
RANGE_CIRCLE_COLOR = [200, 200, 255]

# Background color
BACKGROUND_COLOR = WHITE  # Changed from BLACK to WHITE

# ============== UWB System Configuration ==============
ANCHOR_COUNT = 4
TAG_COUNT = 3  # Optimized for 3 tags
UDP_PORT = 4210

# Anchor positions (in cm) - MEASURE YOUR ACTUAL POSITIONS!
# Moved to center of screen with larger spacing
ANCHOR_POSITIONS = {
    0: (50, 50),      # Bottom-left corner
    1: (550, 50),     # Bottom-right corner (increased spacing)
    2: (550, 550),    # Top-right corner (increased spacing)
    3: (50, 550)      # Top-left corner
}

# ============== Tracking Parameters ==============
# Kalman Filter settings
KALMAN_PROCESS_NOISE = 0.1  # How much we trust the model
KALMAN_MEASUREMENT_NOISE = 5.0  # How much we trust measurements

# History settings
TRAIL_LENGTH = 30  # Number of position points to keep in history
TAG_TIMEOUT = 5  # Seconds before tag is considered inactive
UPDATE_RECENT_THRESHOLD = 0.5  # Seconds for "active pulse" indicator

# Quality thresholds
QUALITY_EXCELLENT_ANCHORS = 4
QUALITY_GOOD_ANCHORS = 3
QUALITY_FAIR_ANCHORS = 2

# RSSI weighting parameters
RSSI_EXCELLENT = -60  # dBm
RSSI_POOR = -90  # dBm
RSSI_MIN_WEIGHT = 0.1
RSSI_NORMALIZATION = 30

# ============== Visual Settings ==============
TAG_RADIUS = 14
ANCHOR_RADIUS = 8
PULSE_MULTIPLIER = 6
GRID_SPACING_CM = 50  # Grid spacing in centimeters

# Font settings
FONT_TITLE = ("Consolas", 26, True)  # (name, size, bold)
FONT_LABEL = ("Consolas", 18, False)
FONT_INFO = ("Consolas", 16, False)
FONT_SMALL = ("Consolas", 14, False)
FONT_QUALITY = ("Consolas", 12, False)

# ============== Network Settings ==============
UDP_TIMEOUT = 0.1  # Socket timeout in seconds
UDP_BUFFER_SIZE = 2048
CONNECTION_TIMEOUT = 2  # Seconds before showing "waiting for data"

# ============== Debug Settings ==============
SHOW_RANGE_CIRCLES = False  # Default debug mode state
PRINT_PACKET_LOGS = False  # Print packet reception logs
PRINT_CALCULATION_DETAILS = False  # Print trilateration details