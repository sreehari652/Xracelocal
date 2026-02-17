"""
Lap Engine
Tracks start/finish crossings, cooldown, checkpoint validation, pit-zone speed.
Integrates with ScoringEngine to open/close LapScore objects.
"""
import time, math
from race_config import *


class LapEngine:
    """One per car."""

    def __init__(self, car_id, car_name, scoring_engine):
        self.car_id   = car_id
        self.car_name = car_name
        self.scoring  = scoring_engine

        self.current_lap       = 0
        self.total_laps_done   = 0
        self.is_racing         = False
        self.race_finished     = False
        self.admin_armed       = False   # set True by admin Start signal

        self._side             = None   # which side of line car is on
        self._lap_start        = None
        self._last_cross       = 0.0    # epoch of last valid crossing
        self._checkpoints_hit  = set()  # which checkpoints passed this lap
        self._lap_times        = []     # raw lap times

        # for checkpoint tracking
        self._all_checkpoints  = list(range(len(CHECKPOINTS)))

    # ── admin control ───────────────────────────────────────
    def arm(self):
        """Admin hits Start – enable lap activation."""
        self.admin_armed = True
        print(f"🟢 ARM  | {self.car_name} – waiting for line crossing")

    # ── main update ─────────────────────────────────────────
    def update(self, x, y, speed, now) -> dict | None:
        """
        Call every frame with current position.
        Returns event dict on lap completion, else None.
        """
        # Check checkpoint passage (during racing only)
        if self.is_racing:
            self._check_checkpoints(x, y)
            # Pit-zone speed check (near start line, first lap)
            self._check_pit_speed(x, y, speed)

        # Determine which side of start line
        new_side = self._get_side(x, y)

        # First update – just store side
        if self._side is None:
            self._side = new_side
            return None

        crossed = (self._side != new_side)
        in_bounds = self._within_line_bounds(x, y)

        if crossed and in_bounds:
            self._side = new_side
            return self._handle_crossing(now, x, y)

        self._side = new_side
        return None

    # ── crossing logic ──────────────────────────────────────
    def _handle_crossing(self, now, x, y):
        # Cooldown guard (jitter / double-count)
        if now - self._last_cross < MIN_LAP_TIME:
            if PRINT_LAP_EVENTS:
                print(f"⏱  COOLDOWN | {self.car_name} – crossing ignored ({now-self._last_cross:.2f}s)")
            return None
        self._last_cross = now

        # Lap 0 → start of race
        if not self.is_racing:
            if not self.admin_armed:
                if PRINT_LAP_EVENTS:
                    print(f"⏸  NOT ARMED | {self.car_name} – admin hasn't started race")
                return None
            self.is_racing   = True
            self.current_lap = 1
            self._lap_start  = now
            self._checkpoints_hit.clear()
            self.scoring.open_lap(self.car_id, self.current_lap)
            if PRINT_LAP_EVENTS:
                print(f"🏁 START | {self.car_name} – Lap 1/{TOTAL_LAPS}")
            return dict(type='race_start', car_id=self.car_id,
                        car_name=self.car_name, lap=self.current_lap, time=now)

        # Lap N completion
        raw = now - self._lap_start
        self._validate_checkpoints()            # void / penalise corner cuts
        lap_score = self.scoring.close_lap(self.car_id, raw)
        self._lap_times.append(raw)
        self.total_laps_done += 1

        event = dict(type='lap_done', car_id=self.car_id,
                     car_name=self.car_name,
                     lap=self.current_lap,
                     raw_time=raw,
                     elp=lap_score.elp,
                     time=now)

        if self.total_laps_done >= TOTAL_LAPS:
            self.is_racing      = False
            self.race_finished  = True
            if PRINT_LAP_EVENTS:
                print(f"🏆 FINISH | {self.car_name} – all {TOTAL_LAPS} laps done")
            event['type'] = 'race_finish'
            return event

        # Next lap
        self.current_lap += 1
        self._lap_start   = now
        self._checkpoints_hit.clear()
        self.scoring.open_lap(self.car_id, self.current_lap)
        if PRINT_LAP_EVENTS:
            print(f"🔄 LAP  | {self.car_name} – Lap {self.current_lap}/{TOTAL_LAPS}  raw={raw:.2f}s  ELP={lap_score.elp:.2f}s")
        return event

    # ── helper methods ──────────────────────────────────────
    def _get_side(self, x, y):
        if START_LINE_ORIENTATION == 'vertical':
            return x < START_LINE_X
        return y < START_LINE_Y1

    def _within_line_bounds(self, x, y):
        if START_LINE_ORIENTATION == 'vertical':
            return START_LINE_Y1 <= y <= START_LINE_Y2
        return START_LINE_X - LINE_CROSSING_THRESHOLD <= x <= START_LINE_X + LINE_CROSSING_THRESHOLD

    def _check_checkpoints(self, x, y):
        for idx, (cx, cy) in enumerate(CHECKPOINTS):
            if idx in self._checkpoints_hit: continue
            if math.hypot(x-cx, y-cy) <= CHECKPOINT_RADIUS:
                self._checkpoints_hit.add(idx)

    def _validate_checkpoints(self):
        for idx in self._all_checkpoints:
            if idx not in self._checkpoints_hit:
                self.scoring.corner_cut(self.car_id)

    def _check_pit_speed(self, x, y, speed):
        if self.current_lap == 1:
            near = (abs(x - START_LINE_X) < 50 and START_LINE_Y1 <= y <= START_LINE_Y2)
            if near and speed > PIT_ZONE_MAX_SPEED_CM_S:
                self.scoring.overspeed(self.car_id)

    # ── queries ─────────────────────────────────────────────
    def current_lap_elapsed(self, now):
        return (now - self._lap_start) if self._lap_start else 0.0

    def best_raw(self):
        return min(self._lap_times) if self._lap_times else 0.0

    def get_info(self, now=None):
        return dict(
            car_id=self.car_id, car_name=self.car_name,
            current_lap=self.current_lap, total_laps=TOTAL_LAPS,
            laps_done=self.total_laps_done,
            is_racing=self.is_racing, race_finished=self.race_finished,
            current_lap_elapsed=self.current_lap_elapsed(now or time.time()),
            best_raw=self.best_raw(),
            lap_times=list(self._lap_times),
        )

    def reset(self):
        self.current_lap=0; self.total_laps_done=0
        self.is_racing=False; self.race_finished=False; self.admin_armed=False
        self._side=None; self._lap_start=None; self._last_cross=0.0
        self._checkpoints_hit.clear(); self._lap_times.clear()
        print(f"🔄 {self.car_name} lap engine reset")


