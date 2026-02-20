#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════
UWB WebSocket Bridge - FULL SYSTEM (All-in-One)
═══════════════════════════════════════════════════════════════════════════
Includes:
  • UDP receiver (from hardware anchors)
  • Trilateration / weighted multilateration (positioning.py)
  • Kalman filter smoothing (kalman_filter.py)
  • Lap detection & race management (lap_engine.py)
  • ELP scoring engine (scoring_engine.py)
  • Car-to-car & wall collision detection (collision_engine.py)
  • Speed tracking (speed_tracker.py)
  • Oval track geometry (track_loader.py)
  • WebSocket broadcast to browser clients
═══════════════════════════════════════════════════════════════════════════
"""

import asyncio
import websockets
import socket
import json
import math
import time
import threading
import signal
import sys
from datetime import datetime
from collections import defaultdict, deque

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION  (mirrors config.py + race_config.py)
# ═══════════════════════════════════════════════════════════════════════════
UDP_PORT = 4210
WS_PORT  = 8001

# Anchor physical positions (cm)
ANCHOR_POSITIONS = {
    0: (0,   0),
    1: (200, 0),
    2: (200, 200),
    3: (0,   200),
}
ANCHOR_COUNT = 4
TAG_COUNT    = 3

# Kalman
KALMAN_PROCESS_NOISE     = 0.1
KALMAN_MEASUREMENT_NOISE = 5.0

# RSSI weighting
RSSI_EXCELLENT    = -60
RSSI_POOR         = -90
RSSI_MIN_WEIGHT   = 0.1
RSSI_NORMALIZATION = 30

# Quality thresholds
QUALITY_EXCELLENT_ANCHORS = 4
QUALITY_GOOD_ANCHORS      = 3

# Tag display
TRAIL_LENGTH = 30
TAG_TIMEOUT  = 5

# Race
TOTAL_LAPS          = 10
MIN_LAPS_TO_QUALIFY = 3
MIN_LAP_TIME        = 3.0        # cooldown seconds

# Start line (vertical, inside the 200×200 cm square)
START_LINE_X            = 100
START_LINE_Y1           = 30
START_LINE_Y2           = 70
START_LINE_ORIENTATION  = 'vertical'
LINE_CROSSING_THRESHOLD = 20     # cm

# Checkpoints  (empty = disabled)
CHECKPOINTS      = []
CHECKPOINT_RADIUS = 25

# ELP penalties / bonuses
WALL_HIT_PENALTY               = 5.0
CAR_COLLISION_ATTACKER_PENALTY  = 5.0
CAR_COLLISION_VICTIM_BONUS      = 2.0
CORNER_CUT_PENALTY              = 3.0
CORNER_CUT_VOID_LAP             = False
PIT_ZONE_MAX_SPEED_CM_S         = 30.0
PIT_ZONE_OVERSPEED_PENALTY      = 2.0

# Collision detection
CAR_COLLISION_DISTANCE_CM = 25
CAR_COLLISION_COOLDOWN    = 1.0
SPEED_DIFF_THRESHOLD      = 10.0
WALL_TOLERANCE_CM         = 5.0
WALL_COLLISION_COOLDOWN   = 0.5

# Ghost / anomaly
GHOSTING_SPEED_THRESHOLD  = 0.20
GHOSTING_TIME_THRESHOLD   = 3.0
MAX_PLAUSIBLE_SPEED_CM_S  = 278

# Speed
SPEED_AVERAGE_SAMPLES = 10
SPEED_DISPLAY_UNIT    = 'km/h'

# Print flags
PRINT_LAP_EVENTS       = True
PRINT_COLLISION_EVENTS = True
PRINT_WALL_EVENTS      = True
PRINT_ANOMALIES        = True

# ═══════════════════════════════════════════════════════════════════════════
# KALMAN FILTER
# ═══════════════════════════════════════════════════════════════════════════
class KalmanFilter:
    def __init__(self):
        self.x = self.y = 0.0
        self.prev_x = self.prev_y = 0.0
        self.vx = self.vy = 0.0
        self.initialized = False
        self.q = KALMAN_PROCESS_NOISE
        self.r = KALMAN_MEASUREMENT_NOISE

    def update(self, mx, my, dt=0.03):
        if not self.initialized:
            self.x, self.y = mx, my
            self.prev_x, self.prev_y = mx, my
            self.initialized = True
            return mx, my

        # Save previous for velocity
        prev_x, prev_y = self.x, self.y

        # Predict
        self.x += self.vx * dt
        self.y += self.vy * dt

        # Kalman gain
        k = self.r / (self.r + self.q)

        # Correct
        self.x = self.x + k * (mx - self.x)
        self.y = self.y + k * (my - self.y)

        # Velocity from corrected delta (fixed: use previous corrected position)
        if dt > 0:
            self.vx = (self.x - prev_x) / dt
            self.vy = (self.y - prev_y) / dt

        return self.x, self.y

    def get_speed(self):
        return math.hypot(self.vx, self.vy)

    def reset(self):
        self.x = self.y = self.vx = self.vy = 0.0
        self.initialized = False

# ═══════════════════════════════════════════════════════════════════════════
# POSITIONING  (trilateration + multilateration)
# ═══════════════════════════════════════════════════════════════════════════
class Positioning:
    @staticmethod
    def rssi_weight(rssi):
        if rssi >= 0:
            return 1.0
        normalized = (rssi + (RSSI_EXCELLENT + RSSI_POOR) / 2) / RSSI_NORMALIZATION
        return max(RSSI_MIN_WEIGHT, 1.0 + normalized)

    @staticmethod
    def get_valid_anchors(ranges, rssi_list, anchor_positions):
        valid = []
        for i, r in enumerate(ranges):
            if r > 0 and i in anchor_positions:
                rssi = rssi_list[i] if i < len(rssi_list) else 0
                w    = Positioning.rssi_weight(rssi)
                ax, ay = anchor_positions[i]
                valid.append({'id': i, 'range': r, 'rssi': rssi,
                              'weight': w, 'x': ax, 'y': ay})
        return valid

    @staticmethod
    def trilaterate_3(a1, a2, a3):
        x1,y1,r1 = a1['x'], a1['y'], a1['range']
        x2,y2,r2 = a2['x'], a2['y'], a2['range']
        x3,y3,r3 = a3['x'], a3['y'], a3['range']

        A = 2*(x2-x1);  B = 2*(y2-y1)
        C = r1**2 - r2**2 - x1**2 + x2**2 - y1**2 + y2**2
        D = 2*(x3-x2);  E = 2*(y3-y2)
        F = r2**2 - r3**2 - x2**2 + x3**2 - y2**2 + y3**2

        denom = A*E - B*D
        if abs(denom) < 0.001:
            # collinear fallback: two-circle midpoint
            d = math.hypot(x2-x1, y2-y1)
            if d == 0: return x1, y1
            ratio = r1 / (r1 + r2) if (r1+r2) > 0 else 0.5
            return x1+(x2-x1)*ratio, y1+(y2-y1)*ratio

        x = (C*E - F*B) / denom
        y = (A*F - C*D) / denom
        return x, y

    @staticmethod
    def weighted_multilateration(valid):
        if len(valid) < 3:
            return None
        combos = []
        n = len(valid)
        for i in range(n):
            for j in range(i+1, n):
                for k in range(j+1, n):
                    a1, a2, a3 = valid[i], valid[j], valid[k]
                    px, py = Positioning.trilaterate_3(a1, a2, a3)
                    w = (a1['weight'] + a2['weight'] + a3['weight']) / 3
                    combos.append((px, py, w))
        if not combos:
            return None
        tw = sum(c[2] for c in combos)
        if tw <= 0:
            return None
        x = sum(c[0]*c[2] for c in combos) / tw
        y = sum(c[1]*c[2] for c in combos) / tw
        return x, y

    @staticmethod
    def calculate(ranges, rssi_list, anchor_positions):
        valid = Positioning.get_valid_anchors(ranges, rssi_list, anchor_positions)
        if len(valid) >= QUALITY_EXCELLENT_ANCHORS:
            pos = Positioning.weighted_multilateration(valid)
            quality = 'excellent'
        elif len(valid) >= QUALITY_GOOD_ANCHORS:
            valid.sort(key=lambda a: a['weight'], reverse=True)
            pos = Positioning.trilaterate_3(valid[0], valid[1], valid[2])
            quality = 'good'
        elif len(valid) >= 2:
            a1, a2 = valid[0], valid[1]
            d = math.hypot(a2['x']-a1['x'], a2['y']-a1['y'])
            if d == 0:
                pos = (a1['x'], a1['y'])
            else:
                ratio = a1['range'] / (a1['range'] + a2['range'])
                pos = (a1['x']+(a2['x']-a1['x'])*ratio,
                       a1['y']+(a2['y']-a1['y'])*ratio)
            quality = 'fair'
        else:
            return None, 'poor', len(valid)

        # pos may be a tuple or None
        if pos is None:
            return None, quality, len(valid)
        px, py = pos if isinstance(pos, tuple) else (pos[0], pos[1])
        return (px, py), quality, len(valid)

# ═══════════════════════════════════════════════════════════════════════════
# TAG STATE  (position, kalman, trail, speed)
# ═══════════════════════════════════════════════════════════════════════════
class TagState:
    def __init__(self, tag_id):
        self.id          = tag_id
        self.name        = f"Car{tag_id}"
        self.x           = 0.0
        self.y           = 0.0
        self.raw_x       = 0.0
        self.raw_y       = 0.0
        self.status      = False
        self.last_update = 0.0
        self.quality     = 'unknown'
        self.anchor_count = 0
        self.kalman      = KalmanFilter()
        self.history     = deque(maxlen=TRAIL_LENGTH)
        self.update_count = 0

        # Speed tracking
        self._pos_buf    = deque(maxlen=SPEED_AVERAGE_SAMPLES)
        self.speed_cms   = 0.0
        self.max_speed   = 0.0

    def update_position(self, raw_x, raw_y, quality, anchor_count, now):
        dt = now - self.last_update if self.last_update else 0.033
        dt = max(0.001, min(dt, 1.0))   # clamp

        self.raw_x, self.raw_y = raw_x, raw_y
        self.x, self.y = self.kalman.update(raw_x, raw_y, dt)
        self.quality        = quality
        self.anchor_count   = anchor_count
        self.status         = True
        self.last_update    = now
        self.history.append((self.x, self.y, now))
        self.update_count  += 1

        # Speed
        self._pos_buf.append({'x': self.x, 'y': self.y, 't': now})
        if len(self._pos_buf) >= 2:
            p1, p2 = self._pos_buf[-2], self._pos_buf[-1]
            ddt = p2['t'] - p1['t']
            if ddt > 0:
                dist = math.hypot(p2['x']-p1['x'], p2['y']-p1['y'])
                self.speed_cms = dist / ddt
                self.max_speed = max(self.max_speed, self.speed_cms)

    def speed_display(self):
        if SPEED_DISPLAY_UNIT == 'km/h':
            return self.speed_cms * 0.036
        if SPEED_DISPLAY_UNIT == 'm/s':
            return self.speed_cms / 100
        return self.speed_cms

    def is_active(self):
        return self.status and (time.time() - self.last_update) < TAG_TIMEOUT

    def reset(self):
        self.kalman.reset()
        self.history.clear()
        self._pos_buf.clear()
        self.speed_cms  = 0.0
        self.max_speed  = 0.0
        self.status     = False
        self.update_count = 0

# ═══════════════════════════════════════════════════════════════════════════
# SCORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════
class LapScore:
    def __init__(self, car_id, car_name, lap_number):
        self.car_id      = car_id
        self.car_name    = car_name
        self.lap_number  = lap_number
        self.raw_time    = 0.0
        self.closed_at   = None
        self.wall_hits   = 0
        self.atk_hits    = 0
        self.vic_hits    = 0
        self.corner_cuts = 0
        self.overspeed   = False
        self.voided      = False
        self._pen        = 0.0
        self._bon        = 0.0

    def add_wall_hit(self):
        self.wall_hits += 1
        self._pen      += WALL_HIT_PENALTY
        if PRINT_WALL_EVENTS:
            print(f"  🚧 WALL  | {self.car_name} Lap {self.lap_number} +{WALL_HIT_PENALTY}s")

    def add_attacker_penalty(self):
        self.atk_hits += 1
        self._pen     += CAR_COLLISION_ATTACKER_PENALTY
        if PRINT_COLLISION_EVENTS:
            print(f"  🔴 ATK   | {self.car_name} Lap {self.lap_number} +{CAR_COLLISION_ATTACKER_PENALTY}s")

    def add_victim_bonus(self):
        self.vic_hits += 1
        self._bon     += CAR_COLLISION_VICTIM_BONUS
        if PRINT_COLLISION_EVENTS:
            print(f"  🟢 VIC   | {self.car_name} Lap {self.lap_number} -{CAR_COLLISION_VICTIM_BONUS}s")

    def add_corner_cut(self):
        self.corner_cuts += 1
        if CORNER_CUT_VOID_LAP:
            self.voided = True
            print(f"  ⛔ VOID  | {self.car_name} Lap {self.lap_number}")
        else:
            self._pen += CORNER_CUT_PENALTY
            print(f"  🔶 CUT   | {self.car_name} Lap {self.lap_number} +{CORNER_CUT_PENALTY}s")

    def add_overspeed(self):
        if not self.overspeed:
            self.overspeed = True
            self._pen     += PIT_ZONE_OVERSPEED_PENALTY
            print(f"  🚨 SPD   | {self.car_name} Lap {self.lap_number} +{PIT_ZONE_OVERSPEED_PENALTY}s")

    @property
    def elp(self):
        return float('inf') if self.voided else max(0.0, self.raw_time + self._pen - self._bon)

    def to_dict(self):
        return dict(car_id=self.car_id, car_name=self.car_name,
                    lap=self.lap_number, raw=round(self.raw_time, 3),
                    penalty=round(self._pen, 3), bonus=round(self._bon, 3),
                    elp=round(self.elp, 3), voided=self.voided)


class ScoringEngine:
    def __init__(self):
        self._history = defaultdict(list)
        self._open    = {}
        self._names   = {}
        self._feed    = []

    def register(self, car_id, car_name):
        self._names[car_id] = car_name

    def open_lap(self, car_id, lap_number):
        self._open[car_id] = LapScore(car_id, self._names.get(car_id, f"Car{car_id}"), lap_number)

    def close_lap(self, car_id, raw_time):
        lap = self._open.pop(car_id, None)
        if lap is None:
            lap = LapScore(car_id, self._names.get(car_id, f"Car{car_id}"), 0)
        lap.raw_time  = raw_time
        lap.closed_at = time.time()
        self._history[car_id].append(lap)
        msg = (f"📊 LAP | {lap.car_name} Lap {lap.lap_number} "
               f"raw={raw_time:.2f}s ELP={lap.elp:.2f}s")
        if PRINT_LAP_EVENTS:
            print(msg)
        self._feed.append(msg)
        return lap

    def current_lap(self, car_id):
        return self._open.get(car_id)

    def wall_hit(self, car_id):
        lap = self._open.get(car_id)
        if lap:
            lap.add_wall_hit()
            self._feed.append(f"🚧 WALL {self._names.get(car_id,'?')} Lap {lap.lap_number} +{WALL_HIT_PENALTY}s")

    def car_collision(self, attacker_id, victim_id):
        a = self._open.get(attacker_id)
        v = self._open.get(victim_id)
        if a: a.add_attacker_penalty()
        if v: v.add_victim_bonus()
        an = self._names.get(attacker_id, '?')
        vn = self._names.get(victim_id, '?')
        self._feed.append(f"💥 {an}→{vn} | +{CAR_COLLISION_ATTACKER_PENALTY}s / -{CAR_COLLISION_VICTIM_BONUS}s")

    def corner_cut(self, car_id):
        lap = self._open.get(car_id)
        if lap: lap.add_corner_cut()

    def overspeed(self, car_id):
        lap = self._open.get(car_id)
        if lap: lap.add_overspeed()

    def best_elp(self, car_id):
        valid = [l.elp for l in self._history.get(car_id, []) if not l.voided]
        return min(valid) if valid else float('inf')

    def laps_done(self, car_id):
        return len(self._history.get(car_id, []))

    def qualifies(self, car_id):
        return self.laps_done(car_id) >= MIN_LAPS_TO_QUALIFY

    def get_leaderboard(self):
        rows = []
        for cid, laps in self._history.items():
            valid = [l for l in laps if not l.voided]
            if not valid: continue
            best = min(valid, key=lambda l: (l.elp, l.closed_at or 0))
            rows.append(dict(
                car_id=cid, car_name=self._names.get(cid, f"Car{cid}"),
                best_elp=round(best.elp, 3), best_raw=round(best.raw_time, 3),
                best_lap=best.lap_number, laps_done=len(laps),
                qualifies=self.qualifies(cid),
                penalty_total=round(sum(l._pen for l in laps), 2),
                bonus_total=round(sum(l._bon for l in laps), 2)
            ))
        rows.sort(key=lambda r: (r['best_elp'], r['best_lap']))
        return rows

    def get_car_summary(self, car_id):
        laps = self._history.get(car_id, [])
        op   = self._open.get(car_id)
        return dict(car_id=car_id,
                    car_name=self._names.get(car_id, f"Car{car_id}"),
                    laps_done=len(laps),
                    best_elp=self.best_elp(car_id),
                    qualifies=self.qualifies(car_id),
                    open_lap=op.to_dict() if op else None,
                    history=[l.to_dict() for l in laps])

    def get_feed(self, n=8):
        return self._feed[-n:]

    def reset(self):
        self._history.clear()
        self._open.clear()
        self._feed.clear()
        print("📊 Scoring engine reset")

# ═══════════════════════════════════════════════════════════════════════════
# TRACK GEOMETRY  (oval, inner+outer boundary)
# ═══════════════════════════════════════════════════════════════════════════
class Track:
    def __init__(self, outer_points, inner_points=None):
        self.name         = "Oval Track"
        self.outer_points = outer_points
        self.inner_points = inner_points or []

    def has_width(self):
        return len(self.inner_points) > 0

    def get_outer_points(self):
        return self.outer_points

    def get_inner_points(self):
        return self.inner_points


def create_oval_track(cx=100, cy=110, ow=85, oh=70, tw=30, n=40):
    outer, inner = [], []
    for i in range(n):
        angle = 2 * math.pi * i / n
        outer.append((cx + ow * math.cos(angle), cy + oh * math.sin(angle)))
        inner.append((cx + (ow-tw) * math.cos(angle), cy + (oh-tw) * math.sin(angle)))
    return Track(outer, inner)


def dist_to_boundary(px, py, pts):
    """Minimum distance from point to a closed polygon boundary."""
    if not pts or len(pts) < 2:
        return float('inf')
    best = float('inf')
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i+1) % n]
        dx, dy = x2-x1, y2-y1
        denom = dx*dx + dy*dy
        if denom == 0:
            d = math.hypot(px-x1, py-y1)
        else:
            t = max(0, min(1, ((px-x1)*dx + (py-y1)*dy) / denom))
            d = math.hypot(px - x1 - t*dx, py - y1 - t*dy)
        best = min(best, d)
    return best

# ═══════════════════════════════════════════════════════════════════════════
# LAP ENGINE  (per car)
# ═══════════════════════════════════════════════════════════════════════════
class LapEngine:
    def __init__(self, car_id, car_name, scoring):
        self.car_id         = car_id
        self.car_name       = car_name
        self.scoring        = scoring
        self.current_lap    = 0
        self.laps_done      = 0
        self.is_racing      = False
        self.race_finished  = False
        self.admin_armed    = False
        self._side          = None
        self._lap_start     = None
        self._last_cross    = 0.0
        self._checkpoints   = set()
        self._lap_times     = []
        self._all_cp        = list(range(len(CHECKPOINTS)))

    def arm(self):
        self.admin_armed = True
        print(f"🟢 ARM  | {self.car_name} – waiting for line crossing")

    def update(self, x, y, speed, now):
        if self.is_racing:
            self._check_checkpoints(x, y)
            self._check_pit_speed(x, y, speed)

        new_side = self._get_side(x, y)
        if self._side is None:
            self._side = new_side
            return None

        crossed   = (self._side != new_side)
        in_bounds = self._within_bounds(x, y)

        if crossed and in_bounds:
            self._side = new_side
            return self._handle_crossing(now, x, y)

        self._side = new_side
        return None

    def _handle_crossing(self, now, x, y):
        if now - self._last_cross < MIN_LAP_TIME:
            return None
        self._last_cross = now

        if not self.is_racing:
            if not self.admin_armed:
                return None
            self.is_racing   = True
            self.current_lap = 1
            self._lap_start  = now
            self._checkpoints.clear()
            self.scoring.open_lap(self.car_id, self.current_lap)
            if PRINT_LAP_EVENTS:
                print(f"🏁 START | {self.car_name} – Lap 1/{TOTAL_LAPS}")
            return dict(type='race_start', car_id=self.car_id,
                        car_name=self.car_name, lap=self.current_lap, time=now)

        raw = now - self._lap_start
        self._validate_checkpoints()
        lap_score = self.scoring.close_lap(self.car_id, raw)
        self._lap_times.append(raw)
        self.laps_done += 1

        event = dict(type='lap_done', car_id=self.car_id,
                     car_name=self.car_name, lap=self.current_lap,
                     raw_time=raw, elp=lap_score.elp, time=now)

        if self.laps_done >= TOTAL_LAPS:
            self.is_racing     = False
            self.race_finished = True
            if PRINT_LAP_EVENTS:
                print(f"🏆 FINISH | {self.car_name} – all {TOTAL_LAPS} laps done")
            event['type'] = 'race_finish'
            return event

        self.current_lap += 1
        self._lap_start   = now
        self._checkpoints.clear()
        self.scoring.open_lap(self.car_id, self.current_lap)
        if PRINT_LAP_EVENTS:
            print(f"🔄 LAP  | {self.car_name} – Lap {self.current_lap}/{TOTAL_LAPS}  "
                  f"raw={raw:.2f}s  ELP={lap_score.elp:.2f}s")
        return event

    def _get_side(self, x, y):
        if START_LINE_ORIENTATION == 'vertical':
            return x < START_LINE_X
        return y < START_LINE_Y1

    def _within_bounds(self, x, y):
        if START_LINE_ORIENTATION == 'vertical':
            return START_LINE_Y1 <= y <= START_LINE_Y2
        return (START_LINE_X - LINE_CROSSING_THRESHOLD <= x
                <= START_LINE_X + LINE_CROSSING_THRESHOLD)

    def _check_checkpoints(self, x, y):
        for idx, (cx, cy) in enumerate(CHECKPOINTS):
            if idx not in self._checkpoints:
                if math.hypot(x-cx, y-cy) <= CHECKPOINT_RADIUS:
                    self._checkpoints.add(idx)

    def _validate_checkpoints(self):
        for idx in self._all_cp:
            if idx not in self._checkpoints:
                self.scoring.corner_cut(self.car_id)

    def _check_pit_speed(self, x, y, speed):
        if self.current_lap == 1:
            near = (abs(x - START_LINE_X) < 50 and START_LINE_Y1 <= y <= START_LINE_Y2)
            if near and speed > PIT_ZONE_MAX_SPEED_CM_S:
                self.scoring.overspeed(self.car_id)

    def current_lap_elapsed(self, now):
        return (now - self._lap_start) if self._lap_start else 0.0

    def best_raw(self):
        return min(self._lap_times) if self._lap_times else 0.0

    def get_info(self, now=None):
        return dict(car_id=self.car_id, car_name=self.car_name,
                    current_lap=self.current_lap, total_laps=TOTAL_LAPS,
                    laps_done=self.laps_done, is_racing=self.is_racing,
                    race_finished=self.race_finished,
                    current_lap_elapsed=self.current_lap_elapsed(now or time.time()),
                    best_raw=self.best_raw(), lap_times=list(self._lap_times))

    def reset(self):
        self.current_lap=0; self.laps_done=0
        self.is_racing=False; self.race_finished=False; self.admin_armed=False
        self._side=None; self._lap_start=None; self._last_cross=0.0
        self._checkpoints.clear(); self._lap_times.clear()

# ═══════════════════════════════════════════════════════════════════════════
# RACE MANAGER  (all lap engines + scoring)
# ═══════════════════════════════════════════════════════════════════════════
class RaceManager:
    def __init__(self, scoring):
        self.scoring         = scoring
        self._engines        = {}
        self.race_active     = False
        self.race_start_time = None
        self.race_end_time   = None

    def register(self, car_id, car_name):
        self.scoring.register(car_id, car_name)
        self._engines[car_id] = LapEngine(car_id, car_name, self.scoring)

    def admin_start(self):
        for eng in self._engines.values():
            eng.arm()
        print("🟢 RACE ARMED – waiting for first line crossings")

    def update(self, car_id, x, y, speed, now):
        eng = self._engines.get(car_id)
        if not eng: return None
        event = eng.update(x, y, speed, now)
        if event:
            if event['type'] == 'race_start' and not self.race_active:
                self.race_active     = True
                self.race_start_time = now
                print("🏁 RACE IN PROGRESS")
            if event['type'] == 'race_finish':
                if all(e.race_finished for e in self._engines.values()):
                    self.race_active   = False
                    self.race_end_time = now
                    print(f"🏆 ALL FINISHED – {now - self.race_start_time:.2f}s total")
        return event

    def get_info(self, car_id, now=None):
        eng = self._engines.get(car_id)
        return eng.get_info(now) if eng else None

    def get_leaderboard(self):
        return self.scoring.get_leaderboard()

    def reset(self):
        for eng in self._engines.values():
            eng.reset()
        self.scoring.reset()
        self.race_active = False
        self.race_start_time = self.race_end_time = None
        print("🔄 Race reset – ready")

# ═══════════════════════════════════════════════════════════════════════════
# COLLISION ENGINE
# ═══════════════════════════════════════════════════════════════════════════
class CollisionEngine:
    def __init__(self, scoring, track):
        self.scoring   = scoring
        self.track     = track
        self._names    = {}
        self._pos      = {}
        self._speeds   = {}
        self._laps     = {}
        self._racing   = {}
        self._car_cd   = {}
        self._wall_cd  = {}
        self._ghost_t  = {}
        self._spd_buf  = []
        self.events    = []
        self.anomalies = []

    def register(self, car_id, car_name):
        self._names[car_id] = car_name

    def update(self, cars, now):
        new_evts = []

        for cid, d in cars.items():
            self._pos[cid]    = (d['x'], d['y'], now)
            self._speeds[cid] = d.get('speed', 0.0)
            self._laps[cid]   = d.get('lap', 0)
            self._racing[cid] = d.get('racing', False)
            spd = self._speeds[cid]
            if spd > 0:
                self._spd_buf.append(spd)
                if len(self._spd_buf) > 300:
                    self._spd_buf.pop(0)
            if spd > MAX_PLAUSIBLE_SPEED_CM_S:
                self._flag_anomaly(cid, spd, now)

        racing_ids = [c for c, d in cars.items() if d.get('racing', False)]
        for i in range(len(racing_ids)):
            for j in range(i+1, len(racing_ids)):
                e = self._check_car(racing_ids[i], racing_ids[j], now)
                if e: new_evts.append(e)

        for cid, d in cars.items():
            if not d.get('racing', False): continue
            e = self._check_wall(cid, d['x'], d['y'], d.get('lap', 0), now)
            if e: new_evts.append(e)

        self.events.extend(new_evts)
        return new_evts

    def _check_car(self, a, b, now):
        if self._is_ghost(a) or self._is_ghost(b): return None
        pa = self._pos.get(a); pb = self._pos.get(b)
        if not pa or not pb: return None

        dist = math.hypot(pa[0]-pb[0], pa[1]-pb[1])
        if dist > CAR_COLLISION_DISTANCE_CM: return None

        key = frozenset([a, b])
        if now - self._car_cd.get(key, 0) < CAR_COLLISION_COOLDOWN: return None
        self._car_cd[key] = now

        sa = self._speeds.get(a, 0); sb = self._speeds.get(b, 0)
        if abs(sa-sb) >= SPEED_DIFF_THRESHOLD:
            attacker, victim = (a,b) if sa>=sb else (b,a)
        else:
            attacker, victim = a, b

        self.scoring.car_collision(attacker, victim)
        an = self._names.get(attacker, f"Car{attacker}")
        vn = self._names.get(victim,   f"Car{victim}")
        lap = self._laps.get(attacker, 0)

        if PRINT_COLLISION_EVENTS:
            print(f"💥 CAR | {an} → {vn}  dist={dist:.1f}cm  Lap{lap}")

        return dict(type='car', attacker=attacker, victim=victim,
                    attacker_name=an, victim_name=vn, dist=dist, lap=lap, time=now)

    def _check_wall(self, cid, x, y, lap, now):
        if not self.track or not self.track.has_width(): return None
        if now - self._wall_cd.get(cid, 0) < WALL_COLLISION_COOLDOWN: return None

        od = dist_to_boundary(x, y, self.track.get_outer_points())
        id_ = dist_to_boundary(x, y, self.track.get_inner_points())

        wall = None
        if od <= WALL_TOLERANCE_CM:   wall = 'outer'
        elif id_ <= WALL_TOLERANCE_CM: wall = 'inner'
        if not wall: return None

        self._wall_cd[cid] = now
        self.scoring.wall_hit(cid)
        name = self._names.get(cid, f"Car{cid}")
        if PRINT_WALL_EVENTS:
            print(f"🚧 WALL | {name} hit {wall.upper()} wall  Lap{lap}")

        return dict(type='wall', car_id=cid, car_name=name,
                    wall=wall, lap=lap, time=now)

    def _is_ghost(self, cid):
        spd = self._speeds.get(cid, 0)
        avg = sum(self._spd_buf)/len(self._spd_buf) if self._spd_buf else 1
        thr = avg * GHOSTING_SPEED_THRESHOLD
        if spd < thr:
            if cid not in self._ghost_t:
                self._ghost_t[cid] = time.time()
            elif time.time() - self._ghost_t[cid] > GHOSTING_TIME_THRESHOLD:
                return True
        else:
            self._ghost_t.pop(cid, None)
        return False

    def _flag_anomaly(self, cid, spd, now):
        name = self._names.get(cid, f"Car{cid}")
        self.anomalies.append(dict(car_id=cid, name=name, speed=spd, time=now))
        if PRINT_ANOMALIES:
            print(f"⚠️  ANOMALY | {name} speed={spd:.0f}cm/s ({spd*0.036:.1f}km/h)")

    def wall_hits(self, cid):
        return [e for e in self.events if e['type']=='wall' and e['car_id']==cid]

    def car_events(self, cid):
        return [e for e in self.events if e['type']=='car'
                and (e['attacker']==cid or e['victim']==cid)]

    def reset(self):
        self.events.clear(); self.anomalies.clear()
        self._car_cd.clear(); self._wall_cd.clear()
        self._ghost_t.clear(); self._spd_buf.clear()
        print("✓ Collision engine reset")

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL SYSTEM STATE
# ═══════════════════════════════════════════════════════════════════════════
tags         = {i: TagState(i) for i in range(TAG_COUNT)}
anchors_info = ANCHOR_POSITIONS        # dict id -> (x,y)

scoring  = ScoringEngine()
race_mgr = RaceManager(scoring)
track    = create_oval_track()
col_eng  = CollisionEngine(scoring, track)

for tid, tag in tags.items():
    name = f"Car{tid}"
    tag.name = name
    race_mgr.register(tid, name)
    col_eng.register(tid, name)

# Bridge state
connected_clients = set()
event_loop        = None
running           = True

bridge_stats = {
    'udp_packets_total':   0,
    'udp_packets_valid':   0,
    'udp_packets_invalid': 0,
    'ws_messages_sent':    0,
    'ws_clients_total':    0,
    'tags_seen':           set(),
    'start_time':          datetime.now(),
}

race_armed    = False   # set True via WebSocket command "admin_start"

# ═══════════════════════════════════════════════════════════════════════════
# RACE UPDATE  (called each valid UDP packet)
# ═══════════════════════════════════════════════════════════════════════════
def process_race_update(tag_id, now):
    """Run lap/collision engines for one tag after position is updated."""
    tag = tags.get(tag_id)
    if not tag or not tag.is_active():
        return []

    events_out = []

    # Lap engine
    lap_event = race_mgr.update(tag_id, tag.x, tag.y, tag.speed_cms, now)
    if lap_event:
        events_out.append(lap_event)

    # Collision engine (needs all active tags)
    cars_data = {}
    for tid, t in tags.items():
        if t.is_active():
            li = race_mgr.get_info(tid, now)
            cars_data[tid] = dict(
                x=t.x, y=t.y,
                speed=t.speed_cms,
                lap=li['current_lap'] if li else 0,
                racing=li['is_racing'] if li else False,
            )
    if cars_data:
        col_events = col_eng.update(cars_data, now)
        events_out.extend(col_events)

    return events_out


def build_state_message(now):
    """Build full state JSON to broadcast to all WS clients."""
    cars = []
    for tid, tag in tags.items():
        if not tag.is_active():
            continue
        li = race_mgr.get_info(tid, now)
        sc = scoring.get_car_summary(tid)
        wh = len(col_eng.wall_hits(tid))
        ce = len(col_eng.car_events(tid))
        cars.append(dict(
            tag_id=tid,
            name=tag.name,
            x=round(tag.x, 1),
            y=round(tag.y, 1),
            raw_x=round(tag.raw_x, 1),
            raw_y=round(tag.raw_y, 1),
            speed=round(tag.speed_display(), 2),
            speed_unit=SPEED_DISPLAY_UNIT,
            speed_cms=round(tag.speed_cms, 1),
            quality=tag.quality,
            anchor_count=tag.anchor_count,
            trail=[(round(h[0],1), round(h[1],1)) for h in tag.history],
            lap_info=li,
            scoring=dict(
                best_elp=sc['best_elp'] if sc['best_elp'] < float('inf') else None,
                laps_done=sc['laps_done'],
                qualifies=sc['qualifies'],
                history=sc['history'],
            ),
            wall_hits=wh,
            car_collisions=ce,
        ))

    return json.dumps({
        "type":        "state_update",
        "timestamp":   now,
        "race_active": race_mgr.race_active,
        "race_armed":  race_armed,
        "cars":        cars,
        "leaderboard": race_mgr.get_leaderboard(),
        "feed":        scoring.get_feed(10),
    })

# ═══════════════════════════════════════════════════════════════════════════
# UDP RECEIVER
# ═══════════════════════════════════════════════════════════════════════════
def create_udp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT))
    sock.settimeout(0.1)
    return sock


def udp_receiver():
    global running
    sock = create_udp_socket()
    print(f"[UDP] ✓ Listening on port {UDP_PORT}")

    pkt = 0
    while running:
        try:
            data, addr = sock.recvfrom(2048)
            bridge_stats['udp_packets_total'] += 1
            pkt += 1

            message = data.decode('utf-8', errors='ignore').strip()

            try:
                uwb = json.loads(message)
            except json.JSONDecodeError:
                continue

            # Validate
            if 'id' not in uwb or 'range' not in uwb:
                bridge_stats['udp_packets_invalid'] += 1
                continue

            tag_id = int(uwb['id'])
            ranges = uwb['range']

            if not isinstance(ranges, list) or len(ranges) < ANCHOR_COUNT:
                bridge_stats['udp_packets_invalid'] += 1
                continue

            if tag_id not in tags:
                bridge_stats['udp_packets_invalid'] += 1
                continue

            rssi_list = uwb.get('rssi', [0]*len(ranges))
            now = time.time()

            # ── Position calculation ──────────────────────────
            # Use only first ANCHOR_COUNT ranges (hardware sends 8, we use 4)
            active_ranges = ranges[:ANCHOR_COUNT]
            pos, quality, anc_count = Positioning.calculate(
                active_ranges, rssi_list, anchors_info
            )

            if pos is None:
                bridge_stats['udp_packets_invalid'] += 1
                continue

            raw_x, raw_y = pos

            # ── Update tag state ─────────────────────────────
            tag = tags[tag_id]
            tag.update_position(raw_x, raw_y, quality, anc_count, now)

            bridge_stats['udp_packets_valid'] += 1
            bridge_stats['tags_seen'].add(tag_id)

            # ── Run race/collision logic ──────────────────────
            game_events = process_race_update(tag_id, now)

            # ── Build and broadcast WebSocket message ─────────
            if connected_clients and event_loop:
                # Per-packet position message
                pos_msg = json.dumps({
                    "type":      "tag_position",
                    "tag_id":    tag_id,
                    "x":         round(tag.x, 1),
                    "y":         round(tag.y, 1),
                    "raw_x":     round(raw_x, 1),
                    "raw_y":     round(raw_y, 1),
                    "range":     active_ranges,
                    "speed":     round(tag.speed_display(), 2),
                    "speed_cms": round(tag.speed_cms, 1),
                    "speed_unit": SPEED_DISPLAY_UNIT,
                    "quality":   quality,
                    "anchor_count": anc_count,
                    "timestamp": now,
                    "game_events": game_events,
                })
                asyncio.run_coroutine_threadsafe(
                    broadcast(pos_msg), event_loop
                )

                # If there were lap/collision events, also send full state
                if game_events:
                    state_msg = build_state_message(now)
                    asyncio.run_coroutine_threadsafe(
                        broadcast(state_msg), event_loop
                    )

            # Periodic log
            if pkt % 20 == 0:
                print(f"[UWB] Tag {tag_id}: ({tag.x:.0f},{tag.y:.0f})cm  "
                      f"qual={quality}  spd={tag.speed_display():.1f}{SPEED_DISPLAY_UNIT}  "
                      f"pkt#{bridge_stats['udp_packets_valid']}")

        except socket.timeout:
            continue
        except Exception as e:
            if running:
                print(f"[UDP] ✗ Error: {e}")

    sock.close()
    print("[UDP] ✓ Receiver stopped")

# ═══════════════════════════════════════════════════════════════════════════
# WEBSOCKET SERVER
# ═══════════════════════════════════════════════════════════════════════════
async def broadcast(message):
    if not connected_clients:
        return
    bridge_stats['ws_messages_sent'] += 1
    dead = set()
    for client in connected_clients:
        try:
            await client.send(message)
        except Exception:
            dead.add(client)
    connected_clients.difference_update(dead)


async def handle_client(websocket):
    global race_armed
    client_addr = websocket.remote_address
    client_id   = f"{client_addr[0]}:{client_addr[1]}"
    print(f"\n[WS] ✓ Client connected: {client_id}")

    connected_clients.add(websocket)
    bridge_stats['ws_clients_total'] += 1
    print(f"[WS] Active clients: {len(connected_clients)}")

    try:
        # Welcome message
        now = time.time()
        await websocket.send(json.dumps({
            "type":    "connection",
            "status":  "connected",
            "message": "Connected to UWB Full Racing System",
            "timestamp": now,
            "server_info": {
                "udp_port":       UDP_PORT,
                "ws_port":        WS_PORT,
                "anchor_count":   ANCHOR_COUNT,
                "tag_count":      TAG_COUNT,
                "total_laps":     TOTAL_LAPS,
                "uptime_seconds": (datetime.now() - bridge_stats['start_time']).total_seconds(),
            },
            "anchors": {str(k): {"x": v[0], "y": v[1]} for k, v in ANCHOR_POSITIONS.items()},
            "track": {
                "outer_points": track.get_outer_points(),
                "inner_points": track.get_inner_points(),
            },
            "stats": {
                "packets_received": bridge_stats['udp_packets_valid'],
                "tags_seen": sorted(list(bridge_stats['tags_seen'])),
            }
        }))

        # Send initial full state
        await websocket.send(build_state_message(now))

        # Handle commands from client
        async for message in websocket:
            try:
                data     = json.loads(message)
                msg_type = data.get('type')

                if msg_type == 'ping':
                    await websocket.send(json.dumps({
                        "type": "pong", "timestamp": time.time()
                    }))

                elif msg_type == 'admin_start':
                    race_mgr.admin_start()
                    race_armed = True
                    await broadcast(json.dumps({
                        "type": "admin_event",
                        "event": "race_armed",
                        "message": "Race armed – waiting for first crossing",
                        "timestamp": time.time(),
                    }))
                    print("[CMD] Admin Start issued")

                elif msg_type == 'reset':
                    race_mgr.reset()
                    col_eng.reset()
                    race_armed = False
                    for tag in tags.values():
                        tag.reset()
                    await broadcast(json.dumps({
                        "type": "admin_event",
                        "event": "race_reset",
                        "message": "Race reset",
                        "timestamp": time.time(),
                    }))
                    print("[CMD] Race reset issued")

                elif msg_type == 'get_stats':
                    uptime = (datetime.now() - bridge_stats['start_time']).total_seconds()
                    await websocket.send(json.dumps({
                        "type":               "stats",
                        "udp_packets_total":  bridge_stats['udp_packets_total'],
                        "udp_packets_valid":  bridge_stats['udp_packets_valid'],
                        "udp_packets_invalid":bridge_stats['udp_packets_invalid'],
                        "ws_messages_sent":   bridge_stats['ws_messages_sent'],
                        "ws_clients":         len(connected_clients),
                        "tags_seen":          sorted(list(bridge_stats['tags_seen'])),
                        "uptime_seconds":     uptime,
                        "leaderboard":        race_mgr.get_leaderboard(),
                        "feed":               scoring.get_feed(20),
                        "timestamp":          time.time(),
                    }))

                elif msg_type == 'get_state':
                    await websocket.send(build_state_message(time.time()))

                else:
                    print(f"[WS] Unknown command '{msg_type}' from {client_id}")

            except json.JSONDecodeError:
                print(f"[WS] ⚠ Invalid JSON from {client_id}")
            except Exception as e:
                print(f"[WS] ⚠ Handler error: {e}")

    except websockets.exceptions.ConnectionClosed:
        print(f"[WS] ✗ Client closed: {client_id}")
    except Exception as e:
        print(f"[WS] ✗ Client error: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"[WS] ✗ Disconnected: {client_id} | Active: {len(connected_clients)}")


async def stats_reporter():
    while running:
        await asyncio.sleep(60)
        if not running: break
        uptime = (datetime.now() - bridge_stats['start_time']).total_seconds()
        print(f"\n{'═'*70}")
        print(f"STATS – Uptime {uptime:.0f}s | "
              f"UDP {bridge_stats['udp_packets_valid']}/{bridge_stats['udp_packets_total']} valid | "
              f"WS msgs {bridge_stats['ws_messages_sent']} | "
              f"Clients {len(connected_clients)}")
        lb = race_mgr.get_leaderboard()
        if lb:
            print("Leaderboard:")
            for i, r in enumerate(lb):
                elp = f"{r['best_elp']:.2f}s" if r['best_elp'] < float('inf') else "—"
                print(f"  {i+1}. {r['car_name']:<8} ELP={elp}  Laps={r['laps_done']}")
        print(f"{'═'*70}\n")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    global event_loop, running
    event_loop = asyncio.get_event_loop()

    print(f"\n{'═'*70}")
    print(f"  UWB FULL RACING SYSTEM – WebSocket Bridge")
    print(f"{'═'*70}")
    print(f"  Started:        {bridge_stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  UDP Port:       {UDP_PORT}  (UWB hardware → here)")
    print(f"  WebSocket Port: {WS_PORT}   (browsers connect here)")
    print(f"  Tags:           {TAG_COUNT}   Anchors: {ANCHOR_COUNT}")
    print(f"  Track:          {track.name}  (inner+outer walls)")
    print(f"  Total Laps:     {TOTAL_LAPS}   Min qualify: {MIN_LAPS_TO_QUALIFY}")
    print(f"{'═'*70}")
    print(f"  WS Commands:  admin_start | reset | get_stats | get_state | ping")
    print(f"{'═'*70}\n")

    # UDP thread
    t = threading.Thread(target=udp_receiver, daemon=True, name="UDP-Receiver")
    t.start()

    asyncio.create_task(stats_reporter())

    try:
        async with websockets.serve(handle_client, "0.0.0.0", WS_PORT):
            print(f"[WS] ✓ ws://0.0.0.0:{WS_PORT}  ready")
            print(f"[WS] Local:  ws://localhost:{WS_PORT}")
            print(f"✓ READY\n")
            await asyncio.Future()
    except OSError as e:
        if e.errno in (48, 98):
            print(f"✗ Port {WS_PORT} already in use. Kill old process or change WS_PORT.")
        else:
            print(f"✗ Network error: {e}")
        running = False


def signal_handler(sig, frame):
    global running
    running = False
    uptime = (datetime.now() - bridge_stats['start_time']).total_seconds()
    print(f"\n\n{'═'*70}")
    print(f"SHUTDOWN – Runtime {uptime:.0f}s")
    print(f"UDP  valid/total: {bridge_stats['udp_packets_valid']}/{bridge_stats['udp_packets_total']}")
    print(f"WS messages sent: {bridge_stats['ws_messages_sent']}")
    lb = race_mgr.get_leaderboard()
    if lb:
        print("\nFinal Leaderboard:")
        for i, r in enumerate(lb):
            elp = f"{r['best_elp']:.2f}s" if r['best_elp'] < float('inf') else "—"
            print(f"  {i+1}. {r['car_name']}  BestELP={elp}  Laps={r['laps_done']}")
    print(f"{'═'*70}\n")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        signal_handler(None, None)
    except Exception as e:
        print(f"\n✗ FATAL: {e}")
        import traceback
        traceback.print_exc()                                          