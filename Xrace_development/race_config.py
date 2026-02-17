"""Race Management System – Master Configuration"""

# ── Anchors & Network ─────────────────────────────────────────
ANCHOR_COUNT = 4
TAG_COUNT    = 3
UDP_PORT     = 4210
ANCHOR_POSITIONS = {0:(0,0), 1:(200,0), 2:(200,200), 3:(0,200)}

# ── Display ───────────────────────────────────────────────────
SCREEN_X        = 1400
SCREEN_Y        = 900
REFRESH_RATE    = 0.033
GRID_SPACING_CM = 50
TAG_RADIUS      = 14
ANCHOR_RADIUS   = 8
TAG_TIMEOUT     = 5
TRAIL_LENGTH    = 30
SHOW_RANGE_CIRCLES   = False
SHOW_COLLISION_CIRCLES = True
COLLISION_INDICATOR_RADIUS = 25
RACE_STATUS_X   = 500
RACE_STATUS_Y   = 20
LEADERBOARD_X   = 980
LEADERBOARD_Y   = 50
INCIDENT_X      = 980
INCIDENT_Y      = 500

# ── Race ─────────────────────────────────────────────────────
TOTAL_LAPS          = 10
MAX_CARS            = 3
MIN_LAPS_TO_QUALIFY = 3

# ── Start / Finish Line ───────────────────────────────────────
START_LINE_X          = 100
START_LINE_Y1         = 30
START_LINE_Y2         = 70
START_LINE_ORIENTATION = 'vertical'
LINE_CROSSING_THRESHOLD = 20   # cm
MIN_LAP_TIME            = 3.0  # s  (cooldown / double-count guard)
SHOW_START_LINE         = True

# ── ELP Scoring ───────────────────────────────────────────────
# ELP = Raw Lap Time + Σ(penalties) − Σ(bonuses)
WALL_HIT_PENALTY              = 5.0   # s per wall touch
CAR_COLLISION_ATTACKER_PENALTY = 5.0  # s added to attacker
CAR_COLLISION_VICTIM_BONUS     = 2.0  # s subtracted from victim
CORNER_CUT_PENALTY             = 3.0  # s per missed checkpoint
CORNER_CUT_VOID_LAP            = False
PIT_ZONE_MAX_SPEED_CM_S        = 30.0
PIT_ZONE_OVERSPEED_PENALTY     = 2.0  # s added to first lap

# ── Collision Detection ───────────────────────────────────────
CAR_COLLISION_DISTANCE_CM  = 25
CAR_COLLISION_COOLDOWN     = 1.0
SPEED_DIFF_THRESHOLD       = 10.0
WALL_TOLERANCE_CM          = 5.0
WALL_COLLISION_COOLDOWN    = 0.5

# ── Anti-Griefing ─────────────────────────────────────────────
GHOSTING_SPEED_THRESHOLD   = 0.20   # fraction of track avg
GHOSTING_TIME_THRESHOLD    = 3.0    # s
MAX_PLAUSIBLE_SPEED_CM_S   = 278    # ≈ 100 km/h

# ── Checkpoints (corner-cut detection) ───────────────────────
CHECKPOINTS      = []    # list of (x,y) in cm; empty = disabled
CHECKPOINT_RADIUS = 25   # cm

# ── Speed ─────────────────────────────────────────────────────
SPEED_CALC_METHOD     = 'both'
SPEED_AVERAGE_SAMPLES = 10
SPEED_DISPLAY_UNIT    = 'km/h'
SPEED_UPDATE_INTERVAL = 0.1

# ── Logging ───────────────────────────────────────────────────
ENABLE_RACE_LOGGING = True
RACE_LOG_FILE       = 'race_events.csv'
LOG_LAP_TIMES       = True
LOG_COLLISIONS      = True
LOG_POSITIONS       = False

# ── Print Flags ───────────────────────────────────────────────
PRINT_LAP_EVENTS       = True
PRINT_COLLISION_EVENTS = True
PRINT_WALL_EVENTS      = True
PRINT_ANOMALIES        = True
PRINT_SPEED_UPDATES    = False
