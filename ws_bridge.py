#!/usr/bin/env python3
"""
ws_bridge.py  —  UWB Full Racing System  (Dynamic Track + Dynamic Penalties)
=============================================================================

CHANGES IN THIS VERSION
─────────────────────────
1. Track loaded dynamically from CSV sent in admin_start payload (track_csv field)
   CSV format:
     CENTER,       x, y
     INNER,        x, y
     OUTER,        x, y
     START_FINISH, x1, y1, x2, y2  [, direction]
     CHECKPOINT,   id, x, y, radius [, label]

2. Collision penalties are fully dynamic from tournament model fields:
     object_collision_time         → WALL_HIT_PENALTY
         car hits wall → attacker lap time +
     collision_creating_time       → CAR_COLLISION_ATTACKER_PENALTY
         tag A crashes tag B → A lap time +
     collision_absorbing_time      → CAR_COLLISION_VICTIM_BONUS
         tag B gets hit by A → B lap time -

3. No position filters (no Kalman, no OOB clamp, no RSSI weighting)

4. [FIX] Lap data now correctly saved to backend via record-lap API
5. [NEW] Per-car checkpoint progress tracked and broadcast
6. [NEW] Checkpoint touch history (which cars touched each CP) tracked
"""

import asyncio, websockets, socket, json, math, time, threading, signal, sys
import urllib.request, urllib.error
from datetime import datetime
from collections import defaultdict, deque

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════
UDP_PORT = 4210
WS_PORT  = 8001

DJANGO_API_BASE = 'https://xraceapi.zyberspace.in'
LAP_API_URL     = f'{DJANGO_API_BASE}/api/record-lap/'

ANCHOR_POSITIONS = {
    0: (0,    0),
    1: (610,  0),
    2: (610,  440),
    3: (0,    440),
}

ANCHOR_COUNT = 4
TAG_COUNT    = 6

MIN_RANGE_CM = 10
MAX_RANGE_CM = 1450

# ── Default race / penalty values (overridden by admin_start payload) ──
TOTAL_LAPS_DEFAULT                     = 10
TOTAL_LAPS                             = TOTAL_LAPS_DEFAULT
MIN_LAPS_TO_QUALIFY                    = 3
MIN_LAP_TIME                           = 3.0

WALL_HIT_PENALTY_DEFAULT               = 5.0    # object_collision_time
CAR_COLLISION_ATTACKER_PENALTY_DEFAULT = 5.0    # collision_creating_time
CAR_COLLISION_VICTIM_BONUS_DEFAULT     = 2.0    # collision_absorbing_time

WALL_HIT_PENALTY               = WALL_HIT_PENALTY_DEFAULT
CAR_COLLISION_ATTACKER_PENALTY = CAR_COLLISION_ATTACKER_PENALTY_DEFAULT
CAR_COLLISION_VICTIM_BONUS     = CAR_COLLISION_VICTIM_BONUS_DEFAULT

# ── Default start/finish (overridden by CSV) ──
START_LINE_X         = 490
START_LINE_Y1        = 300
START_LINE_Y2        = 340
LINE_CROSS_TOLERANCE = 8
LINE_Y_TOLERANCE     = 30
SF_CROSSING_DIR      = 'left_to_right'

# ── Default checkpoints (overridden by CSV) ──
CHECKPOINTS = [
    (390, 320, 22), (290, 325, 22), (190, 310, 22),
    ( 80, 290, 22), ( 55, 240, 22), ( 80, 185, 22),
    (160, 140, 22), (280, 100, 22), (420, 110, 22),
    (530, 165, 22), (555, 235, 22), (530, 295, 22),
]

tag_to_gp:        dict       = {}
current_group_id: int | None = None

CORNER_CUT_PENALTY      = 3.0
CORNER_CUT_VOID_LAP     = False
CAR_COLLISION_DISTANCE_CM  = 25
CAR_COLLISION_COOLDOWN     = 1.0
SPEED_DIFF_THRESHOLD       = 10.0
WALL_TOLERANCE_CM          = 5.0
WALL_COLLISION_COOLDOWN    = 0.5
MAX_PLAUSIBLE_SPEED_CM_S   = 2800
SPEED_AVERAGE_SAMPLES      = 2   # only last 2 points — no rolling average, pure raw delta
SPEED_DISPLAY_UNIT         = 'km/h'

PRINT_LAP_EVENTS       = True
PRINT_COLLISION_EVENTS = True
PRINT_WALL_EVENTS      = True
PRINT_ANOMALIES        = True

TRAIL_LENGTH = 30
TAG_TIMEOUT  = 5

# ── [NEW] Checkpoint touch history: cp_id → set of car_names that touched it ──
checkpoint_touch_history: dict = {}   # {cp_id: [{"car_id": .., "car_name": .., "lap": .., "time": ..}, ...]}


# ═══════════════════════════════════════════════════════════════════════
# TRACK CSV PARSER
# ═══════════════════════════════════════════════════════════════════════

class TrackData:
    """Holds geometry loaded from the tournament's track_layout_csv."""
    def __init__(self):
        self.center:      list = []
        self.inner:       list = []
        self.outer:       list = []
        self.checkpoints: list = []   # [(x, y, radius), ...] ordered by id
        self.sf_x:   float = START_LINE_X
        self.sf_y1:  float = START_LINE_Y1
        self.sf_y2:  float = START_LINE_Y2
        self.sf_dir: str   = SF_CROSSING_DIR

    def is_loaded(self) -> bool:
        return bool(self.center)

    def to_dict(self) -> dict:
        return dict(
            center=self.center,
            inner=self.inner,
            outer=self.outer,
            checkpoints=[
                {"id": i, "x": cp[0], "y": cp[1], "r": cp[2]}
                for i, cp in enumerate(self.checkpoints)
            ],
            start_finish=dict(
                x=self.sf_x, y1=self.sf_y1, y2=self.sf_y2, dir=self.sf_dir),
        )


