"""
UWB Position Tracker - Configuration
All constants and configuration parameters
UPDATED: Fixed leaderboard position and display scaling
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
GRAY = [200, 200, 200]
LIGHT_GRAY = [240, 240, 240]
YELLOW = [255, 200, 0]
ORANGE = [255, 165, 0]
RANGE_CIRCLE_COLOR = [200, 200, 255]

# ============== UWB System Configuration ==============
ANCHOR_COUNT = 4
TAG_COUNT = 3
UDP_PORT = 4210

# Anchor positions (in cm) - ACTUAL POSITIONS
# The track area is INSIDE these anchors
ANCHOR_POSITIONS = {
    0: (0, 0),        # Bottom-left
    1: (200, 0),      # Bottom-right
    2: (200, 200),    # Top-right
    3: (0, 200)       # Top-left
}

# ============== Tracking Parameters ==============
# Kalman Filter settings
KALMAN_PROCESS_NOISE = 0.1
KALMAN_MEASUREMENT_NOISE = 5.0

# History settings
TRAIL_LENGTH = 30
TAG_TIMEOUT = 5
UPDATE_RECENT_THRESHOLD = 0.5

# Quality thresholds
QUALITY_EXCELLENT_ANCHORS = 4
QUALITY_GOOD_ANCHORS = 3
QUALITY_FAIR_ANCHORS = 2

# RSSI weighting parameters
RSSI_EXCELLENT = -60
RSSI_POOR = -90
RSSI_MIN_WEIGHT = 0.1
RSSI_NORMALIZATION = 30

# ============== Visual Settings ==============
TAG_RADIUS = 14
ANCHOR_RADIUS = 8
PULSE_MULTIPLIER = 6
GRID_SPACING_CM = 50

# Font settings
FONT_TITLE = ("Consolas", 26, True)
FONT_LABEL = ("Consolas", 18, False)
FONT_INFO = ("Consolas", 16, False)
FONT_SMALL = ("Consolas", 14, False)
FONT_QUALITY = ("Consolas", 12, False)

# ============== Network Settings ==============
UDP_TIMEOUT = 0.1
UDP_BUFFER_SIZE = 2048
CONNECTION_TIMEOUT = 2

# ============== Debug Settings ==============
SHOW_RANGE_CIRCLES = False
PRINT_PACKET_LOGS = False
PRINT_CALCULATION_DETAILS = False