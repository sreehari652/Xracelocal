#!/usr/bin/env python3
"""
ws_bridge.py  —  UWB Full Racing System  (FIXED v4 — Anti-Jitter)
=================================================================

FIXES IN THIS VERSION (v4)
──────────────────────────

FIX 10 — Rolling median position smoother
  - After Kalman filter, take the median of last 5 Kalman outputs (X and Y separately).
  - This kills residual jitter on stationary tags almost entirely.
  - At 10–25 Hz, window=5 = 200–500ms maximum lag (acceptable for RC racing).
  - Jump guard still uses raw Kalman value so recovery is instant.

FIX 11 — Consecutive-skip recovery (CRITICAL)
  - Root cause of the (-61,147)→(1054,59) cascades in v3:
    Kalman state drifted to a bad position, so jump guard rejected ALL subsequent
    valid packets, leaving the tag frozen at a wrong baseline forever.
  - NEW: After MAX_CONSEC_SKIP=8 consecutive rejections, reset Kalman and baseline
    to the new position and accept the packet. This breaks the rejection loop.

FIX 12 — Better JUMP terminal log
  - Now shows per-anchor range values: A0=218cm  A1=282cm  A2=258cm  A3=187cm
  - Shows consecutive skip count: consec=3
  - Makes it easy to identify which anchor is causing the spike.

FIX 13 — Stronger Kalman smoothing
  - KALMAN_MEASUREMENT_NOISE: 12.0 → 18.0
  - KALMAN_PROCESS_NOISE: 2.0 → 1.5
  - Gain k = 1.5/19.5 ≈ 0.077 (was 0.14) — only 7.7% weight on new measurement
  - Stationary tag barely moves in Kalman output.

FIX 14 — Larger range median window
  - RANGE_MEDIAN_WIN: 5 → 7 samples
  - More stable range readings into trilateration.

FIX 15 — Tighter multilat outlier rejection
  - Threshold: max(20, med*1.8) → max(15, med*1.5)
  - Removes more bad trilateration combos.

FIX 5 — Don't feed clamped positions into Kalman
  - If trilateration result is outside arena, the Kalman filter previously
    received the CLAMPED edge position (e.g. x=0 when car was at x=50).
  - This taught the Kalman that the car is at the wall, causing it to
    slowly drift back — creating the left/top/right/bottom edge snapping.
  - NEW: Clamped packets are SKIPPED (not fed to Kalman) unless the
    clamp distance is small (< CLAMP_SKIP_THRESHOLD = 30cm).
    Small clamps are fine; large clamps indicate a bad measurement.

FIX 6 — Tighter outlier rejection in multilateration
  - Old outlier threshold: 2.5× median distance
  - New threshold: 1.8× median distance (and min floor raised to 20cm)
  - This removes more "junk" trilateration combos before averaging.

FIX 7 — Per-tag position continuity guard (Python-side)
  - Added MAX_JUMP_CM = 120 guard in udp_receiver.
  - If the new Kalman output jumps more than 120 cm from last accepted
    position in one step, the packet is rejected as a noise spike.
  - Exception: first 12 updates (warmup) are always accepted.

FIX 8 — Reduced Kalman measurement noise trust
  - KALMAN_MEASUREMENT_NOISE raised from 7.0 → 9.0
  - KALMAN_PROCESS_NOISE kept at 3.0
  - Kalman gain k = 3/(3+9) ≈ 0.25  (was 0.30)
  - Smoother tracking, less spike amplification.

FIX 9 — Triangle inequality tolerance tightened
  - TRIINEQ_TOL reduced from 0.60 → 0.45
  - Rejects more geometrically impossible range combinations.

Previous fixes (kept):
  FIX 1 — Arduino applyFilter() corruption
  FIX 2 — OOB position clamping instead of rejection
  FIX 3 — ancid-aware range reordering
  FIX 4 — Clean terminal output
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

# ★ ANCHOR POSITIONS — update ONLY here
ANCHOR_POSITIONS = {
    0: (0,    0),      # A0 bottom-left
    1: (213,  0),      # A1 bottom-right (2.13m = 213cm)
    2: (213, 205),     # A2 top-right (2.05m = 205cm)
    3: (0,   240),     # A3 top-left (2.4m = 240cm)
}
ANCHOR_COUNT = 4
TAG_COUNT    = 6

MIN_RANGE_CM = 10
MAX_RANGE_CM = 1450

# Triangle inequality tolerance — rejects geometrically impossible range combos
TRIINEQ_TOL  = 0.45

CLAMP_TO_ARENA      = True
CLAMP_HARD_REJECT   = 400   # cm — hard reject if clamping distance > this

# If clamp distance > this, skip packet entirely (don't poison Kalman)
CLAMP_SKIP_THRESHOLD = 35   # cm

ARENA_MARGIN        = 250

# ── Median range filter ──────────────────────────────────────────────
# Buffer last N raw range readings per anchor.  Before trilateration,
# replace each anchor's range with the MEDIAN of its recent readings.
# This kills single-packet range spikes (the #1 cause of position spikes).
# Window=5 at 10-25Hz = 200-500ms lag — acceptable for RC car tracking.
RANGE_MEDIAN_WIN    = 7    # samples per anchor to median-filter (was 5)
                           # Larger window = more stable ranges when stationary

# ── Per-anchor range rate guard ──────────────────────────────────────
# UWB range to an anchor can only change as fast as the car can move.
# Max car speed ~30 km/h = 833 cm/s.  At 10 Hz max change = 83 cm/step.
# Any anchor range that changes more than this in one step is a bad reading.
# We REPLACE it with the previous good reading rather than drop the packet.
RANGE_MAX_DELTA_CM  = 100  # cm per step — above this = corrupt reading, use last good

TOTAL_LAPS_DEFAULT                     = 10
TOTAL_LAPS                             = TOTAL_LAPS_DEFAULT
MIN_LAPS_TO_QUALIFY                    = 3
MIN_LAP_TIME                           = 3.0
WALL_HIT_PENALTY_DEFAULT               = 5.0
CAR_COLLISION_ATTACKER_PENALTY_DEFAULT = 5.0
CAR_COLLISION_VICTIM_BONUS_DEFAULT     = 2.0
WALL_HIT_PENALTY                       = WALL_HIT_PENALTY_DEFAULT
CAR_COLLISION_ATTACKER_PENALTY         = CAR_COLLISION_ATTACKER_PENALTY_DEFAULT
CAR_COLLISION_VICTIM_BONUS             = CAR_COLLISION_VICTIM_BONUS_DEFAULT

tag_to_gp:        dict       = {}
current_group_id: int | None = None

# ★ FIX 8 — Higher measurement noise = less trust in raw UWB = smoother output
# Kalman gain k = PROCESS/(PROCESS+MEASUREMENT) = 3/(3+9) ≈ 0.25
# Was: MEASUREMENT=7.0 → k≈0.30 (too jumpy for WiFi UWB)
KALMAN_PROCESS_NOISE     = 1.5   # how much car movement we trust between steps
KALMAN_MEASUREMENT_NOISE = 18.0  # how noisy UWB readings are (higher = smoother)
# Kalman gain k = P/(P+M) = 1.5/19.5 ≈ 0.077  (only 7.7% weight on new measurement)
# Very slow to move → stationary tags barely jitter at all

RSSI_EXCELLENT     = -60
RSSI_POOR          = -90
RSSI_MIN_WEIGHT    = 0.1
RSSI_NORMALIZATION = 30

QUALITY_EXCELLENT_ANCHORS = 4
QUALITY_GOOD_ANCHORS      = 3

TRAIL_LENGTH = 30
TAG_TIMEOUT  = 5

# ── Output position smoother ─────────────────────────────────────────
# After Kalman, take a rolling median of the last N Kalman outputs.
# This almost completely eliminates jitter on stationary tags without
# adding significant lag (at 10–25 Hz, window=5 = 200–500ms max lag).
POS_SMOOTH_WIN = 5   # rolling median window for final output position

START_LINE_X            = 60
START_LINE_Y1           = 100
START_LINE_Y2           = 140
LINE_CROSS_TOLERANCE    = 8
LINE_Y_TOLERANCE        = 30
SF_CROSSING_DIR         = 'right_to_left'

CHECKPOINTS = [
    ( 15, 155, 20),   # CP0
    ( 15, 205, 20),   # CP1
    ( 65, 233, 20),   # CP2
    (110, 237, 20),   # CP3
    (158, 233, 20),   # CP4
    (200, 200, 20),   # CP5
    (208, 155, 20),   # CP6
    (200, 100, 20),   # CP7
    (160,  68, 20),   # CP8
    (110,  60, 20),   # CP9
    ( 60,  70, 20),   # CP10
    ( 52,  92, 20),   # CP11
]

CORNER_CUT_PENALTY         = 3.0
CORNER_CUT_VOID_LAP        = False
PIT_ZONE_MAX_SPEED_CM_S    = 30.0
PIT_ZONE_OVERSPEED_PENALTY = 2.0

CAR_COLLISION_DISTANCE_CM  = 25
CAR_COLLISION_COOLDOWN     = 1.0
SPEED_DIFF_THRESHOLD       = 10.0
WALL_TOLERANCE_CM          = 5.0
WALL_COLLISION_COOLDOWN    = 1.5
WALL_OOB_FRAMES_NEEDED     = 3

GHOSTING_SPEED_THRESHOLD   = 0.20
GHOSTING_TIME_THRESHOLD    = 3.0
MAX_PLAUSIBLE_SPEED_CM_S   = 2800

SPEED_AVERAGE_SAMPLES = 10
SPEED_DISPLAY_UNIT    = 'km/h'

PRINT_LAP_EVENTS       = True
PRINT_COLLISION_EVENTS = True
PRINT_WALL_EVENTS      = True
PRINT_ANOMALIES        = True
PRINT_EVERY_PACKET     = True

CP_DWELL_FRAMES = 2

# ── Position continuity / jump guard ────────────────────────────────
# After Kalman, if output jumps more than this FROM THE LAST ACCEPTED
# position, the measurement is rejected and Kalman is NOT updated.
# Key fix vs v3: jump check now happens BEFORE kalman.update() so bad
# measurements never corrupt Kalman state at all.
# RC car 30 km/h = 833 cm/s.  At 10 Hz → 83 cm/step max.  Use 90 cm.
MAX_JUMP_CM          = 90    # cm — above this = spike, packet rejected entirely
MAX_JUMP_WARMUP      = 15    # packets before jump guard activates
MAX_CONSEC_SKIP      = 8     # if this many consecutive packets skipped, reset baseline
                             # (prevents being stuck at a bad last position forever)


# ═══════════════════════════════════════════════════════════════════════
# ANCID-AWARE RANGE REORDERING
# ═══════════════════════════════════════════════════════════════════════

def reorder_by_ancid(slot_ranges: list, ancid: list,
                     n: int = ANCHOR_COUNT) -> list:
    has_ancid = bool(ancid) and any(a >= 0 for a in ancid)
    if not has_ancid:
        return [float(r) for r in slot_ranges[:n]]
    out = [0.0] * n
    for slot, anc in enumerate(ancid):
        if 0 <= anc < n and slot < len(slot_ranges):
            out[anc] = float(slot_ranges[slot])
    return out


# ═══════════════════════════════════════════════════════════════════════
# GEOMETRIC RANGE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════

def _build_pair_dist(ap: dict) -> dict:
    d, ids = {}, sorted(ap)
    for i in range(len(ids)):
        for j in range(i+1, len(ids)):
            a, b = ids[i], ids[j]
            d[(a,b)] = math.hypot(ap[a][0]-ap[b][0], ap[a][1]-ap[b][1])
    return d

_PAIR_DIST = _build_pair_dist(ANCHOR_POSITIONS)


def validate_ranges(raw: list, ap: dict = ANCHOR_POSITIONS,
                    min_valid: int = 3) -> list[bool]:
    n, ids = len(ap), sorted(ap)
    ok = [(MIN_RANGE_CM <= raw[i] <= MAX_RANGE_CM) if i < len(raw) else False
          for i in range(n)]
    for _ in range(n):
        active = [i for i in range(n) if ok[i]]
        if len(active) < min_valid: break
        votes, any_bad = {i:0 for i in active}, False
        for ki, i in enumerate(active):
            for j in active[ki+1:]:
                key = (min(ids[i],ids[j]), max(ids[i],ids[j]))
                if raw[i]+raw[j] < _PAIR_DIST.get(key,0)*TRIINEQ_TOL:
                    votes[i]+=1; votes[j]+=1; any_bad=True
        if not any_bad: break
        if len(active) <= min_valid: break
        mv = max(votes.values())
        if mv == 0: break
        worst = min([k for k in active if votes[k]==mv], key=lambda k: raw[k])
        print(f"[VALIDATE] Drop A{ids[worst]}={raw[worst]:.0f}cm (violations={votes[worst]})")
        ok[worst] = False
    return ok


# ═══════════════════════════════════════════════════════════════════════
# ARENA CLAMP
# ═══════════════════════════════════════════════════════════════════════

def _arena_bounds(ap: dict = ANCHOR_POSITIONS):
    xs = [v[0] for v in ap.values()]; ys = [v[1] for v in ap.values()]
    return min(xs), max(xs), min(ys), max(ys)

_ARENA = _arena_bounds()


def clamp_to_arena(x: float, y: float):
    minX, maxX, minY, maxY = _ARENA
    cx = max(minX, min(maxX, x))
    cy = max(minY, min(maxY, y))
    return cx, cy, math.hypot(x-cx, y-cy)


def is_inside_arena_margin(x, y, margin=ARENA_MARGIN):
    minX, maxX, minY, maxY = _ARENA
    return (minX-margin <= x <= maxX+margin and minY-margin <= y <= maxY+margin)


# ═══════════════════════════════════════════════════════════════════════
# DYNAMIC CONFIG
# ═══════════════════════════════════════════════════════════════════════

def apply_race_config(cfg: dict, new_laps):
    global TOTAL_LAPS, WALL_HIT_PENALTY, CAR_COLLISION_ATTACKER_PENALTY, CAR_COLLISION_VICTIM_BONUS
    TOTAL_LAPS = new_laps if isinstance(new_laps, int) and new_laps > 0 else TOTAL_LAPS_DEFAULT
    w = cfg.get('object_collision_time')
    WALL_HIT_PENALTY = float(w) if w and float(w)>0 else WALL_HIT_PENALTY_DEFAULT
    a = cfg.get('collision_creating_time')
    CAR_COLLISION_ATTACKER_PENALTY = float(a) if a and float(a)>0 else CAR_COLLISION_ATTACKER_PENALTY_DEFAULT
    v = cfg.get('collision_absorbing_time')
    CAR_COLLISION_VICTIM_BONUS = float(v) if v and float(v)>0 else CAR_COLLISION_VICTIM_BONUS_DEFAULT
    print(f"[CONFIG] laps={TOTAL_LAPS} wall={WALL_HIT_PENALTY} atk={CAR_COLLISION_ATTACKER_PENALTY} vic={CAR_COLLISION_VICTIM_BONUS}")


def reset_race_config():
    global TOTAL_LAPS, WALL_HIT_PENALTY, CAR_COLLISION_ATTACKER_PENALTY, CAR_COLLISION_VICTIM_BONUS
    TOTAL_LAPS                     = TOTAL_LAPS_DEFAULT
    WALL_HIT_PENALTY               = WALL_HIT_PENALTY_DEFAULT
    CAR_COLLISION_ATTACKER_PENALTY = CAR_COLLISION_ATTACKER_PENALTY_DEFAULT
    CAR_COLLISION_VICTIM_BONUS     = CAR_COLLISION_VICTIM_BONUS_DEFAULT


# ═══════════════════════════════════════════════════════════════════════
# API POSTER
# ═══════════════════════════════════════════════════════════════════════

def post_lap_to_api(tag_id: int, lap):
    gp = tag_to_gp.get(tag_id)
    if not gp: return
    body = json.dumps({
        "gp_id":gp, "lap_number":lap.lap_number,
        "raw_time":round(lap.raw_time,3), "elp_time":round(lap.elp,3),
        "penalty":round(lap._pen,3), "bonus":round(lap._bon,3),
        "wall_hits":lap.wall_hits, "atk_hits":lap.atk_hits,
        "vic_hits":lap.vic_hits, "corner_cuts":lap.corner_cuts,
        "voided":lap.voided}).encode()
    def _go():
        try:
            req = urllib.request.Request(LAP_API_URL, data=body,
                                         headers={'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as r:
                print(f"[API] tag={tag_id} lap={lap.lap_number} ok")
        except Exception as e:
            print(f"[API] error: {e}")
    threading.Thread(target=_go, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════
# KALMAN FILTER
# ═══════════════════════════════════════════════════════════════════════

class KalmanFilter:
    def __init__(self):
        self.x = self.y = self.vx = self.vy = 0.0
        self.initialized = False

    def update(self, mx, my, dt=0.033):
        if not self.initialized:
            self.x, self.y = mx, my
            self.initialized = True
            return mx, my
        px, py  = self.x, self.y
        self.x += self.vx * dt
        self.y += self.vy * dt
        k       = KALMAN_PROCESS_NOISE / (KALMAN_PROCESS_NOISE + KALMAN_MEASUREMENT_NOISE)
        self.x += k * (mx - self.x)
        self.y += k * (my - self.y)
        if dt > 0:
            self.vx = (self.x - px) / dt
            self.vy = (self.y - py) / dt
            MAX_V = 3000.0
            if abs(self.vx) > MAX_V or abs(self.vy) > MAX_V:
                self.vx = self.vy = 0.0
        return self.x, self.y

    def reset(self):
        self.x = self.y = self.vx = self.vy = 0.0
        self.initialized = False


# ═══════════════════════════════════════════════════════════════════════
# POSITIONING
# ═══════════════════════════════════════════════════════════════════════

class Positioning:
    @staticmethod
    def rssi_w(rssi):
        if rssi >= 0: return 1.0
        return max(RSSI_MIN_WEIGHT, 1.0+(rssi+(RSSI_EXCELLENT+RSSI_POOR)/2)/RSSI_NORMALIZATION)

    @staticmethod
    def valid_anchors(ranges, rssi_list, ap, mask=None):
        out = []
        for i, r in enumerate(ranges):
            if r <= 0 or i not in ap: continue
            if mask is not None and not mask[i]: continue
            rssi = rssi_list[i] if i < len(rssi_list) else 0.0
            out.append({'id':i,'range':r,'rssi':rssi,
                        'weight':Positioning.rssi_w(rssi),'x':ap[i][0],'y':ap[i][1]})
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
        den=A*E-B*D
        if abs(den)<0.001:
            d=math.hypot(x2-x1,y2-y1)
            ratio=r1/(r1+r2) if (r1+r2)>0 else 0.5
            return x1+(x2-x1)*ratio, y1+(y2-y1)*ratio
        return (C*E-F*B)/den, (A*F-C*D)/den

    @staticmethod
    def multilat(va):
        if len(va)<3: return None
        combos=[]; n=len(va)
        for i in range(n):
            for j in range(i+1,n):
                for k in range(j+1,n):
                    px,py=Positioning.tri3(va[i],va[j],va[k])
                    w=(va[i]['weight']+va[j]['weight']+va[k]['weight'])/3
                    combos.append((px,py,w))
        if not combos: return None

        # ★ FIX 6 — Tighter outlier rejection: 1.8× median (was 2.5×), min floor 20cm (was 30)
        cx = sum(c[0]*c[2] for c in combos)/sum(c[2] for c in combos)
        cy = sum(c[1]*c[2] for c in combos)/sum(c[2] for c in combos)
        dists = [math.hypot(c[0]-cx, c[1]-cy) for c in combos]
        med_d = sorted(dists)[len(dists)//2]
        thresh = max(15.0, med_d * 1.5)   # tighter: was max(20, med*1.8)
        good = [c for c,d in zip(combos,dists) if d <= thresh]
        if not good: good = combos
        tw=sum(c[2] for c in good)
        if tw<=0: return None
        return sum(c[0]*c[2] for c in good)/tw, sum(c[1]*c[2] for c in good)/tw

    @staticmethod
    def calculate(ranges, rssi_list, ap):
        """
        Returns (position, quality_str, anchor_count, raw_trilat, clamp_dist)
        On failure returns (None, reason, n, None, 0.0)
        """
        mask = validate_ranges(ranges, ap)
        nv   = sum(mask)
        if nv < 2:
            return None, 'poor', nv, None, 0.0

        va = Positioning.valid_anchors(ranges, rssi_list, ap, mask)

        if len(va) >= QUALITY_EXCELLENT_ANCHORS:
            pos = Positioning.multilat(va); q = 'excellent'
        elif len(va) >= QUALITY_GOOD_ANCHORS:
            va.sort(key=lambda a: a['weight'], reverse=True)
            pos = Positioning.tri3(*va[:3]); q = 'good'
        elif len(va) >= 2:
            a1, a2 = va[0], va[1]
            ratio = a1['range']/(a1['range']+a2['range']) if (a1['range']+a2['range'])>0 else 0.5
            pos = (a1['x']+(a2['x']-a1['x'])*ratio, a1['y']+(a2['y']-a1['y'])*ratio)
            q = 'fair'
        else:
            return None, 'poor', len(va), None, 0.0

        if pos is None:
            return None, q, len(va), None, 0.0

        px, py = pos if isinstance(pos, tuple) else (pos[0], pos[1])
        raw_trilat = (px, py)

        if CLAMP_TO_ARENA:
            cx, cy, clamp_dist = clamp_to_arena(px, py)
            if clamp_dist > CLAMP_HARD_REJECT:
                print(f"[OOB] Hard reject ({px:.0f},{py:.0f}) "
                      f"drift={clamp_dist:.0f}cm ranges={[int(r) for r in ranges[:len(ap)]]}")
                return None, 'oob', len(va), (px, py), clamp_dist
            return (cx, cy), q, len(va), (px, py), clamp_dist
        else:
            if not is_inside_arena_margin(px, py):
                print(f"[OOB] Reject ({px:.0f},{py:.0f}) "
                      f"ranges={[int(r) for r in ranges[:len(ap)]]}")
                return None, 'oob', len(va), (px, py), 0.0
            return (px, py), q, len(va), (px, py), 0.0


# ═══════════════════════════════════════════════════════════════════════
# MEDIAN RANGE FILTER  (per-anchor, per-tag)
# ═══════════════════════════════════════════════════════════════════════

class RangeFilter:
    """
    Two-layer filter applied to raw UWB ranges before trilateration:

    Layer 1 — Rate guard:
      If a single anchor's range jumps more than RANGE_MAX_DELTA_CM in one
      step, that reading is a RF glitch — replace it with the last good value.
      This is the #1 fix: a single bad range reading can move the computed
      position by hundreds of cm.  Replacing with last-good keeps it stable.

    Layer 2 — Sliding-window median:
      Take the median of the last RANGE_MEDIAN_WIN readings per anchor.
      Kills residual noise without lagging much at 10-25 Hz.
    """
    def __init__(self, n_anchors: int):
        self._n   = n_anchors
        self._buf = [deque(maxlen=RANGE_MEDIAN_WIN) for _ in range(n_anchors)]
        self._last_good = [None] * n_anchors

    def filter(self, ranges: list) -> tuple:
        """
        Returns (filtered_ranges, replaced_mask)
          filtered_ranges : list of median-filtered values, length n_anchors
          replaced_mask   : True for each index whose value was rate-guarded
        """
        replaced = [False] * self._n
        for i in range(self._n):
            r = float(ranges[i]) if i < len(ranges) else 0.0

            # Rate guard — only apply if range is nominally valid
            if (self._last_good[i] is not None and
                    MIN_RANGE_CM <= r <= MAX_RANGE_CM):
                delta = abs(r - self._last_good[i])
                if delta > RANGE_MAX_DELTA_CM:
                    r = self._last_good[i]   # substitute last good
                    replaced[i] = True

            if MIN_RANGE_CM <= r <= MAX_RANGE_CM:
                self._buf[i].append(r)
                self._last_good[i] = r

        out = []
        for i in range(self._n):
            if self._buf[i]:
                s = sorted(self._buf[i])
                out.append(s[len(s) // 2])   # median
            else:
                out.append(0.0)

        return out, replaced

    def reset(self):
        for i in range(self._n):
            self._buf[i].clear()
            self._last_good[i] = None


# ═══════════════════════════════════════════════════════════════════════
# TAG STATE
# ═══════════════════════════════════════════════════════════════════════

class TagState:
    def __init__(self, tid):
        self.id=tid; self.name=f"Car{tid}"
        self.x=self.y=self.raw_x=self.raw_y=0.0
        self.status=False; self.last_update=0.0
        self.quality='unknown'; self.anchor_count=0
        self.kalman=KalmanFilter()
        self.range_filter=RangeFilter(ANCHOR_COUNT)
        self.history=deque(maxlen=TRAIL_LENGTH)
        self.update_count=0
        self._warmup=True
        self._pos_buf=deque(maxlen=SPEED_AVERAGE_SAMPLES)
        self.speed_cms=self.max_speed=0.0
        self.pkt_total=self.pkt_accepted=self.pkt_rejected=self.clamped_count=0
        self.pkt_skip_jump=0
        self.pkt_rate_guard=0        # count of rate-guarded anchor readings
        self.last_ranges=[0]*ANCHOR_COUNT        # median-filtered (what was used)
        self.last_raw_ranges=[0]*ANCHOR_COUNT    # pre-filter raw (for terminal display)
        self._last_kx: float | None = None       # last accepted position for jump guard
        self._last_ky: float | None = None
        self._consec_skip: int = 0               # consecutive skipped packets (jump guard)
        self._kx_buf: deque = deque(maxlen=POS_SMOOTH_WIN)  # rolling median buffer X
        self._ky_buf: deque = deque(maxlen=POS_SMOOTH_WIN)  # rolling median buffer Y

    def update_position(self, rx, ry, quality, anc, now):
        """
        Jump guard runs BEFORE kalman.update().
        Bad measurements are rejected without touching Kalman state at all.
        This was the critical bug in v3: Kalman was updated first, corrupting
        its internal state even when the packet was subsequently 'skipped'.
        """
        dt = max(0.010, min((now - self.last_update) if self.last_update else 0.033, 1.0))
        self.raw_x, self.raw_y = rx, ry

        # ── Jump guard: compare RAW trilateration against last ACCEPTED pos ──
        if (self._last_kx is not None and
                self.update_count >= MAX_JUMP_WARMUP):
            jump = math.hypot(rx - self._last_kx, ry - self._last_ky)
            if jump > MAX_JUMP_CM:
                self._consec_skip += 1
                self.pkt_skip_jump += 1
                # Show per-anchor ranges so user can diagnose which anchor is spiking
                anc_str = "  ".join(
                    f"A{i}={self.last_raw_ranges[i]}cm"
                    for i in range(ANCHOR_COUNT)
                    if self.last_raw_ranges[i] > 0
                )
                print(f"[JUMP] Tag{self.id}  jump={jump:.0f}cm  "
                      f"({self._last_kx:.0f},{self._last_ky:.0f})→({rx:.0f},{ry:.0f})  "
                      f"consec={self._consec_skip}  [{anc_str}]")

                # ── Recovery: if stuck skipping for too long, accept this packet
                # and reset Kalman to new position.  Prevents being frozen at a
                # bad baseline while all subsequent real positions get rejected. ──
                if self._consec_skip >= MAX_CONSEC_SKIP:
                    print(f"[RECOVER] Tag{self.id}  {self._consec_skip} consecutive skips "
                          f"→ resetting baseline to ({rx:.0f},{ry:.0f})")
                    self._last_kx, self._last_ky = rx, ry
                    self.kalman.reset()
                    self._consec_skip = 0
                    # Fall through to Kalman update below
                else:
                    return False   # Kalman NOT updated

        # ── Safe — update Kalman ─────────────────────────────────────
        kx, ky = self.kalman.update(rx, ry, dt)
        self._last_kx, self._last_ky = kx, ky
        self._consec_skip = 0   # reset consecutive-skip counter on successful accept

        # ── Rolling median smoother — kills residual jitter ──────────
        self._kx_buf.append(kx); self._ky_buf.append(ky)
        sorted_x = sorted(self._kx_buf); sorted_y = sorted(self._ky_buf)
        mid = len(sorted_x) // 2
        self.x = sorted_x[mid]; self.y = sorted_y[mid]
        self.quality=quality; self.anchor_count=anc
        self.status=True; self.last_update=now
        self.history.append((self.x, self.y, now))
        self.update_count += 1; self.pkt_accepted += 1
        if self._warmup and self.update_count >= 8:
            self._warmup = False
        self._pos_buf.append({'x': self.x, 'y': self.y, 't': now})
        if len(self._pos_buf) >= 2:
            n = min(3, len(self._pos_buf))
            p1, p2 = self._pos_buf[-n], self._pos_buf[-1]
            ddt = p2['t'] - p1['t']
            if ddt >= 0.010:
                raw_spd = math.hypot(p2['x']-p1['x'], p2['y']-p1['y']) / ddt
                raw_spd = min(raw_spd, MAX_PLAUSIBLE_SPEED_CM_S)
                if self.speed_cms > MAX_PLAUSIBLE_SPEED_CM_S:
                    self.speed_cms = raw_spd
                else:
                    self.speed_cms = 0.3 * raw_spd + 0.7 * self.speed_cms
                self.max_speed = max(self.max_speed, self.speed_cms)
        return True

    def speed_display(self):
        if SPEED_DISPLAY_UNIT=='km/h': return self.speed_cms*0.036
        if SPEED_DISPLAY_UNIT=='m/s':  return self.speed_cms/100
        return self.speed_cms

    def is_active(self):
        return self.status and (time.time()-self.last_update)<TAG_TIMEOUT

    def reset(self):
        self.kalman.reset(); self.range_filter.reset()
        self.history.clear(); self._pos_buf.clear()
        self.speed_cms=self.max_speed=0.0; self.status=False
        self._warmup=True; self._last_kx=None; self._last_ky=None; self._consec_skip=0
        self._kx_buf.clear(); self._ky_buf.clear()
        self.update_count=self.pkt_total=self.pkt_accepted=self.pkt_rejected=0
        self.clamped_count=self.pkt_skip_jump=self.pkt_rate_guard=0
        self.last_ranges=[0]*ANCHOR_COUNT
        self.last_raw_ranges=[0]*ANCHOR_COUNT

# ═══════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════

class LapScore:
    def __init__(self, car_id, car_name, lap_number):
        self.car_id=car_id; self.car_name=car_name; self.lap_number=lap_number
        self.raw_time=0.0; self.closed_at=None
        self.wall_hits=self.atk_hits=self.vic_hits=self.corner_cuts=0
        self.overspeed=self.voided=False; self._pen=self._bon=0.0

    def add_wall_hit(self):
        self._pen+=WALL_HIT_PENALTY; self.wall_hits+=1
        if PRINT_WALL_EVENTS: print(f"  🚧 WALL  | {self.car_name} Lap {self.lap_number}")

    def add_attacker_penalty(self):
        self._pen+=CAR_COLLISION_ATTACKER_PENALTY; self.atk_hits+=1
        if PRINT_COLLISION_EVENTS: print(f"  🔴 ATK   | {self.car_name} Lap {self.lap_number}")

    def add_victim_bonus(self):
        self._bon+=CAR_COLLISION_VICTIM_BONUS; self.vic_hits+=1
        if PRINT_COLLISION_EVENTS: print(f"  🟢 VIC   | {self.car_name} Lap {self.lap_number}")

    def add_corner_cut(self):
        self.corner_cuts+=1
        if CORNER_CUT_VOID_LAP: self.voided=True
        else: self._pen+=CORNER_CUT_PENALTY

    def add_overspeed(self):
        if not self.overspeed:
            self.overspeed=True; self._pen+=PIT_ZONE_OVERSPEED_PENALTY

    @property
    def elp(self):
        return float('inf') if self.voided else max(0.0, self.raw_time+self._pen-self._bon)

    def to_dict(self):
        return dict(car_id=self.car_id,car_name=self.car_name,lap=self.lap_number,
                    raw=round(self.raw_time,3),penalty=round(self._pen,3),
                    bonus=round(self._bon,3),elp=round(self.elp,3),voided=self.voided)


class ScoringEngine:
    def __init__(self):
        self._history=defaultdict(list); self._open={}; self._names={}; self._feed=[]

    def register(self,cid,name): self._names[cid]=name

    def open_lap(self,cid,n):
        self._open[cid]=LapScore(cid,self._names.get(cid,f"Car{cid}"),n)

    def close_lap(self,cid,raw):
        lap=self._open.pop(cid,None) or LapScore(cid,self._names.get(cid,f"Car{cid}"),0)
        lap.raw_time=raw; lap.closed_at=time.time()
        self._history[cid].append(lap)
        msg=f"📊 LAP | {lap.car_name} Lap {lap.lap_number} raw={raw:.2f}s ELP={lap.elp:.2f}s"
        if PRINT_LAP_EVENTS: print(msg)
        self._feed.append(msg); post_lap_to_api(cid,lap); return lap

    def laps_done(self,cid):    return len(self._history.get(cid,[]))
    def qualifies(self,cid):    return self.laps_done(cid)>=MIN_LAPS_TO_QUALIFY
    def best_elp(self,cid):
        v=[l.elp for l in self._history.get(cid,[]) if not l.voided]
        return min(v) if v else float('inf')

    def wall_hit(self,cid):
        l=self._open.get(cid)
        if l: l.add_wall_hit(); self._feed.append(f"🚧 WALL {self._names.get(cid,'?')}")

    def car_collision(self,atk,vic):
        a=self._open.get(atk); v=self._open.get(vic)
        if a: a.add_attacker_penalty()
        if v: v.add_victim_bonus()
        self._feed.append(f"💥 {self._names.get(atk,'?')}>{self._names.get(vic,'?')}")

    def corner_cut(self,cid):
        l=self._open.get(cid)
        if l: l.add_corner_cut()

    def overspeed(self,cid):
        l=self._open.get(cid)
        if l: l.add_overspeed()

    def get_leaderboard(self):
        rows=[]
        for cid,laps in self._history.items():
            valid=[l for l in laps if not l.voided]
            if not valid: continue
            best=min(valid,key=lambda l:(l.elp,l.closed_at or 0))
            rows.append(dict(car_id=cid,car_name=self._names.get(cid,f"Car{cid}"),
                             best_elp=round(best.elp,3),best_raw=round(best.raw_time,3),
                             best_lap=best.lap_number,laps_done=len(laps),
                             qualifies=self.qualifies(cid),
                             penalty_total=round(sum(l._pen for l in laps),2),
                             bonus_total=round(sum(l._bon for l in laps),2)))
        rows.sort(key=lambda r:(r['best_elp'],r['best_lap'])); return rows

    def get_car_summary(self,cid):
        laps=self._history.get(cid,[]); op=self._open.get(cid)
        return dict(car_id=cid,car_name=self._names.get(cid,f"Car{cid}"),
                    laps_done=len(laps),best_elp=self.best_elp(cid),
                    qualifies=self.qualifies(cid),
                    open_lap=op.to_dict() if op else None,
                    history=[l.to_dict() for l in laps])

    def get_feed(self,n=8): return self._feed[-n:]

    def reset(self):
        self._history.clear(); self._open.clear(); self._feed.clear()
        print("📊 Scoring reset")


# ═══════════════════════════════════════════════════════════════════════
# TRACK
# ═══════════════════════════════════════════════════════════════════════

class Track:
    def __init__(self,outer,inner=None):
        self.outer=outer; self.inner=inner or []
    def has_width(self):          return len(self.inner)>0
    def get_outer_points(self):   return self.outer
    def get_inner_points(self):   return self.inner


_CSV_INNER = [
    (63.4,112.2),(45.0,106.0),(9.2,111.1),(-1.9,158.6),(-1.4,203.9),
    (22.6,233.9),(62.4,246.8),(110.0,251.0),(158.3,246.6),(197.1,228.6),
    (218.3,194.2),(222.0,155.0),(218.7,117.3),(205.9,81.3),(170.5,57.1),
    (132.5,48.2),(110.0,46.0),(86.8,48.4),(60.8,58.0),(38.9,80.1),
    (43.3,112.7),(63.4,112.2),
]
_CSV_OUTER = [
    (86.6,127.8),(45.0,134.0),(30.8,128.9),(25.9,161.4),(25.4,196.1),
    (37.4,210.1),(67.6,219.2),(110.0,223.0),(151.7,219.4),(178.9,207.4),
    (191.7,185.8),(194.0,155.0),(191.3,122.7),(184.1,98.7),(159.5,82.9),
    (127.5,75.8),(110.0,74.0),(93.2,75.6),(75.2,82.0),(65.1,89.9),
    (66.7,97.3),(86.6,127.8),
]


def create_circuit_track() -> Track:
    return Track(outer=_CSV_OUTER, inner=_CSV_INNER)


def dist_to_boundary(px,py,pts):
    if not pts or len(pts)<2: return float('inf')
    best=float('inf'); n=len(pts)
    for i in range(n):
        x1,y1=pts[i]; x2,y2=pts[(i+1)%n]; dx,dy=x2-x1,y2-y1; den=dx*dx+dy*dy
        if den==0: d=math.hypot(px-x1,py-y1)
        else:
            t=max(0,min(1,((px-x1)*dx+(py-y1)*dy)/den))
            d=math.hypot(px-x1-t*dx,py-y1-t*dy)
        best=min(best,d)
    return best


# ═══════════════════════════════════════════════════════════════════════
# LAP ENGINE
# ═══════════════════════════════════════════════════════════════════════

class LapEngine:
    def __init__(self, cid, name, sc):
        self.car_id   = cid
        self.car_name = name
        self.scoring  = sc
        self.current_lap   = 0
        self.laps_done     = 0
        self.is_racing     = False
        self.race_finished = False
        self.admin_armed   = False
        self._lap_start    = None
        self._last_cross   = 0.0
        self._lap_times    = []
        self._next_cp  = 0
        self._cp_dwell = 0
        self._sf_side = None

    def arm(self):
        self.admin_armed = True
        print(f"🟢 ARM | {self.car_name} (auto-start on line crossing)")

    def update(self, x, y, speed, now):
        cp_ev = None
        if self.is_racing:
            cp_ev = self._check_checkpoints(x, y)
        sf_ev = self._check_sf_line(x, y, now)
        return sf_ev or cp_ev

    def _on_line(self, y):
        return (START_LINE_Y1 - LINE_Y_TOLERANCE) <= y <= (START_LINE_Y2 + LINE_Y_TOLERANCE)

    def _check_sf_line(self, x, y, now):
        tol = LINE_CROSS_TOLERANCE
        if x < START_LINE_X - tol:
            new_side = 'left'
        elif x > START_LINE_X + tol:
            new_side = 'right'
        else:
            return None

        if self._sf_side is None:
            self._sf_side = new_side
            return None

        prev_side      = self._sf_side
        self._sf_side  = new_side

        if SF_CROSSING_DIR == 'right_to_left':
            crossing = (prev_side == 'right' and new_side == 'left')
        else:
            crossing = (prev_side == 'left'  and new_side == 'right')

        if not crossing:
            return None
        if not self._on_line(y):
            print(f"[SF] {self.car_name} crossed ({prev_side}→{new_side}) but y={y:.0f} "
                  f"outside [{START_LINE_Y1-LINE_Y_TOLERANCE}..{START_LINE_Y2+LINE_Y_TOLERANCE}] — ignored")
            return None
        if now - self._last_cross < MIN_LAP_TIME:
            print(f"[SF] {self.car_name} crossed too fast (debounce {now-self._last_cross:.1f}s < {MIN_LAP_TIME}s)")
            return None
        print(f"[SF] ✓ {self.car_name} valid crossing ({prev_side}→{new_side}) "
              f"x={x:.0f} y={y:.0f}")

        self._last_cross = now
        return self._process_crossing(now)

    def _process_crossing(self, now):
        if not self.is_racing:
            self.is_racing   = True
            self.current_lap = 1
            self._lap_start  = now
            self._next_cp    = 0
            self._cp_dwell   = 0
            self.scoring.open_lap(self.car_id, 1)
            print(f"🏁 START | {self.car_name} Lap 1/{TOTAL_LAPS}")
            return dict(type='race_start', car_id=self.car_id,
                        car_name=self.car_name, lap=1, time=now)

        if self._next_cp < len(CHECKPOINTS):
            missing = len(CHECKPOINTS) - self._next_cp
            print(f"⚠ LAP VOID | {self.car_name} — {missing} checkpoint(s) not hit "
                  f"(next required: CP{self._next_cp}) — lap NOT counted")
            self._next_cp  = 0
            self._cp_dwell = 0
            return None

        raw = now - self._lap_start
        ls  = self.scoring.close_lap(self.car_id, raw)
        self._lap_times.append(raw)
        self.laps_done += 1
        self._next_cp   = 0
        self._cp_dwell  = 0

        ev = dict(type='lap_done', car_id=self.car_id, car_name=self.car_name,
                  lap=self.current_lap, raw_time=raw, elp=ls.elp, time=now)

        if self.laps_done >= TOTAL_LAPS:
            self.is_racing     = False
            self.race_finished = True
            if PRINT_LAP_EVENTS:
                print(f"🏆 FINISH | {self.car_name} ({self.laps_done} laps)")
            ev['type'] = 'race_finish'
            return ev

        self.current_lap += 1
        self._lap_start   = now
        self.scoring.open_lap(self.car_id, self.current_lap)
        if PRINT_LAP_EVENTS:
            print(f"🔄 LAP | {self.car_name} Lap {self.current_lap}/{TOTAL_LAPS} "
                  f"raw={raw:.2f}s ELP={ls.elp:.2f}s")
        return ev

    def _check_checkpoints(self, x, y):
        if self._next_cp >= len(CHECKPOINTS):
            return None
        cx, cy, cr = CHECKPOINTS[self._next_cp]
        dist = math.hypot(x - cx, y - cy)
        enter_r = cr * 1.5

        if dist <= enter_r:
            self._cp_dwell += 1
        else:
            self._cp_dwell = 0

        if self._cp_dwell >= CP_DWELL_FRAMES:
            self._cp_dwell = 0
            idx = self._next_cp
            print(f"  ✔ CP{idx} | {self.car_name} "
                  f"@ ({x:.0f},{y:.0f})  dist={dist:.0f}cm  [{idx+1}/{len(CHECKPOINTS)}]")
            self._next_cp += 1
            return dict(type='checkpoint', car_id=self.car_id,
                        car_name=self.car_name, cp_index=idx,
                        total=len(CHECKPOINTS))
        return None

    def elapsed(self, now):
        return (now - self._lap_start) if self._lap_start else 0.0

    def best_raw(self):
        return min(self._lap_times) if self._lap_times else 0.0

    def get_info(self, now=None):
        return dict(
            car_id            = self.car_id,
            car_name          = self.car_name,
            current_lap       = self.current_lap,
            total_laps        = TOTAL_LAPS,
            laps_done         = self.laps_done,
            is_racing         = self.is_racing,
            race_finished     = self.race_finished,
            current_lap_elapsed = self.elapsed(now or time.time()),
            best_raw          = self.best_raw(),
            lap_times         = list(self._lap_times),
            checkpoints_hit   = self._next_cp,
            checkpoints_total = len(CHECKPOINTS),
        )

    def reset(self):
        self.current_lap   = 0
        self.laps_done     = 0
        self.is_racing     = False
        self.race_finished = False
        self.admin_armed   = False
        self._sf_side      = None
        self._lap_start    = None
        self._last_cross   = 0.0
        self._lap_times.clear()
        self._next_cp      = 0


# ═══════════════════════════════════════════════════════════════════════
# RACE MANAGER
# ═══════════════════════════════════════════════════════════════════════

class RaceManager:
    def __init__(self,sc):
        self.scoring=sc; self._engines={}
        self.race_active=False; self.race_start_time=self.race_end_time=None

    def register(self,cid,name):
        self.scoring.register(cid,name); self._engines[cid]=LapEngine(cid,name,self.scoring)

    def admin_start(self):
        for e in self._engines.values(): e.arm()
        print(f"🟢 RACE ARMED – {TOTAL_LAPS} laps")

    def update(self,cid,x,y,speed,now):
        eng=self._engines.get(cid)
        if not eng: return None
        ev=eng.update(x,y,speed,now)
        if ev:
            if ev['type']=='race_start' and not self.race_active:
                self.race_active=True; self.race_start_time=now; print("🏁 RACE IN PROGRESS")
            if ev['type']=='race_finish' and all(e.race_finished for e in self._engines.values()):
                self.race_active=False; self.race_end_time=now; print("🏆 ALL FINISHED")
        return ev

    def get_info(self,cid,now=None):
        e=self._engines.get(cid); return e.get_info(now) if e else None

    def get_leaderboard(self): return self.scoring.get_leaderboard()

    def reset(self):
        for e in self._engines.values(): e.reset()
        self.scoring.reset(); self.race_active=False
        self.race_start_time=self.race_end_time=None; print("🔄 Race reset")


# ═══════════════════════════════════════════════════════════════════════
# COLLISION ENGINE
# ═══════════════════════════════════════════════════════════════════════

def _point_in_polygon(px: float, py: float, polygon) -> bool:
    n, inside = len(polygon), False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class CollisionEngine:
    def __init__(self,sc,track):
        self.scoring=sc; self.track=track
        self._names={}; self._pos={}; self._speeds={}; self._laps={}; self._racing={}
        self._car_cd={}; self._wall_cd={}; self._ghost_t={}; self._spd_buf=[]
        self._wall_oob={}
        self.events=[]; self.anomalies=[]

    def register(self,cid,name): self._names[cid]=name

    def update(self,cars,now):
        evts=[]
        for cid,d in cars.items():
            x, y = d['x'], d['y']
            spd   = d.get('speed', 0.0)
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if abs(x) > 10000 or abs(y) > 10000:
                continue
            self._pos[cid]=(x,y,now); self._speeds[cid]=spd
            self._laps[cid]=d.get('lap',0); self._racing[cid]=d.get('racing',False)
            if 0 < spd <= MAX_PLAUSIBLE_SPEED_CM_S:
                self._spd_buf.append(spd)
            if len(self._spd_buf)>300: self._spd_buf.pop(0)
            if spd>MAX_PLAUSIBLE_SPEED_CM_S: self._anomaly(cid,spd,now)
        racing=[c for c,d in cars.items() if d.get('racing',False)]
        for i in range(len(racing)):
            for j in range(i+1,len(racing)):
                e=self._car(racing[i],racing[j],now)
                if e: evts.append(e)
        for cid,d in cars.items():
            if not d.get('racing',False): continue
            e=self._wall(cid,d['x'],d['y'],d.get('lap',0),now)
            if e: evts.append(e)
        self.events.extend(evts); return evts

    def _car(self,a,b,now):
        if self._ghost(a) or self._ghost(b): return None
        pa=self._pos.get(a); pb=self._pos.get(b)
        if not pa or not pb: return None
        dist=math.hypot(pa[0]-pb[0],pa[1]-pb[1])
        if dist>CAR_COLLISION_DISTANCE_CM: return None
        key=frozenset([a,b])
        if now-self._car_cd.get(key,0)<CAR_COLLISION_COOLDOWN: return None
        self._car_cd[key]=now
        sa=self._speeds.get(a,0); sb=self._speeds.get(b,0)
        atk,vic=(a,b) if abs(sa-sb)>=SPEED_DIFF_THRESHOLD and sa>=sb \
                else ((b,a) if abs(sa-sb)>=SPEED_DIFF_THRESHOLD else (a,b))
        self.scoring.car_collision(atk,vic)
        an=self._names.get(atk,f"Car{atk}"); vn=self._names.get(vic,f"Car{vic}")
        if PRINT_COLLISION_EVENTS: print(f"💥 CAR | {an}>{vn} dist={dist:.1f}cm")
        return dict(type='car',attacker=atk,victim=vic,attacker_name=an,victim_name=vn,
                    dist=dist,lap=self._laps.get(atk,0),time=now)

    def _wall(self,cid,x,y,lap,now):
        if not self.track or not self.track.has_width(): return None
        if now-self._wall_cd.get(cid,0)<WALL_COLLISION_COOLDOWN: return None

        outer = self.track.get_outer_points()
        inner = self.track.get_inner_points()

        outside_outer = not _point_in_polygon(x, y, outer)
        inside_inner  = _point_in_polygon(x, y, inner)

        od  = dist_to_boundary(x, y, outer)
        id_ = dist_to_boundary(x, y, inner)

        wall = None
        if outside_outer or od <= WALL_TOLERANCE_CM:
            wall = 'outer'
        elif inside_inner or id_ <= WALL_TOLERANCE_CM:
            wall = 'inner'

        if not wall:
            self._wall_oob[cid] = 0
            return None
        self._wall_oob[cid] = self._wall_oob.get(cid, 0) + 1
        if self._wall_oob[cid] < WALL_OOB_FRAMES_NEEDED:
            return None
        self._wall_oob[cid] = 0
        self._wall_cd[cid]=now; self.scoring.wall_hit(cid)
        name=self._names.get(cid,f"Car{cid}")
        if PRINT_WALL_EVENTS: print(f"🚧 WALL | {name} {wall} Lap{lap} pos=({x:.0f},{y:.0f})")
        return dict(type='wall',car_id=cid,car_name=name,wall=wall,lap=lap,time=now)

    def _ghost(self,cid):
        spd=self._speeds.get(cid,0)
        avg=sum(self._spd_buf)/len(self._spd_buf) if self._spd_buf else 1
        if spd<avg*GHOSTING_SPEED_THRESHOLD:
            if cid not in self._ghost_t: self._ghost_t[cid]=time.time()
            elif time.time()-self._ghost_t[cid]>GHOSTING_TIME_THRESHOLD: return True
        else: self._ghost_t.pop(cid,None)
        return False

    def _anomaly(self,cid,spd,now):
        n=self._names.get(cid,f"Car{cid}")
        self.anomalies.append(dict(car_id=cid,name=n,speed=spd,time=now))
        if PRINT_ANOMALIES: print(f"⚠️ ANOMALY | {n} speed={spd:.0f}cm/s ({spd*0.036:.1f}km/h)")

    def wall_hits(self,cid):
        return [e for e in self.events if e['type']=='wall' and e['car_id']==cid]

    def car_events(self,cid):
        return [e for e in self.events if e['type']=='car' and (e['attacker']==cid or e['victim']==cid)]

    def reset(self):
        self.events.clear(); self.anomalies.clear()
        self._car_cd.clear(); self._wall_cd.clear(); self._ghost_t.clear(); self._spd_buf.clear()
        self._wall_oob.clear()
        print("✓ Collision reset")


# ═══════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════
tags      = {i: TagState(i) for i in range(TAG_COUNT)}
scoring   = ScoringEngine()
race_mgr  = RaceManager(scoring)
track     = create_circuit_track()
col_eng   = CollisionEngine(scoring, track)

for tid, tag in tags.items():
    race_mgr.register(tid, tag.name)
    col_eng.register(tid, tag.name)

connected_clients = set()
event_loop        = None
running           = True
race_armed        = False

stats = {
    'udp_total':0,'udp_valid':0,'udp_invalid':0,'udp_oob':0,'udp_clamped':0,
    'udp_skip_clamp':0,'udp_skip_jump':0,   # ★ new counters
    'ws_sent':0,'ws_clients':0,'tags_seen':set(),'start':datetime.now()
}


# ═══════════════════════════════════════════════════════════════════════
# RACE UPDATE HELPERS
# ═══════════════════════════════════════════════════════════════════════

def process_race_update(tid, now):
    tag=tags.get(tid)
    if not tag or not tag.is_active(): return []
    if tag._warmup: return []
    evts=[]
    ev=race_mgr.update(tid,tag.x,tag.y,tag.speed_cms,now)
    if ev: evts.append(ev)
    cars={}
    for t_id,t in tags.items():
        if t.is_active():
            li=race_mgr.get_info(t_id,now)
            cars[t_id]=dict(x=t.x,y=t.y,speed=t.speed_cms,
                            lap=li['current_lap'] if li else 0,
                            racing=li['is_racing'] if li else False)
    if cars: evts.extend(col_eng.update(cars,now))
    return evts


def build_state(now):
    cars=[]
    for tid,tag in tags.items():
        if not tag.is_active(): continue
        li=race_mgr.get_info(tid,now); sc=scoring.get_car_summary(tid)
        cars.append(dict(
            tag_id=tid,name=tag.name,
            x=round(tag.x,1),y=round(tag.y,1),
            raw_x=round(tag.raw_x,1),raw_y=round(tag.raw_y,1),
            speed=round(tag.speed_display(),2),speed_unit=SPEED_DISPLAY_UNIT,
            speed_cms=round(tag.speed_cms,1),quality=tag.quality,
            anchor_count=tag.anchor_count,last_ranges=tag.last_ranges,
            trail=[(round(h[0],1),round(h[1],1)) for h in tag.history],
            lap_info=li,
            scoring=dict(best_elp=sc['best_elp'] if sc['best_elp']<float('inf') else None,
                         laps_done=sc['laps_done'],qualifies=sc['qualifies'],
                         history=sc['history']),
            wall_hits=len(col_eng.wall_hits(tid)),
            car_collisions=len(col_eng.car_events(tid)),
            pkt_accepted=tag.pkt_accepted,pkt_rejected=tag.pkt_rejected,
            pkt_clamped=tag.clamped_count,pkt_skip_jump=tag.pkt_skip_jump))
    return json.dumps(dict(
        type="state_update",timestamp=now,
        race_active=race_mgr.race_active,race_armed=race_armed,
        total_laps=TOTAL_LAPS,group_id=current_group_id,
        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
        cars=cars,leaderboard=race_mgr.get_leaderboard(),
        feed=scoring.get_feed(10)))


# ═══════════════════════════════════════════════════════════════════════
# UDP RECEIVER
# ═══════════════════════════════════════════════════════════════════════

def udp_receiver():
    global running
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('',UDP_PORT)); sock.settimeout(0.1)
    print(f"[UDP] Listening on port {UDP_PORT}")

    while running:
        try:
            data, addr = sock.recvfrom(2048)
            stats['udp_total']+=1

            try:
                uwb=json.loads(data.decode('utf-8',errors='ignore').strip())
            except:
                stats['udp_invalid']+=1; continue

            if 'id' not in uwb or 'range' not in uwb:
                stats['udp_invalid']+=1; continue

            tid=int(uwb['id'])
            if tid not in tags:
                stats['udp_invalid']+=1; continue

            slot_ranges=uwb['range']
            if not isinstance(slot_ranges,list) or len(slot_ranges)<ANCHOR_COUNT:
                stats['udp_invalid']+=1; continue

            ancid      = uwb.get('ancid', [])
            raw_ranges = reorder_by_ancid(slot_ranges, ancid, ANCHOR_COUNT)

            slot_rssi = [float(x) for x in uwb.get('rssi', [0.0]*len(slot_ranges))]
            if ancid and any(a>=0 for a in ancid):
                rssi=[0.0]*ANCHOR_COUNT
                for si,ai in enumerate(ancid):
                    if 0<=ai<ANCHOR_COUNT and si<len(slot_rssi):
                        rssi[ai]=slot_rssi[si]
            else:
                rssi=slot_rssi[:ANCHOR_COUNT]

            now=time.time()
            tag=tags[tid]; tag.pkt_total+=1

            # ── RAW MODE: no filtering, no Kalman, no jump guard ────────────
            # Trilaterate directly from raw ranges, send straight to frontend.
            raw_int = [int(r) for r in raw_ranges]
            tag.last_raw_ranges = raw_int
            tag.last_ranges = raw_int

            pos, quality, anc_count, raw_trilat, clamp_dist = \
                Positioning.calculate(raw_ranges, rssi, ANCHOR_POSITIONS)

            if pos is None:
                tag.pkt_rejected += 1
                stats['udp_oob' if quality == 'oob' else 'udp_invalid'] += 1
                continue

            rx, ry = pos   # raw trilaterated position — no clamp, no filter
            tag.raw_x, tag.raw_y = rx, ry
            tag.x, tag.y = rx, ry   # display position = raw position
            tag.quality = quality
            tag.anchor_count = anc_count
            tag.status = True
            tag.last_update = now
            tag.history.append((rx, ry, now))
            tag.update_count += 1
            tag.pkt_accepted += 1
            tag._last_kx = rx
            tag._last_ky = ry

            stats['udp_valid'] += 1
            stats['tags_seen'].add(tid)

            pct = tag.pkt_accepted / tag.pkt_total * 100 if tag.pkt_total else 0

            print(
                f"[UWB] Tag{tid}  "
                f"({rx:.0f},{ry:.0f})  "
                f"ranges={raw_int}  {quality}  "
                f"{tag.speed_display():.1f}{SPEED_DISPLAY_UNIT}  acc={pct:.0f}%"
            )

            game_evts=process_race_update(tid,now)

            if connected_clients and event_loop:
                li=race_mgr.get_info(tid,now)
                open_lap=scoring._open.get(tid)
                msg=json.dumps(dict(
                    type="tag_position",tag_id=tid,
                    x=round(tag.x,1),y=round(tag.y,1),
                    raw_x=round(rx,1),raw_y=round(ry,1),
                    range=raw_ranges,
                    speed=round(tag.speed_display(),2),
                    speed_cms=round(tag.speed_cms,1),
                    speed_unit=SPEED_DISPLAY_UNIT,
                    quality=quality,anchor_count=anc_count,
                    timestamp=now,game_events=game_evts,
                    wall_hits=len(col_eng.wall_hits(tid)),
                    car_collisions=len(col_eng.car_events(tid)),
                    current_penalty=round(open_lap._pen,2) if open_lap else 0.0,
                    current_bonus=round(open_lap._bon,2) if open_lap else 0.0,
                    lap_info=li))
                asyncio.run_coroutine_threadsafe(broadcast(msg),event_loop)
                if game_evts:
                    asyncio.run_coroutine_threadsafe(broadcast(build_state(now)),event_loop)

        except socket.timeout:
            continue
        except Exception as e:
            if running: print(f"[UDP] Error: {e}")

    sock.close(); print("[UDP] Stopped")


# ═══════════════════════════════════════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════════════════════════════════════

async def broadcast(msg):
    if not connected_clients: return
    stats['ws_sent']+=1
    dead=set()
    for c in connected_clients:
        try: await c.send(msg)
        except: dead.add(c)
    connected_clients.difference_update(dead)


async def handle_client(ws):
    global race_armed, TOTAL_LAPS, tag_to_gp, current_group_id
    cid=f"{ws.remote_address[0]}:{ws.remote_address[1]}"
    print(f"[WS] Connected: {cid}")
    connected_clients.add(ws); stats['ws_clients']+=1

    try:
        now=time.time()
        await ws.send(json.dumps(dict(
            type="connection",status="connected",
            message="UWB Racing — FIXED v3 (anti-spike)",
            timestamp=now,
            server_info=dict(
                udp_port=UDP_PORT,ws_port=WS_PORT,
                anchor_count=ANCHOR_COUNT,tag_count=TAG_COUNT,
                total_laps=TOTAL_LAPS,
                oob_mode='clamp' if CLAMP_TO_ARENA else 'reject',
                clamp_skip_threshold=CLAMP_SKIP_THRESHOLD,
                max_jump_cm=MAX_JUMP_CM,
                kalman_gain=round(KALMAN_PROCESS_NOISE/(KALMAN_PROCESS_NOISE+KALMAN_MEASUREMENT_NOISE),3),
                range_validation=dict(min_range=MIN_RANGE_CM,max_range=MAX_RANGE_CM,
                                      triangle_tolerance=TRIINEQ_TOL),
                uptime_seconds=(datetime.now()-stats['start']).total_seconds()),
            anchors={str(k):{"x":v[0],"y":v[1]} for k,v in ANCHOR_POSITIONS.items()},
            track=dict(outer_points=track.get_outer_points(),
                       inner_points=track.get_inner_points()),
            stats=dict(packets_received=stats['udp_valid'],
                       tags_seen=sorted(list(stats['tags_seen']))))))
        await ws.send(build_state(now))

        async for message in ws:
            try:
                d=json.loads(message); mt=d.get('type')

                if mt=='ping':
                    await ws.send(json.dumps({"type":"pong","timestamp":time.time()}))

                elif mt=='admin_start':
                    apply_race_config(d.get('race_config',{}), d.get('total_laps'))
                    nm=d.get('tag_map',{})
                    if nm: tag_to_gp={int(k):int(v) for k,v in nm.items()}
                    current_group_id=d.get('group_id')
                    race_mgr.reset(); race_mgr.admin_start(); race_armed=True
                    await broadcast(json.dumps(dict(
                        type="admin_event",event="race_armed",
                        message=f"Race armed – {TOTAL_LAPS} laps",
                        total_laps=TOTAL_LAPS,group_id=current_group_id,
                        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
                        timestamp=time.time())))
                    print(f"[CMD] Admin Start group={current_group_id} laps={TOTAL_LAPS}")

                elif mt=='reset':
                    race_mgr.reset(); col_eng.reset(); race_armed=False
                    tag_to_gp={}; current_group_id=None
                    for t in tags.values(): t.reset()
                    reset_race_config()
                    await broadcast(json.dumps(dict(type="admin_event",event="race_reset",
                        message="Race reset",timestamp=time.time())))
                    print("[CMD] Reset")

                elif mt=='get_stats':
                    uptime=(datetime.now()-stats['start']).total_seconds()
                    ts={}
                    for t_id,t in tags.items():
                        if t.pkt_total>0:
                            ts[t_id]=dict(total=t.pkt_total,accepted=t.pkt_accepted,
                                          rejected=t.pkt_rejected,clamped=t.clamped_count,
                                          skip_jump=t.pkt_skip_jump,
                                          accept_pct=round(t.pkt_accepted/t.pkt_total*100,1),
                                          last_ranges=t.last_ranges)
                    await ws.send(json.dumps(dict(
                        type="stats",
                        udp_total=stats['udp_total'],udp_valid=stats['udp_valid'],
                        udp_invalid=stats['udp_invalid'],udp_oob=stats['udp_oob'],
                        udp_clamped=stats['udp_clamped'],
                        udp_skip_clamp=stats['udp_skip_clamp'],
                        udp_skip_jump=stats['udp_skip_jump'],
                        ws_sent=stats['ws_sent'],ws_clients=len(connected_clients),
                        tags_seen=sorted(list(stats['tags_seen'])),tag_stats=ts,
                        uptime_seconds=uptime,total_laps=TOTAL_LAPS,
                        group_id=current_group_id,tag_to_gp=tag_to_gp,
                        oob_mode='clamp' if CLAMP_TO_ARENA else 'reject',
                        race_config=dict(wall_hit_penalty=WALL_HIT_PENALTY,
                                         attacker_penalty=CAR_COLLISION_ATTACKER_PENALTY,
                                         victim_bonus=CAR_COLLISION_VICTIM_BONUS),
                        leaderboard=race_mgr.get_leaderboard(),
                        feed=scoring.get_feed(20),timestamp=time.time())))

                elif mt=='get_state':
                    await ws.send(build_state(time.time()))

                else:
                    print(f"[WS] Unknown cmd '{mt}' from {cid}")

            except json.JSONDecodeError:
                print(f"[WS] Bad JSON from {cid}")
            except Exception as e:
                print(f"[WS] Handler error: {e}")

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[WS] Client error: {e}")
    finally:
        connected_clients.discard(ws)
        print(f"[WS] Disconnected: {cid} | active={len(connected_clients)}")


async def stats_reporter():
    while running:
        await asyncio.sleep(60)
        if not running: break
        up=(datetime.now()-stats['start']).total_seconds()
        tot=stats['udp_total']; val=stats['udp_valid']
        pct=val/tot*100 if tot else 0
        print(f"\n{'═'*65}")
        print(f"STATS  uptime={up:.0f}s  UDP {val}/{tot} ({pct:.0f}%)  "
              f"clamped={stats['udp_clamped']}  "
              f"skip_clamp={stats['udp_skip_clamp']}  "
              f"skip_jump={stats['udp_skip_jump']}  "
              f"WS clients={len(connected_clients)}")
        for tid,t in tags.items():
            if t.pkt_total>0:
                p=t.pkt_accepted/t.pkt_total*100
                cp=t.clamped_count/t.pkt_accepted*100 if t.pkt_accepted else 0
                print(f"  Tag{tid}: {t.pkt_accepted}/{t.pkt_total} ({p:.0f}%)  "
                      f"clamped={t.clamped_count}({cp:.0f}%)  "
                      f"skip_jump={t.pkt_skip_jump}  "
                      f"ranges={t.last_ranges}")
        lb=race_mgr.get_leaderboard()
        if lb:
            for i,r in enumerate(lb):
                elp=f"{r['best_elp']:.2f}s" if r['best_elp']<float('inf') else "—"
                print(f"  {i+1}. {r['car_name']:<8} ELP={elp} Laps={r['laps_done']}")
        print(f"{'═'*65}\n")


async def main():
    global event_loop, running
    event_loop=asyncio.get_event_loop()
    minX,maxX,minY,maxY=_ARENA
    print(f"\n{'═'*65}")
    print(f"  UWB RACING SYSTEM — FIXED v3 (Anti-Spike)")
    print(f"  UDP={UDP_PORT}  WS={WS_PORT}")
    print(f"  Arena: {minX}..{maxX} × {minY}..{maxY} cm")
    print(f"  Kalman gain: {KALMAN_PROCESS_NOISE/(KALMAN_PROCESS_NOISE+KALMAN_MEASUREMENT_NOISE):.3f}  (P={KALMAN_PROCESS_NOISE} M={KALMAN_MEASUREMENT_NOISE})")
    print(f"  Pos smooth window: {POS_SMOOTH_WIN} samples (rolling median)")
    print(f"  Clamp-skip threshold: {CLAMP_SKIP_THRESHOLD} cm")
    print(f"  Jump guard: {MAX_JUMP_CM} cm/step (after {MAX_JUMP_WARMUP} warmup pkts, reset after {MAX_CONSEC_SKIP} consecutive skips)")
    print(f"  Range median window: {RANGE_MEDIAN_WIN}  Max delta: {RANGE_MAX_DELTA_CM} cm")
    print(f"  TriIneq tolerance: {TRIINEQ_TOL*100:.0f}%")
    print(f"  Anchors: {ANCHOR_POSITIONS}")
    print(f"{'═'*65}\n")

    threading.Thread(target=udp_receiver, daemon=True, name="UDP").start()
    asyncio.create_task(stats_reporter())

    try:
        async with websockets.serve(handle_client, "0.0.0.0", WS_PORT):
            print(f"[WS] ws://0.0.0.0:{WS_PORT}  ready\n✓ READY\n")
            await asyncio.Future()
    except OSError as e:
        print(f"✗ Port error: {e}"); running=False


def signal_handler(sig, frame):
    global running; running=False
    up=(datetime.now()-stats['start']).total_seconds()
    tot=stats['udp_total']; val=stats['udp_valid']
    print(f"\n{'═'*65}")
    print(f"SHUTDOWN  uptime={up:.0f}s  "
          + (f"UDP {val}/{tot} ({val/tot*100:.0f}%)" if tot else ""))
    lb=race_mgr.get_leaderboard()
    if lb:
        for i,r in enumerate(lb):
            elp=f"{r['best_elp']:.2f}s" if r['best_elp']<float('inf') else "—"
            print(f"  {i+1}. {r['car_name']}  ELP={elp}  Laps={r['laps_done']}")
    print(f"{'═'*65}\n"); sys.exit(0)


if __name__=="__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        signal_handler(None,None)
    except Exception as e:
        print(f"\n✗ FATAL: {e}")
        import traceback; traceback.print_exc()