def parse_track_csv(csv_text: str) -> TrackData:
    td = TrackData()
    cp_dict: dict = {}

    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            continue
        kind = parts[0].upper()
        try:
            if kind == 'CENTER' and len(parts) >= 3:
                td.center.append((float(parts[1]), float(parts[2])))
            elif kind == 'INNER' and len(parts) >= 3:
                td.inner.append((float(parts[1]), float(parts[2])))
            elif kind == 'OUTER' and len(parts) >= 3:
                td.outer.append((float(parts[1]), float(parts[2])))
            elif kind == 'START_FINISH' and len(parts) >= 5:
                x1, y1_sf = float(parts[1]), float(parts[2])
                x2, y2_sf = float(parts[3]), float(parts[4])
                td.sf_x  = (x1 + x2) / 2
                td.sf_y1 = min(y1_sf, y2_sf)
                td.sf_y2 = max(y1_sf, y2_sf)
                if len(parts) >= 6:
                    td.sf_dir = parts[5].lower().strip()
            elif kind == 'CHECKPOINT' and len(parts) >= 5:
                cp_id = int(parts[1])
                x, y, r = float(parts[2]), float(parts[3]), float(parts[4])
                cp_dict[cp_id] = (x, y, r)
        except (ValueError, IndexError) as e:
            print(f"[CSV] Parse warning on '{line}': {e}")
            continue

    if cp_dict:
        td.checkpoints = [cp_dict[k] for k in sorted(cp_dict.keys())]

    return td


def apply_track_data(td: TrackData):
    """Push parsed CSV data into global lap-engine constants."""
    global CHECKPOINTS, START_LINE_X, START_LINE_Y1, START_LINE_Y2, SF_CROSSING_DIR

    if td.checkpoints:
        CHECKPOINTS = list(td.checkpoints)
        print(f"[TRACK] {len(CHECKPOINTS)} checkpoints loaded from CSV")
    else:
        print("[TRACK] No checkpoints in CSV — keeping previous")

    START_LINE_X    = td.sf_x
    START_LINE_Y1   = td.sf_y1
    START_LINE_Y2   = td.sf_y2
    SF_CROSSING_DIR = td.sf_dir
    print(f"[TRACK] S/F  x={START_LINE_X}  y=[{START_LINE_Y1}..{START_LINE_Y2}]  dir={SF_CROSSING_DIR}")

    for eng in race_mgr._engines.values():
        eng.reset()
    print("[TRACK] All lap engines reset with new track data")


current_track = TrackData()


# ═══════════════════════════════════════════════════════════════════════
# ANCID-AWARE RANGE REORDERING
# ═══════════════════════════════════════════════════════════════════════

def reorder_by_ancid(slot_ranges, ancid, n=ANCHOR_COUNT):
    has_ancid = bool(ancid) and any(a >= 0 for a in ancid)
    if not has_ancid:
        return [float(r) for r in slot_ranges[:n]]
    out = [0.0] * n
    for slot, anc in enumerate(ancid):
        if 0 <= anc < n and slot < len(slot_ranges):
            out[anc] = float(slot_ranges[slot])
    return out


# ═══════════════════════════════════════════════════════════════════════
# DYNAMIC CONFIG  (collision penalties from tournament model)
# ═══════════════════════════════════════════════════════════════════════

def apply_race_config(cfg: dict, new_laps):
    global TOTAL_LAPS, WALL_HIT_PENALTY, CAR_COLLISION_ATTACKER_PENALTY, CAR_COLLISION_VICTIM_BONUS

    TOTAL_LAPS = new_laps if isinstance(new_laps, int) and new_laps > 0 else TOTAL_LAPS_DEFAULT

    w = cfg.get('object_collision_time')
    WALL_HIT_PENALTY = float(w) if w and float(w) > 0 else WALL_HIT_PENALTY_DEFAULT

    a = cfg.get('collision_creating_time')
    CAR_COLLISION_ATTACKER_PENALTY = float(a) if a and float(a) > 0 else CAR_COLLISION_ATTACKER_PENALTY_DEFAULT

    v = cfg.get('collision_absorbing_time')
    CAR_COLLISION_VICTIM_BONUS = float(v) if v and float(v) > 0 else CAR_COLLISION_VICTIM_BONUS_DEFAULT

    print(f"[CONFIG] laps={TOTAL_LAPS}  "
          f"wall(object_collision)={WALL_HIT_PENALTY}s  "
          f"attacker(creating)={CAR_COLLISION_ATTACKER_PENALTY}s  "
          f"victim_bonus(absorbing)={CAR_COLLISION_VICTIM_BONUS}s")


def reset_race_config():
    global TOTAL_LAPS, WALL_HIT_PENALTY, CAR_COLLISION_ATTACKER_PENALTY, CAR_COLLISION_VICTIM_BONUS
    TOTAL_LAPS                     = TOTAL_LAPS_DEFAULT
    WALL_HIT_PENALTY               = WALL_HIT_PENALTY_DEFAULT
    CAR_COLLISION_ATTACKER_PENALTY = CAR_COLLISION_ATTACKER_PENALTY_DEFAULT
    CAR_COLLISION_VICTIM_BONUS     = CAR_COLLISION_VICTIM_BONUS_DEFAULT


# ═══════════════════════════════════════════════════════════════════════
# API POSTER  — [FIX] ensure tag_to_gp lookup is robust
# ═══════════════════════════════════════════════════════════════════════

