# UWB Racing Tracker - Complete Documentation

## 🏎️ Racing System Overview

This is a **professional racing tracking system** with:
- ✅ **Lap timing** with start/finish line detection
- ✅ **Real-time speed calculation** (instantaneous and average)
- ✅ **Collision detection** with initiator determination
- ✅ **Points system** based on collisions
- ✅ **Live leaderboard** and statistics
- ✅ **Race data logging** to CSV

---

## 📁 Racing Module Files

### Core Racing Modules

1. **`race_config.py`** - All racing-specific configuration
2. **`lap_tracker.py`** - Lap timing and race management
3. **`speed_tracker.py`** - Speed calculation and tracking
4. **`collision_detector.py`** - Collision detection and scoring
5. **`race_renderer.py`** - Racing visualization
6. **`racing_main.py`** - Main racing application

### Base Modules (Required)

- `config.py` - Base system configuration
- `uwb_device.py` - Tag and Anchor classes
- `kalman_filter.py` - Position smoothing
- `positioning.py` - Trilateration algorithms
- `network.py` - UDP communication
- `renderer.py` - Base visualization

---

## 🚀 Quick Start

### 1. Configure Your Race

Edit `race_config.py`:

```python
# Race setup
TOTAL_LAPS = 10
MAX_CARS = 3

# Start/Finish line (adjust to your track)
START_LINE_X = 215  # cm
START_LINE_Y1 = 0
START_LINE_Y2 = 470
START_LINE_ORIENTATION = 'vertical'

# Collision distance threshold
COLLISION_DISTANCE_THRESHOLD = 30  # cm

# Points system
COLLISION_POINTS_PENALTY = -5
COLLISION_POINTS_REWARD = 5
```

### 2. Run the Race

```bash
python racing_main.py
```

### 3. Use Controls

- **ESC/Q** - Quit
- **R** - Reset race
- **D** - Toggle debug (shows collision circles)
- **S** - Print full statistics
- **L** - Print lap times
- **C** - Print collision report
- **P** - Print points leaderboard

---

## 📋 Feature Details

### 1. LAP TRACKING SYSTEM

#### How It Works

1. **Start Line Detection**
   - Cars must cross the start/finish line to begin racing
   - Initial appearance doesn't count as lap start
   - Race starts only when car crosses from one side to the other

2. **Lap Counting**
   - Each line crossing increments lap counter
   - Minimum lap time prevents false triggers
   - Tracks current lap and total laps completed

3. **Lap Timing**
   - Records time for each lap
   - Tracks best lap time
   - Calculates total race time

#### Key Classes

**`LapTracker`** - Tracks laps for one car
```python
lap_info = {
    'current_lap': 3,           # Currently on lap 3
    'total_laps': 10,           # Race is 10 laps
    'is_racing': True,          # Currently racing
    'current_lap_time': 12.5,   # Current lap in progress
    'last_lap_time': 11.8,      # Previous lap time
    'best_lap_time': 11.2,      # Best lap so far
    'lap_times': [12.1, 11.8, ...] # All lap times
}
```

**`RaceManager`** - Manages overall race
- Registers all cars
- Detects race start/end
- Generates leaderboard
- Provides race statistics

#### Configuration Options

```python
# Minimum time between laps (prevents false crossings)
MIN_LAP_TIME = 3.0  # seconds

# Line crossing threshold
LINE_CROSSING_THRESHOLD = 20  # cm from line

# Race start mode
RACE_START_MODE = 'first_cross'  # Start when first car crosses
# RACE_START_MODE = 'all_ready'  # Wait for all cars
```

---

### 2. SPEED CALCULATION SYSTEM

#### How It Works

1. **Instantaneous Speed**
   - Calculated from last 2 position updates
   - Updates every frame (~30Hz)
   - Shows real-time speed changes

2. **Average Speed**
   - Calculated from position history buffer
   - Smooths out noise and jitter
   - More stable than instantaneous

3. **Lap-Based Speed**
   - Average speed per lap
   - Allows lap-to-lap comparison
   - Stored in history

#### Key Classes

**`SpeedTracker`** - Tracks speed for one car
```python
speed_info = {
    'instantaneous': 45.2,    # Current speed
    'average': 42.8,          # Average from buffer
    'max': 58.3,              # Maximum recorded
    'unit': 'km/h',           # Display unit
    'lap_speeds': [43.1, 44.2, ...], # Per-lap averages
    'current_lap_avg': 43.5   # Current lap average
}
```

