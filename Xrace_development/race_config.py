"""
UWB Racing Tracker - Racing Configuration
All race-specific constants and settings
"""

# ============== Race Configuration ==============
TOTAL_LAPS = 10  # Total laps in the race
MAX_CARS = 3  # Maximum number of cars (tags)

# ============== Start/Finish Line Configuration ==============
# START LINE (where race begins)
START_LINE_X = 200  # X position in cm (adjusted for new track)
START_LINE_Y1 = 50   # Start Y position
START_LINE_Y2 = 550  # End Y position
START_LINE_COLOR = [0, 200, 0]  # Green for start
START_LINE_WIDTH = 5

# FINISH LINE (where laps complete)
FINISH_LINE_X = 300  # Same position as start (traditional racing)
FINISH_LINE_Y1 = 50
FINISH_LINE_Y2 = 550
FINISH_LINE_COLOR = [255, 0, 0]  # Red for finish
FINISH_LINE_WIDTH = 5

# Use same line for both start and finish? (traditional racing)
USE_SAME_LINE_FOR_START_FINISH = True

# Or use separate lines (if False, will use both lines separately)
# START_LINE_X = 200  # Example: separate start line
# FINISH_LINE_X = 400  # Example: separate finish line

# Start line orientation: 'vertical' or 'horizontal'
START_LINE_ORIENTATION = 'vertical'

# Crossing detection threshold (how close to line to register crossing)
LINE_CROSSING_THRESHOLD = 30  # cm (increased for better detection)

# Minimum time between lap completions (prevents false triggers)
MIN_LAP_TIME = 3.0  # seconds

# ============== Speed Calculation ==============
# Speed calculation method: 'instantaneous' or 'average'
SPEED_CALC_METHOD = 'both'

# Number of samples for average speed calculation
SPEED_AVERAGE_SAMPLES = 10

# Speed display units: 'cm/s', 'm/s', 'km/h'
SPEED_DISPLAY_UNIT = 'km/h'

# Speed update frequency
SPEED_UPDATE_INTERVAL = 0.1  # seconds

# ============== Collision Detection ==============
# Collision detection distance threshold
COLLISION_DISTANCE_THRESHOLD = 30  # cm (if cars within this distance, collision)

# Collision detection time window (how long to register as same collision)
COLLISION_TIME_WINDOW = 1.0  # seconds

# Collision points system
COLLISION_POINTS_PENALTY = -5  # Points deducted from initiator
COLLISION_POINTS_REWARD = 5   # Points added to victim

# Collision initiator determination
# Method: 'speed' (faster car is initiator) or 'rear' (car from behind)
COLLISION_INITIATOR_METHOD = 'speed'

# Minimum speed difference to determine initiator (cm/s)
COLLISION_SPEED_DIFF_THRESHOLD = 20

# ============== Visualization Settings ==============
# Lap counter position offset from car
LAP_DISPLAY_OFFSET_X = 18
LAP_DISPLAY_OFFSET_Y = 25

# Speed display position offset from car
SPEED_DISPLAY_OFFSET_X = 18
SPEED_DISPLAY_OFFSET_Y = 40

# Collision indicator settings
COLLISION_FLASH_DURATION = 1.0  # seconds
COLLISION_INDICATOR_COLOR = [255, 0, 0]  # Red
COLLISION_INDICATOR_RADIUS = 25

# Race status display position
RACE_STATUS_X = 400
RACE_STATUS_Y = 10

# Leaderboard position
LEADERBOARD_X = 900
LEADERBOARD_Y = 100

# ============== Data Logging ==============
# Enable race data logging
ENABLE_RACE_LOGGING = True

# Log file path
RACE_LOG_FILE = 'race_data.csv'

# What to log
LOG_LAP_TIMES = True
LOG_SPEEDS = True
LOG_COLLISIONS = True
LOG_POSITIONS = True

# ============== Race Rules ==============
# Race start detection
# 'first_cross' - Race starts when first car crosses start line
# 'all_ready' - Race starts when all cars are detected and cross line
RACE_START_MODE = 'first_cross'

# Require all cars to finish current lap before starting new race
REQUIRE_ALL_FINISH = False

# Disqualification rules
ENABLE_DISQUALIFICATION = False
MAX_COLLISIONS_BEFORE_DQ = 10

# ============== Debug Settings ==============
SHOW_START_LINE = True
SHOW_COLLISION_CIRCLES = True
SHOW_LAP_SECTORS = False
PRINT_LAP_EVENTS = True
PRINT_COLLISION_EVENTS = True
PRINT_SPEED_UPDATES = False