class RaceManager:
    """Manages all LapEngines + ScoringEngine, exposes unified API."""

    def __init__(self, scoring_engine):
        self.scoring   = scoring_engine
        self._engines  = {}   # car_id -> LapEngine
        self.race_active     = False
        self.race_start_time = None
        self.race_end_time   = None
        self._admin_armed    = False

    def register_car(self, car_id, car_name):
        self.scoring.register(car_id, car_name)
        self._engines[car_id] = LapEngine(car_id, car_name, self.scoring)
        print(f"🏎️  Registered {car_name}")

    def admin_start(self):
        """Admin hits Start – arm all cars."""
        self._admin_armed = True
        for eng in self._engines.values():
            eng.arm()
        print("🟢 RACE ARMED – waiting for first line crossings")

    def update_car(self, car_id, x, y, speed, now) -> dict | None:
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
                    print(f"🏆 ALL FINISHED – {now-self.race_start_time:.2f}s total")
        return event

    def is_race_active(self): return self.race_active

    def get_car_info(self, car_id, now=None):
        eng = self._engines.get(car_id)
        return eng.get_info(now) if eng else None

    def get_leaderboard(self):
        """Returns ELP-sorted leaderboard from scoring engine."""
        return self.scoring.get_leaderboard()

    def get_legacy_leaderboard(self, now=None):
        """Legacy format used by existing renderer."""
        rows = []
        for cid, eng in self._engines.items():
            info = eng.get_info(now)
            info['total_time'] = sum(info['lap_times'])
            info['car_name']   = eng.car_name
            info['car_id']     = cid
            info['race_finished'] = eng.race_finished
            info['current_lap']   = eng.current_lap
            info['total_laps']    = TOTAL_LAPS
            info['best_lap_time'] = eng.best_raw()
            rows.append(info)
        rows.sort(key=lambda r: (-r['laps_done'], r['total_time']))
        return rows

    def get_all_info(self, now=None):
        return {cid: eng.get_info(now) for cid, eng in self._engines.items()}

    def reset_race(self):
        for eng in self._engines.values():
            eng.reset()
        self.scoring.reset()
        self.race_active=False; self.race_start_time=None; self.race_end_time=None
        self._admin_armed=False
        print("🔄 Race reset – ready")