**`SpeedManager`** - Manages all car speeds
- Updates all car positions
- Notified on lap completion
- Provides speed statistics

#### Configuration Options

```python
# Speed calculation method
SPEED_CALC_METHOD = 'both'  # 'instantaneous', 'average', or 'both'

# History buffer size
SPEED_AVERAGE_SAMPLES = 10  # Number of samples

# Display units
SPEED_DISPLAY_UNIT = 'km/h'  # 'cm/s', 'm/s', or 'km/h'
```

#### Speed Conversions

- `cm/s` → `m/s`: divide by 100
- `cm/s` → `km/h`: divide by 100, multiply by 3.6

Example: 100 cm/s = 1 m/s = 3.6 km/h

---

### 3. COLLISION DETECTION SYSTEM

#### How It Works

1. **Distance-Based Detection**
   - Checks distance between all car pairs
   - Collision if distance < threshold
   - Time window prevents duplicate detections

2. **Initiator Determination**
   - **Speed Method**: Faster car is initiator
   - **Rear Method**: Car from behind is initiator
   - Requires minimum speed difference

3. **Points System**
   - Initiator loses points (penalty)
   - Victim gains points (reward)
   - Applied after lap completion

4. **Lap-Based Processing**
   - Collisions tracked per lap
   - Points applied when lap finishes
   - Prevents mid-lap point changes

#### Key Classes

**`CollisionEvent`** - Single collision
```python
collision = {
    'car1_id': 0,
    'car2_id': 1,
    'initiator_id': 0,    # Car 0 caused it
    'victim_id': 1,       # Car 1 was hit
    'lap': 3,
    'timestamp': 1234.56
}
```

**`CarCollisionTracker`** - Per-car tracking
```python
collision_info = {
    'total_collisions': 5,    # Total collisions
    'initiated': 3,           # Caused by this car
    'received': 2,            # Hit by others
    'points': -5,             # Current points
    'collisions_per_lap': {1: 1, 2: 2, 3: 2},
    'is_in_collision': False  # Currently colliding
}
```

**`CollisionDetector`** - System manager
- Detects all collisions
- Determines initiators
- Manages points system
- Generates reports

#### Configuration Options

```python
# Detection threshold
COLLISION_DISTANCE_THRESHOLD = 30  # cm

# Time window (same collision)
COLLISION_TIME_WINDOW = 1.0  # seconds

# Points system
COLLISION_POINTS_PENALTY = -5  # Initiator loses
COLLISION_POINTS_REWARD = 5    # Victim gains

# Initiator method
COLLISION_INITIATOR_METHOD = 'speed'  # or 'rear'

# Minimum speed difference
COLLISION_SPEED_DIFF_THRESHOLD = 20  # cm/s
```

#### Collision Scenarios

**Example 1: Speed-Based**
- Car A: 80 km/h
- Car B: 50 km/h
- Result: Car A is initiator (faster)

**Example 2: Too Similar**
- Car A: 60 km/h
- Car B: 62 km/h
- Speed diff: 2 km/h < threshold
- Fallback: Use rear position

**Example 3: Points Over Race**
```
Car A starts: 0 points
Lap 1: A hits B → A: -5, B: +5
Lap 2: B hits A → A: 0, B: 0
Lap 3: A hits C → A: -5, C: +5
Final: A: -5, B: 0, C: +5
```

---

## 🎨 Visualization Features

### Start/Finish Line
- Green line with checkered pattern
- Vertical or horizontal orientation
- "START/FINISH" label

### Car Display
Shows for each car:
- **Position** with trail
- **Lap counter**: "Lap 3/10"
- **Speed**: "S: 45.2 km/h (Avg: 42.8)"
- **Quality indicator** (signal strength)
- **Collision flash** (red circle when colliding)

### Leaderboard
Displays:
- Position (1st, 2nd, 3rd with colors)
- Car name
- Current lap
- Total time
- Points

### Collision Summary
Shows for each car:
- Total collisions
- Initiated vs Received
- Current points

---

## 📊 Data Logging

### Enable Logging

```python
# In race_config.py
ENABLE_RACE_LOGGING = True
RACE_LOG_FILE = 'race_data.csv'
```

### Log File Format

```csv
timestamp,car_id,car_name,event_type,lap,value,details
1234.56,0,TAG 0,lap_complete,1,12.5,
1235.12,0,TAG 0,collision,2,,with_car_1
1236.78,1,TAG 1,lap_complete,1,13.2,
```