def post_lap_to_api(tag_id: int, lap):
    """
    POST lap data to Django /api/record-lap/
    FIX: tag_to_gp keys are stored as int; also try str fallback.
    """
    gp = tag_to_gp.get(int(tag_id)) or tag_to_gp.get(str(tag_id))
    if not gp:
        print(f"[API] SKIP tag={tag_id} — not in tag_to_gp map ({tag_to_gp})")
        return

    body = json.dumps({
        "gp_id":       gp,
        "lap_number":  lap.lap_number,
        "raw_time":    round(lap.raw_time, 3),
        "elp_time":    round(lap.elp, 3),
        "penalty":     round(lap._pen, 3),
        "bonus":       round(lap._bon, 3),
        "wall_hits":   lap.wall_hits,
        "atk_hits":    lap.atk_hits,
        "vic_hits":    lap.vic_hits,
        "corner_cuts": lap.corner_cuts,
        "voided":      lap.voided,
    }).encode()

    def _go():
        try:
            req = urllib.request.Request(
                LAP_API_URL, data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                resp_body = r.read().decode('utf-8', errors='replace')
                print(f"[API] ✓ tag={tag_id} gp={gp} lap={lap.lap_number} "
                      f"raw={lap.raw_time:.2f}s elp={lap.elp:.2f}s  resp={resp_body[:80]}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')
            print(f"[API] ✗ HTTP {e.code} tag={tag_id} lap={lap.lap_number}: {err_body[:200]}")
        except Exception as e:
            print(f"[API] ✗ error tag={tag_id} lap={lap.lap_number}: {e}")

    threading.Thread(target=_go, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════
# POSITIONING  (raw trilateration — no filters)
# ═══════════════════════════════════════════════════════════════════════

class Positioning:
    @staticmethod
    def valid_anchors(ranges, ap):
        out = []
        for i, r in enumerate(ranges):
            if r <= 0 or i not in ap: continue
            if r < MIN_RANGE_CM or r > MAX_RANGE_CM: continue
            out.append({'id': i, 'range': r, 'x': ap[i][0], 'y': ap[i][1]})
        return out

    @staticmethod
    def tri3(a1, a2, a3):
        x1,y1,r1 = a1['x'],a1['y'],a1['range']
        x2,y2,r2 = a2['x'],a2['y'],a2['range']
        x3,y3,r3 = a3['x'],a3['y'],a3['range']
        A=2*(x2-x1); B=2*(y2-y1)
        C=r1**2-r2**2-x1**2+x2**2-y1**2+y2**2
        D=2*(x3-x2); E=2*(y3-y2)
        F=r2**2-r3**2-x2**2+x3**2-y2**2+y3**2
        den = A*E - B*D
        if abs(den) < 0.001:
            d = math.hypot(x2-x1, y2-y1)
            ratio = r1/(r1+r2) if (r1+r2) > 0 else 0.5
            return x1+(x2-x1)*ratio, y1+(y2-y1)*ratio
        return (C*E-F*B)/den, (A*F-C*D)/den

    @staticmethod
    def multilat(va):
        if len(va) < 3: return None
        combos = []
        for i in range(len(va)):
            for j in range(i+1, len(va)):
                for k in range(j+1, len(va)):
                    px, py = Positioning.tri3(va[i], va[j], va[k])
                    combos.append((px, py))
        if not combos: return None
        return sum(c[0] for c in combos)/len(combos), sum(c[1] for c in combos)/len(combos)

    @staticmethod
    def calculate(ranges, ap):
        va = Positioning.valid_anchors(ranges, ap)
        nv = len(va)
        if nv >= 4:   pos = Positioning.multilat(va); q = 'excellent'
        elif nv == 3: pos = Positioning.tri3(*va[:3]); q = 'good'
        elif nv == 2:
            a1, a2 = va[0], va[1]
            ratio = a1['range']/(a1['range']+a2['range']) if (a1['range']+a2['range']) > 0 else 0.5
            pos = (a1['x']+(a2['x']-a1['x'])*ratio, a1['y']+(a2['y']-a1['y'])*ratio); q = 'fair'
        else: return None, 'poor', nv
        if pos is None: return None, q, nv
        return (pos[0], pos[1]), q, nv


# ═══════════════════════════════════════════════════════════════════════
# TAG STATE
# ═══════════════════════════════════════════════════════════════════════

class TagState:
    def __init__(self, tid):
        self.id = tid; self.name = f"Car{tid}"
        self.x = self.y = 0.0
        self.status = False; self.last_update = 0.0
        self.quality = 'unknown'; self.anchor_count = 0
        self.history = deque(maxlen=TRAIL_LENGTH)
        self.update_count = 0
        # Raw speed from last two packets only — no rolling average, no smoothing
        self._prev_x = self._prev_y = self._prev_t = None
        self.speed_cms = self.max_speed = 0.0
        self.pkt_total = self.pkt_accepted = self.pkt_rejected = 0
        self.last_ranges = [0]*ANCHOR_COUNT

    def update_position(self, rx, ry, quality, anc, now):
        # Speed = raw distance delta / time delta, no filtering
        if self._prev_t is not None:
            dt = now - self._prev_t
            if dt > 0:
                self.speed_cms = math.hypot(rx - self._prev_x, ry - self._prev_y) / dt
                self.max_speed = max(self.max_speed, self.speed_cms)
        self._prev_x, self._prev_y, self._prev_t = rx, ry, now
        # Store raw trilateration result exactly as-is
        self.x, self.y = rx, ry
        self.quality = quality; self.anchor_count = anc
        self.status = True; self.last_update = now
        self.history.append((self.x, self.y, now))
        self.update_count += 1; self.pkt_accepted += 1

    def speed_display(self):
        if SPEED_DISPLAY_UNIT == 'km/h': return self.speed_cms * 0.036
        if SPEED_DISPLAY_UNIT == 'm/s':  return self.speed_cms / 100
        return self.speed_cms

    def is_active(self): return self.status and (time.time() - self.last_update) < TAG_TIMEOUT

    def reset(self):
        self.history.clear()
        self._prev_x = self._prev_y = self._prev_t = None
        self.speed_cms = self.max_speed = 0.0; self.status = False
        self.update_count = self.pkt_total = self.pkt_accepted = self.pkt_rejected = 0
        self.last_ranges = [0]*ANCHOR_COUNT


# ═══════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════

class LapScore:
    def __init__(self, car_id, car_name, lap_number):
        self.car_id = car_id; self.car_name = car_name; self.lap_number = lap_number
        self.raw_time = 0.0; self.closed_at = None
        self.wall_hits = self.atk_hits = self.vic_hits = self.corner_cuts = 0
        self.overspeed = self.voided = False; self._pen = self._bon = 0.0

    def add_wall_hit(self):
        self._pen += WALL_HIT_PENALTY; self.wall_hits += 1
        if PRINT_WALL_EVENTS:
            print(f"  🚧 WALL  | {self.car_name} Lap {self.lap_number}  +{WALL_HIT_PENALTY}s  (object_collision)")

    def add_attacker_penalty(self):
        self._pen += CAR_COLLISION_ATTACKER_PENALTY; self.atk_hits += 1
        if PRINT_COLLISION_EVENTS:
            print(f"  🔴 ATK   | {self.car_name} Lap {self.lap_number}  +{CAR_COLLISION_ATTACKER_PENALTY}s  (collision_creating)")

    def add_victim_bonus(self):
        self._bon += CAR_COLLISION_VICTIM_BONUS; self.vic_hits += 1
        if PRINT_COLLISION_EVENTS:
            print(f"  🟢 VIC   | {self.car_name} Lap {self.lap_number}  -{CAR_COLLISION_VICTIM_BONUS}s  (collision_absorbing)")

    def add_corner_cut(self):
        self.corner_cuts += 1
        if CORNER_CUT_VOID_LAP: self.voided = True
        else: self._pen += CORNER_CUT_PENALTY

    @property
    def elp(self):
        return float('inf') if self.voided else max(0.0, self.raw_time + self._pen - self._bon)

    def to_dict(self):
        return dict(car_id=self.car_id, car_name=self.car_name, lap=self.lap_number,
                    raw=round(self.raw_time, 3), penalty=round(self._pen, 3),
                    bonus=round(self._bon, 3), elp=round(self.elp, 3), voided=self.voided)


class ScoringEngine:
    def __init__(self):
        self._history = defaultdict(list); self._open = {}; self._names = {}; self._feed = []

    def register(self, cid, name): self._names[cid] = name
    def open_lap(self, cid, n): self._open[cid] = LapScore(cid, self._names.get(cid, f"Car{cid}"), n)

    def close_lap(self, cid, raw):
        lap = self._open.pop(cid, None) or LapScore(cid, self._names.get(cid, f"Car{cid}"), 0)
        lap.raw_time = raw; lap.closed_at = time.time()
        self._history[cid].append(lap)
        msg = f"📊 LAP | {lap.car_name} Lap {lap.lap_number} raw={raw:.2f}s ELP={lap.elp:.2f}s"
        if PRINT_LAP_EVENTS: print(msg)
        self._feed.append(msg)
        # [FIX] Always attempt to post to API — robust lookup in post_lap_to_api
        post_lap_to_api(cid, lap)
        return lap

    def laps_done(self, cid): return len(self._history.get(cid, []))
    def qualifies(self, cid): return self.laps_done(cid) >= MIN_LAPS_TO_QUALIFY
    def best_elp(self, cid):
        v = [l.elp for l in self._history.get(cid, []) if not l.voided]
        return min(v) if v else float('inf')

    def wall_hit(self, cid):
        l = self._open.get(cid)
        if l: l.add_wall_hit(); self._feed.append(f"🚧 WALL {self._names.get(cid,'?')}")

    def car_collision(self, atk, vic):
        a = self._open.get(atk); v = self._open.get(vic)
        if a: a.add_attacker_penalty()
        if v: v.add_victim_bonus()
        self._feed.append(f"💥 {self._names.get(atk,'?')}>{self._names.get(vic,'?')}")

    def corner_cut(self, cid):
        l = self._open.get(cid)
        if l: l.add_corner_cut()

    def get_leaderboard(self):
        rows = []
        for cid, laps in self._history.items():
            valid = [l for l in laps if not l.voided]
            if not valid: continue
            best = min(valid, key=lambda l: (l.elp, l.closed_at or 0))
            rows.append(dict(car_id=cid, car_name=self._names.get(cid, f"Car{cid}"),
                             best_elp=round(best.elp, 3), best_raw=round(best.raw_time, 3),
                             best_lap=best.lap_number, laps_done=len(laps),
                             qualifies=self.qualifies(cid),
                             penalty_total=round(sum(l._pen for l in laps), 2),
                             bonus_total=round(sum(l._bon for l in laps), 2)))
        rows.sort(key=lambda r: (r['best_elp'], r['best_lap'])); return rows

    def get_car_summary(self, cid):
        laps = self._history.get(cid, []); op = self._open.get(cid)
        return dict(car_id=cid, car_name=self._names.get(cid, f"Car{cid}"),
                    laps_done=len(laps), best_elp=self.best_elp(cid),
                    qualifies=self.qualifies(cid),
                    open_lap=op.to_dict() if op else None,
                    history=[l.to_dict() for l in laps])

    def get_feed(self, n=8): return self._feed[-n:]

    def reset(self):
        self._history.clear(); self._open.clear(); self._feed.clear(); print("📊 Scoring reset")


# ═══════════════════════════════════════════════════════════════════════
# TRACK / COLLISION GEOMETRY
# ═══════════════════════════════════════════════════════════════════════

class Track:
    def __init__(self, outer, inner=None):
        self.outer = outer; self.inner = inner or []
    def has_width(self): return len(self.inner) > 0
    def get_outer_points(self): return self.outer
    def get_inner_points(self): return self.inner


def create_track_from_data(td: TrackData) -> Track:
    if td.inner and td.outer: return Track(td.outer, td.inner)
    return create_oval_track()


def create_oval_track(cx=305, cy=220, ow=260, oh=190, tw=30, n=40):
    o, i = [], []
    for k in range(n):
        a = 2*math.pi*k/n
        o.append((cx + ow*math.cos(a), cy + oh*math.sin(a)))
        i.append((cx + (ow-tw)*math.cos(a), cy + (oh-tw)*math.sin(a)))
    return Track(o, i)


def dist_to_boundary(px, py, pts):
    if not pts or len(pts) < 2: return float('inf')
    best = float('inf'); n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i+1)%n]
        dx, dy = x2-x1, y2-y1; den = dx*dx + dy*dy
        if den == 0: d = math.hypot(px-x1, py-y1)
        else:
            t = max(0, min(1, ((px-x1)*dx + (py-y1)*dy) / den))
            d = math.hypot(px-x1-t*dx, py-y1-t*dy)
        best = min(best, d)
    return best


# ═══════════════════════════════════════════════════════════════════════
# LAP ENGINE  — [NEW] track per-car CP hits + broadcast CP touch history
# ═══════════════════════════════════════════════════════════════════════

class LapEngine:
    def __init__(self, cid, name, sc):
        self.car_id = cid; self.car_name = name; self.scoring = sc
        self.current_lap = 0; self.laps_done = 0
        self.is_racing = False; self.race_finished = False; self.admin_armed = False
        self._lap_start = None; self._last_cross = 0.0; self._lap_times = []
        self._next_cp = 0; self._sf_side = None
        # [NEW] per-car: list of cp indices hit in current lap
        self.current_lap_cp_hits: list = []

    def arm(self):
        self.admin_armed = True
        print(f"🟢 ARM | {self.car_name}")

    def update(self, x, y, speed, now):
        cp_ev = self._check_checkpoints(x, y, now) if self.is_racing else None
        sf_ev = self._check_sf_line(x, y, now)
        return sf_ev or cp_ev

    def _on_line(self, y):
        return (START_LINE_Y1 - LINE_Y_TOLERANCE) <= y <= (START_LINE_Y2 + LINE_Y_TOLERANCE)

    def _check_sf_line(self, x, y, now):
        tol = LINE_CROSS_TOLERANCE
        if   x < START_LINE_X - tol: new_side = 'left'
        elif x > START_LINE_X + tol: new_side = 'right'
        else: return None

        if self._sf_side is None: self._sf_side = new_side; return None

        prev_side = self._sf_side; self._sf_side = new_side
        crossing  = ((prev_side=='right' and new_side=='left')  if SF_CROSSING_DIR=='right_to_left'
                     else (prev_side=='left' and new_side=='right'))

        if not crossing: return None
        if not self._on_line(y):
            print(f"[SF] {self.car_name} crossed but y={y:.0f} outside Y range — ignored"); return None
        if now - self._last_cross < MIN_LAP_TIME:
            print(f"[SF] {self.car_name} debounce — ignored"); return None

        print(f"[SF] ✓ {self.car_name} ({prev_side}→{new_side}) x={x:.0f} y={y:.0f}")
        self._last_cross = now
        return self._process_crossing(now)

    def _process_crossing(self, now):
        if not self.is_racing:
            self.is_racing = True; self.current_lap = 1
            self._lap_start = now; self._next_cp = 0
            self.current_lap_cp_hits = []
            self.scoring.open_lap(self.car_id, 1)
            print(f"🏁 START | {self.car_name} Lap 1/{TOTAL_LAPS}")
            return dict(type='race_start', car_id=self.car_id, car_name=self.car_name, lap=1, time=now)

        if self._next_cp < len(CHECKPOINTS):
            missing = len(CHECKPOINTS) - self._next_cp
            print(f"⚠ LAP VOID | {self.car_name} — {missing} CP(s) not hit (next: CP{self._next_cp})")
            self._next_cp = 0
            self.current_lap_cp_hits = []
            return None

        raw = now - self._lap_start
        ls  = self.scoring.close_lap(self.car_id, raw)
        self._lap_times.append(raw); self.laps_done += 1
        self._next_cp = 0
        self.current_lap_cp_hits = []
        ev = dict(type='lap_done', car_id=self.car_id, car_name=self.car_name,
                  lap=self.current_lap, raw_time=raw, elp=ls.elp, time=now)

        if self.laps_done >= TOTAL_LAPS:
            self.is_racing = False; self.race_finished = True
            if PRINT_LAP_EVENTS: print(f"🏆 FINISH | {self.car_name} ({self.laps_done} laps)")
            ev['type'] = 'race_finish'; return ev

        self.current_lap += 1; self._lap_start = now
        self.scoring.open_lap(self.car_id, self.current_lap)
        if PRINT_LAP_EVENTS:
            print(f"🔄 LAP | {self.car_name} Lap {self.current_lap}/{TOTAL_LAPS} raw={raw:.2f}s ELP={ls.elp:.2f}s")
        return ev

    def _check_checkpoints(self, x, y, now):
        """
        [UPDATED] Check next sequential CP. Also record touch in global checkpoint_touch_history.
        """
        if self._next_cp >= len(CHECKPOINTS): return None
        cx, cy, cr = CHECKPOINTS[self._next_cp]
        if math.hypot(x-cx, y-cy) <= cr:
            idx = self._next_cp
            print(f"  ✔ CP{idx} | {self.car_name} @ ({x:.0f},{y:.0f}) [{idx+1}/{len(CHECKPOINTS)}]")
            self._next_cp += 1
            self.current_lap_cp_hits.append(idx)

            # [NEW] Record in global touch history
            if idx not in checkpoint_touch_history:
                checkpoint_touch_history[idx] = []
            checkpoint_touch_history[idx].append({
                "car_id":   self.car_id,
                "car_name": self.car_name,
                "lap":      self.current_lap,
                "time":     now,
            })

            return dict(type='checkpoint', car_id=self.car_id, car_name=self.car_name,
                        cp_index=idx, total=len(CHECKPOINTS),
                        # [NEW] include who has touched this CP so far
                        cp_touches=checkpoint_touch_history.get(idx, []))
        return None

    def elapsed(self, now): return (now - self._lap_start) if self._lap_start else 0.0
    def best_raw(self): return min(self._lap_times) if self._lap_times else 0.0

    def get_info(self, now=None):
        return dict(car_id=self.car_id, car_name=self.car_name,
                    current_lap=self.current_lap, total_laps=TOTAL_LAPS,
                    laps_done=self.laps_done, is_racing=self.is_racing,
                    race_finished=self.race_finished,
                    current_lap_elapsed=self.elapsed(now or time.time()),
                    best_raw=self.best_raw(), lap_times=list(self._lap_times),
                    checkpoints_hit=self._next_cp, checkpoints_total=len(CHECKPOINTS),
                    # [NEW] per-car CP hit list for current lap
                    cp_hits_this_lap=list(self.current_lap_cp_hits))

    def reset(self):
        self.current_lap = 0; self.laps_done = 0
        self.is_racing = False; self.race_finished = False; self.admin_armed = False
        self._sf_side = None; self._lap_start = None; self._last_cross = 0.0
        self._lap_times.clear(); self._next_cp = 0
        self.current_lap_cp_hits = []


# ═══════════════════════════════════════════════════════════════════════
# RACE MANAGER
# ═══════════════════════════════════════════════════════════════════════

class RaceManager:
    def __init__(self, sc):
        self.scoring = sc; self._engines = {}
        self.race_active = False; self.race_start_time = self.race_end_time = None

    def register(self, cid, name):
        self.scoring.register(cid, name); self._engines[cid] = LapEngine(cid, name, self.scoring)

    def admin_start(self):
        for e in self._engines.values(): e.arm()
        print(f"🟢 RACE ARMED – {TOTAL_LAPS} laps")

    def update(self, cid, x, y, speed, now):
        eng = self._engines.get(cid)
        if not eng: return None
        ev = eng.update(x, y, speed, now)
        if ev:
            if ev['type'] == 'race_start' and not self.race_active:
                self.race_active = True; self.race_start_time = now; print("🏁 RACE IN PROGRESS")
            if ev['type'] == 'race_finish' and all(e.race_finished for e in self._engines.values()):
                self.race_active = False; self.race_end_time = now; print("🏆 ALL FINISHED")
        return ev

    def get_info(self, cid, now=None):
        e = self._engines.get(cid); return e.get_info(now) if e else None

    def get_leaderboard(self): return self.scoring.get_leaderboard()

    def reset(self):
        for e in self._engines.values(): e.reset()
        self.scoring.reset(); self.race_active = False
        self.race_start_time = self.race_end_time = None; print("🔄 Race reset")


# ═══════════════════════════════════════════════════════════════════════
# COLLISION ENGINE
# ═══════════════════════════════════════════════════════════════════════

class CollisionEngine:
    def __init__(self, sc, trk):
        self.scoring = sc; self.track = trk
        self._names = {}; self._pos = {}; self._speeds = {}
        self._laps = {}; self._racing = {}
        self._car_cd = {}; self._wall_cd = {}
        self.events = []; self.anomalies = []

    def register(self, cid, name): self._names[cid] = name
    def set_track(self, trk): self.track = trk

    def update(self, cars, now):
        evts = []
        for cid, d in cars.items():
            self._pos[cid] = (d['x'], d['y'], now)
            self._speeds[cid] = d.get('speed', 0.0)
            self._laps[cid] = d.get('lap', 0); self._racing[cid] = d.get('racing', False)
            spd = self._speeds[cid]
            if spd > MAX_PLAUSIBLE_SPEED_CM_S: self._anomaly(cid, spd, now)

        racing = [c for c, d in cars.items() if d.get('racing', False)]
        for i in range(len(racing)):
            for j in range(i+1, len(racing)):
                e = self._car(racing[i], racing[j], now)
                if e: evts.append(e)
        for cid, d in cars.items():
            if not d.get('racing', False): continue
            e = self._wall(cid, d['x'], d['y'], d.get('lap', 0), now)
            if e: evts.append(e)
        self.events.extend(evts); return evts

    def _car(self, a, b, now):
        pa = self._pos.get(a); pb = self._pos.get(b)
        if not pa or not pb: return None
        dist = math.hypot(pa[0]-pb[0], pa[1]-pb[1])
        if dist > CAR_COLLISION_DISTANCE_CM: return None
        key = frozenset([a, b])
        if now - self._car_cd.get(key, 0) < CAR_COLLISION_COOLDOWN: return None
        self._car_cd[key] = now
        sa = self._speeds.get(a, 0); sb = self._speeds.get(b, 0)
        atk, vic = ((a, b) if abs(sa-sb) >= SPEED_DIFF_THRESHOLD and sa >= sb
                    else ((b, a) if abs(sa-sb) >= SPEED_DIFF_THRESHOLD else (a, b)))
        self.scoring.car_collision(atk, vic)
        an = self._names.get(atk, f"Car{atk}"); vn = self._names.get(vic, f"Car{vic}")
        if PRINT_COLLISION_EVENTS:
            print(f"💥 CAR | {an}→{vn} dist={dist:.1f}cm  "
                  f"atk+{CAR_COLLISION_ATTACKER_PENALTY}s / vic-{CAR_COLLISION_VICTIM_BONUS}s")
        return dict(type='car', attacker=atk, victim=vic,
                    attacker_name=an, victim_name=vn, dist=dist,
                    lap=self._laps.get(atk, 0), time=now)

    def _wall(self, cid, x, y, lap, now):
        if not self.track or not self.track.has_width(): return None
        if now - self._wall_cd.get(cid, 0) < WALL_COLLISION_COOLDOWN: return None
        od = dist_to_boundary(x, y, self.track.get_outer_points())
        id_ = dist_to_boundary(x, y, self.track.get_inner_points())
        wall = ('outer' if od <= WALL_TOLERANCE_CM else ('inner' if id_ <= WALL_TOLERANCE_CM else None))
        if not wall: return None
        self._wall_cd[cid] = now; self.scoring.wall_hit(cid)
        name = self._names.get(cid, f"Car{cid}")
        if PRINT_WALL_EVENTS: print(f"🚧 WALL | {name} {wall} Lap{lap}  +{WALL_HIT_PENALTY}s")
        return dict(type='wall', car_id=cid, car_name=name, wall=wall, lap=lap, time=now)

    def _anomaly(self, cid, spd, now):
        n = self._names.get(cid, f"Car{cid}")
        self.anomalies.append(dict(car_id=cid, name=n, speed=spd, time=now))
        if PRINT_ANOMALIES: print(f"⚠️ ANOMALY | {n} speed={spd:.0f}cm/s")

    def wall_hits(self, cid): return [e for e in self.events if e['type']=='wall' and e['car_id']==cid]
    def car_events(self, cid): return [e for e in self.events if e['type']=='car' and (e['attacker']==cid or e['victim']==cid)]

    def reset(self):
        self.events.clear(); self.anomalies.clear()
        self._car_cd.clear(); self._wall_cd.clear()
        print("✓ Collision reset")


# ═══════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════
tags     = {i: TagState(i) for i in range(TAG_COUNT)}
scoring  = ScoringEngine()
race_mgr = RaceManager(scoring)
track    = create_oval_track()
col_eng  = CollisionEngine(scoring, track)

for tid, tag in tags.items():
    race_mgr.register(tid, tag.name)
    col_eng.register(tid, tag.name)

connected_clients = set()
event_loop        = None
running           = True
race_armed        = False

stats = {'udp_total':0,'udp_valid':0,'udp_invalid':0,'ws_sent':0,'ws_clients':0,
         'tags_seen':set(),'start':datetime.now()}


# ═══════════════════════════════════════════════════════════════════════
# RACE UPDATE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def process_race_update(tid, now):
    tag = tags.get(tid)
    if not tag or not tag.is_active(): return []
    evts = []
    ev = race_mgr.update(tid, tag.x, tag.y, tag.speed_cms, now)
    if ev: evts.append(ev)
    cars = {}
    for t_id, t in tags.items():
        if t.is_active():
            li = race_mgr.get_info(t_id, now)
            cars[t_id] = dict(x=t.x, y=t.y, speed=t.speed_cms,
                               lap=li['current_lap'] if li else 0,
                               racing=li['is_racing'] if li else False)
    if cars: evts.extend(col_eng.update(cars, now))
    return evts


def build_state(now):
    """
    [UPDATED] Include cp_hits_this_lap per car and checkpoint_touch_history globally.
    """
    cars = []
    for tid, tag in tags.items():
        if not tag.is_active(): continue
        li = race_mgr.get_info(tid, now); sc = scoring.get_car_summary(tid)
        cars.append(dict(
            tag_id=tid, name=tag.name, x=round(tag.x,1), y=round(tag.y,1),
            speed=round(tag.speed_display(),2), speed_unit=SPEED_DISPLAY_UNIT,
            speed_cms=round(tag.speed_cms,1), quality=tag.quality,
            anchor_count=tag.anchor_count, last_ranges=tag.last_ranges,
            trail=[(round(h[0],1),round(h[1],1)) for h in tag.history],
            lap_info=li,
            scoring=dict(best_elp=sc['best_elp'] if sc['best_elp']<float('inf') else None,
                         laps_done=sc['laps_done'], qualifies=sc['qualifies'],
                         history=sc['history']),
            wall_hits=len(col_eng.wall_hits(tid)),
            car_collisions=len(col_eng.car_events(tid)),
            pkt_accepted=tag.pkt_accepted, pkt_rejected=tag.pkt_rejected))
    return json.dumps(dict(
        type="state_update", timestamp=now,
        race_active=race_mgr.race_active, race_armed=race_armed,
        total_laps=TOTAL_LAPS, group_id=current_group_id,
        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
        track=current_track.to_dict(),
        cars=cars, leaderboard=race_mgr.get_leaderboard(),
        feed=scoring.get_feed(10),
        # [NEW] global CP touch history for the frontend panel
        checkpoint_touches=_serialize_cp_touches()))


def _serialize_cp_touches() -> dict:
    """
    Returns {cp_id: [{"car_id":..,"car_name":..,"lap":..}, ...], ...}
    Serialisable, no timestamps (too much data).
    """
    result = {}
    for cp_id, touches in checkpoint_touch_history.items():
        result[str(cp_id)] = [
            {"car_id": t["car_id"], "car_name": t["car_name"], "lap": t["lap"]}
            for t in touches
        ]
    return result


# ═══════════════════════════════════════════════════════════════════════
# UDP RECEIVER
# ═══════════════════════════════════════════════════════════════════════

def udp_receiver():
    global running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT)); sock.settimeout(0.1)
    print(f"[UDP] Listening on port {UDP_PORT}")

    while running:
        try:
            data, addr = sock.recvfrom(2048)
            stats['udp_total'] += 1
            try: uwb = json.loads(data.decode('utf-8', errors='ignore').strip())
            except: stats['udp_invalid'] += 1; continue

            if 'id' not in uwb or 'range' not in uwb: stats['udp_invalid'] += 1; continue
            tid = int(uwb['id'])
            if tid not in tags: stats['udp_invalid'] += 1; continue
            slot_ranges = uwb['range']
            if not isinstance(slot_ranges, list) or len(slot_ranges) < ANCHOR_COUNT:
                stats['udp_invalid'] += 1; continue

            ancid      = uwb.get('ancid', [])
            raw_ranges = reorder_by_ancid(slot_ranges, ancid, ANCHOR_COUNT)
            now = time.time(); tag = tags[tid]; tag.pkt_total += 1

            pos, quality, anc_count = Positioning.calculate(raw_ranges, ANCHOR_POSITIONS)
            if pos is None: tag.pkt_rejected += 1; stats['udp_invalid'] += 1; continue

            rx, ry = pos
            tag.update_position(rx, ry, quality, anc_count, now)
            tag.last_ranges = [int(r) for r in raw_ranges]
            stats['udp_valid'] += 1; stats['tags_seen'].add(tid)

            print(f"[UWB] Tag{tid}  ({rx:.0f},{ry:.0f})  "
                  f"ranges={[int(r) for r in raw_ranges]}  {quality}  "
                  f"{tag.speed_display():.1f}{SPEED_DISPLAY_UNIT}")

            game_evts = process_race_update(tid, now)

            if connected_clients and event_loop:
                li = race_mgr.get_info(tid, now); open_lap = scoring._open.get(tid)
                msg = json.dumps(dict(
                    type="tag_position", tag_id=tid,
                    x=round(tag.x,1), y=round(tag.y,1), range=raw_ranges,
                    speed=round(tag.speed_display(),2), speed_cms=round(tag.speed_cms,1),
                    speed_unit=SPEED_DISPLAY_UNIT, quality=quality, anchor_count=anc_count,
                    timestamp=now, game_events=game_evts,
                    wall_hits=len(col_eng.wall_hits(tid)),
                    car_collisions=len(col_eng.car_events(tid)),
                    current_penalty=round(open_lap._pen,2) if open_lap else 0.0,
                    current_bonus=round(open_lap._bon,2) if open_lap else 0.0,
                    lap_info=li,
                    # [NEW] send latest CP touch map on every tag_position message
                    checkpoint_touches=_serialize_cp_touches()))
                asyncio.run_coroutine_threadsafe(broadcast(msg), event_loop)
                if game_evts:
                    asyncio.run_coroutine_threadsafe(broadcast(build_state(now)), event_loop)

        except socket.timeout: continue
        except Exception as e:
            if running: print(f"[UDP] Error: {e}")
    sock.close(); print("[UDP] Stopped")