### Event Types
- `lap_complete` - Lap finished
- `collision` - Collision detected
- `speed_update` - Speed recorded (optional)
- `position` - Position logged (optional)

---

## 🔧 Advanced Configuration

### Tuning Collision Detection

**Too Many False Positives?**
```python
COLLISION_DISTANCE_THRESHOLD = 40  # Increase threshold
COLLISION_TIME_WINDOW = 2.0        # Longer window
```

**Missing Collisions?**
```python
COLLISION_DISTANCE_THRESHOLD = 20  # Decrease threshold
COLLISION_TIME_WINDOW = 0.5        # Shorter window
```

### Tuning Speed Calculation

**Too Noisy?**
```python
SPEED_AVERAGE_SAMPLES = 20  # More smoothing
SPEED_CALC_METHOD = 'average'  # Use average only
```

**Too Slow to React?**
```python
SPEED_AVERAGE_SAMPLES = 5  # Less smoothing
SPEED_CALC_METHOD = 'instantaneous'  # Instant only
```

### Tuning Lap Detection

**False Lap Triggers?**
```python
MIN_LAP_TIME = 5.0  # Longer minimum
LINE_CROSSING_THRESHOLD = 10  # Stricter crossing
```

**Missing Crossings?**
```python
LINE_CROSSING_THRESHOLD = 30  # More lenient
```

---

## 🐛 Troubleshooting

### Problem: Cars not starting race
**Solutions:**
1. Check start line position in config
2. Verify `START_LINE_ORIENTATION`
3. Enable debug: `PRINT_LAP_EVENTS = True`
4. Check cars are actually crossing the line

### Problem: No collisions detected
**Solutions:**
1. Check `COLLISION_DISTANCE_THRESHOLD`
2. Enable debug: `SHOW_COLLISION_CIRCLES = True`
3. Verify speed manager is linked
4. Check `PRINT_COLLISION_EVENTS = True`

### Problem: Wrong collision initiator
**Solutions:**
1. Try different method: `COLLISION_INITIATOR_METHOD = 'rear'`
2. Adjust speed threshold
3. Check speed calculations are working

### Problem: Inaccurate speeds
**Solutions:**
1. Verify position updates are frequent
2. Check Kalman filter settings
3. Increase `SPEED_AVERAGE_SAMPLES`
4. Verify anchor positions

---

## 📈 Performance Tips

### Optimize Update Rate
```python
# In race_config.py
SPEED_UPDATE_INTERVAL = 0.2  # Update less frequently

# In config.py
REFRESH_RATE = 0.05  # 20 FPS instead of 30
```

### Reduce Logging
```python
LOG_POSITIONS = False  # Don't log every position
PRINT_SPEED_UPDATES = False
PRINT_COLLISION_EVENTS = False
```

### Limit History
```python
# In config.py
TRAIL_LENGTH = 10  # Shorter trails
SPEED_AVERAGE_SAMPLES = 5  # Smaller buffer
```

---

## 🎯 Example Race Scenarios

### Scenario 1: Basic Race
```python
TOTAL_LAPS = 5
COLLISION_DISTANCE_THRESHOLD = 30
COLLISION_INITIATOR_METHOD = 'speed'
```

### Scenario 2: Strict Collision Penalties
```python
COLLISION_POINTS_PENALTY = -10
COLLISION_POINTS_REWARD = 10
ENABLE_DISQUALIFICATION = True
MAX_COLLISIONS_BEFORE_DQ = 5
```

### Scenario 3: Time Trial (No Collisions)
```python
COLLISION_DISTANCE_THRESHOLD = 0  # Disable
ENABLE_RACE_LOGGING = True
LOG_LAP_TIMES = True
LOG_SPEEDS = True
```

---

## 📝 TODO / Future Enhancements

- [ ] Sector timing (split times)
- [ ] Pit stop detection
- [ ] Formation lap support
- [ ] Qualifying mode
- [ ] Real-time graphs
- [ ] Replay system
- [ ] Multi-race championship
- [ ] Weather/track conditions
- [ ] Tire wear simulation
- [ ] Fuel consumption
- [ ] DRS zones
- [ ] Safety car periods

---

## 🤝 Contributing

To add new features:
1. Add configuration to `race_config.py`
2. Implement logic in appropriate module
3. Update renderer in `race_renderer.py`
4. Integrate in `racing_main.py`
5. Update this documentation

---

## 📄 License

[Your License Here]

## 📧 Support

[Your Contact Information]