# ═══════════════════════════════════════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════════════════════════════════════

async def broadcast(msg):
    if not connected_clients: return
    stats['ws_sent'] += 1; dead = set()
    for c in connected_clients:
        try: await c.send(msg)
        except: dead.add(c)
    connected_clients.difference_update(dead)


async def handle_client(ws):
    global race_armed, TOTAL_LAPS, tag_to_gp, current_group_id, current_track, track
    cid = f"{ws.remote_address[0]}:{ws.remote_address[1]}"
    print(f"[WS] Connected: {cid}")
    connected_clients.add(ws); stats['ws_clients'] += 1

    try:
        now = time.time()
        await ws.send(json.dumps(dict(
            type="connection", status="connected",
            message="UWB Racing — Dynamic Track + Penalties",
            timestamp=now,
            server_info=dict(udp_port=UDP_PORT, ws_port=WS_PORT,
                             anchor_count=ANCHOR_COUNT, tag_count=TAG_COUNT,
                             total_laps=TOTAL_LAPS,
                             uptime_seconds=(datetime.now()-stats['start']).total_seconds()),
            anchors={str(k):{"x":v[0],"y":v[1]} for k,v in ANCHOR_POSITIONS.items()},
            track=current_track.to_dict(),
            stats=dict(packets_received=stats['udp_valid'],
                       tags_seen=sorted(list(stats['tags_seen']))))))
        await ws.send(build_state(now))

        async for message in ws:
            try:
                d = json.loads(message); mt = d.get('type')

                if mt == 'ping':
                    await ws.send(json.dumps({"type":"pong","timestamp":time.time()}))

                elif mt == 'admin_start':
                    apply_race_config(d.get('race_config', {}), d.get('total_laps'))

                    nm = d.get('tag_map', {})
                    if nm:
                        # [FIX] store both int and str keys for robust lookup
                        tag_to_gp = {}
                        for k, v in nm.items():
                            tag_to_gp[int(k)] = int(v)
                            tag_to_gp[str(k)] = int(v)
                        print(f"[MAP] tag_to_gp = {tag_to_gp}")
                    current_group_id = d.get('group_id')

                    track_csv = d.get('track_csv', '')
                    if track_csv:
                        td = parse_track_csv(track_csv)
                        if td.is_loaded():
                            current_track = td
                            track = create_track_from_data(td)
                            col_eng.set_track(track)
                            apply_track_data(td)
                            print(f"[TRACK] ✓ center={len(td.center)} inner={len(td.inner)} "
                                  f"outer={len(td.outer)} cp={len(td.checkpoints)}")
                        else:
                            print("[TRACK] CSV parsed but no CENTER points — using current defaults")
                    else:
                        print("[TRACK] No track_csv — using current/default track")

                    # Reset CP touch history on new race
                    checkpoint_touch_history.clear()

                    race_mgr.reset(); race_mgr.admin_start(); race_armed = True
                    await broadcast(json.dumps(dict(
                        type="admin_event", event="race_armed",
                        message=f"Race armed – {TOTAL_LAPS} laps",
                        total_laps=TOTAL_LAPS, group_id=current_group_id,
                        track=current_track.to_dict(),
                        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
                        timestamp=time.time())))
                    print(f"[CMD] Start  group={current_group_id}  laps={TOTAL_LAPS}  "
                          f"wall={WALL_HIT_PENALTY}s  atk={CAR_COLLISION_ATTACKER_PENALTY}s  "
                          f"vic_bonus={CAR_COLLISION_VICTIM_BONUS}s")

                elif mt == 'reset':
                    race_mgr.reset(); col_eng.reset(); race_armed = False
                    tag_to_gp = {}; current_group_id = None
                    checkpoint_touch_history.clear()
                    for t in tags.values(): t.reset()
                    reset_race_config()
                    await broadcast(json.dumps(dict(type="admin_event", event="race_reset",
                        message="Race reset", timestamp=time.time())))
                    print("[CMD] Reset")

                elif mt == 'get_stats':
                    uptime = (datetime.now()-stats['start']).total_seconds()
                    ts = {t_id: dict(total=t.pkt_total, accepted=t.pkt_accepted,
                                     rejected=t.pkt_rejected,
                                     accept_pct=round(t.pkt_accepted/t.pkt_total*100,1),
                                     last_ranges=t.last_ranges)
                          for t_id, t in tags.items() if t.pkt_total > 0}
                    await ws.send(json.dumps(dict(
                        type="stats", udp_total=stats['udp_total'],
                        udp_valid=stats['udp_valid'], udp_invalid=stats['udp_invalid'],
                        ws_sent=stats['ws_sent'], ws_clients=len(connected_clients),
                        tags_seen=sorted(list(stats['tags_seen'])), tag_stats=ts,
                        uptime_seconds=uptime, total_laps=TOTAL_LAPS,
                        group_id=current_group_id, tag_to_gp=tag_to_gp,
                        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
                        leaderboard=race_mgr.get_leaderboard(),
                        feed=scoring.get_feed(20), timestamp=time.time())))

                elif mt == 'get_state':
                    await ws.send(build_state(time.time()))

                else:
                    print(f"[WS] Unknown cmd '{mt}' from {cid}")

            except json.JSONDecodeError: print(f"[WS] Bad JSON from {cid}")
            except Exception as e: print(f"[WS] Handler error: {e}")

    except websockets.exceptions.ConnectionClosed: pass
    except Exception as e: print(f"[WS] Client error: {e}")
    finally:
        connected_clients.discard(ws)
        print(f"[WS] Disconnected: {cid} | active={len(connected_clients)}")


async def stats_reporter():
    while running:
        await asyncio.sleep(60)
        if not running: break
        up = (datetime.now()-stats['start']).total_seconds()
        tot = stats['udp_total']; val = stats['udp_valid']
        print(f"\n{'═'*60}\nSTATS  uptime={up:.0f}s  UDP {val}/{tot} ({val/tot*100 if tot else 0:.0f}%)")
        for tid, t in tags.items():
            if t.pkt_total > 0:
                print(f"  Tag{tid}: {t.pkt_accepted}/{t.pkt_total} ({t.pkt_accepted/t.pkt_total*100:.0f}%)")
        for i, r in enumerate(race_mgr.get_leaderboard()):
            elp = f"{r['best_elp']:.2f}s" if r['best_elp'] < float('inf') else "—"
            print(f"  {i+1}. {r['car_name']:<8} ELP={elp} Laps={r['laps_done']}")
        print(f"{'═'*60}\n")


async def main():
    global event_loop, running
    event_loop = asyncio.get_event_loop()
    print(f"\n{'═'*60}")
    print(f"  UWB RACING — Dynamic Track + Dynamic Penalties")
    print(f"  UDP={UDP_PORT}  WS={WS_PORT}")
    print(f"  Track: loaded from tournament.track.track_layout_csv via admin_start")
    print(f"  Penalties: from tournament model collision fields")
    print(f"{'═'*60}\n")
    threading.Thread(target=udp_receiver, daemon=True, name="UDP").start()
    asyncio.create_task(stats_reporter())
    try:
        async with websockets.serve(handle_client, "0.0.0.0", WS_PORT):
            print(f"[WS] ws://0.0.0.0:{WS_PORT}  ready\n✓ READY\n")
            await asyncio.Future()
    except OSError as e:
        print(f"✗ Port error: {e}"); running = False


def signal_handler(sig, frame):
    global running; running = False
    up = (datetime.now()-stats['start']).total_seconds()
    tot = stats['udp_total']; val = stats['udp_valid']
    print(f"\n{'═'*60}\nSHUTDOWN  uptime={up:.0f}s"
          + (f"  UDP {val}/{tot} ({val/tot*100:.0f}%)" if tot else ""))
    for i, r in enumerate(race_mgr.get_leaderboard()):
        elp = f"{r['best_elp']:.2f}s" if r['best_elp'] < float('inf') else "—"
        print(f"  {i+1}. {r['car_name']}  ELP={elp}  Laps={r['laps_done']}")
    print(f"{'═'*60}\n"); sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try: asyncio.run(main())
    except KeyboardInterrupt: signal_handler(None, None)
    except Exception as e:
        print(f"\n✗ FATAL: {e}")
        import traceback; traceback.print_exc()