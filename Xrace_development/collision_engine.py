"""
Unified Collision Engine
Car-to-car: velocity-based attacker ID, ghost logic, 3-way resolution
Wall:        inner + outer boundary distance, cooldown
Anomaly:     impossible speed flag
"""
import math, time
from collections import defaultdict
from race_config import *


class CollisionEngine:
    def __init__(self, scoring_engine=None, track=None):
        self.scoring   = scoring_engine
        self.track     = track
        self._names    = {}
        self._pos      = {}   # car_id -> (x,y,t)
        self._speeds   = {}   # car_id -> cm/s
        self._laps     = {}
        self._racing   = {}   # car_id -> bool
        self._car_cd   = {}   # frozenset pair -> last t
        self._wall_cd  = {}   # car_id -> last t
        self._ghost_t  = {}   # car_id -> time below threshold
        self._spd_buf  = []   # rolling speed samples
        self.events    = []
        self.anomalies = []

    # ── setup ──────────────────────────────────────────────
    def register(self, car_id, car_name):
        self._names[car_id] = car_name

    def set_track(self, track):   self.track   = track
    def set_scoring(self, eng):   self.scoring = eng

    # ── main update (call every frame) ─────────────────────
    def update(self, cars: dict, now: float) -> list:
        """
        cars = {car_id: {x, y, speed, lap, racing(bool)}}
        Returns list of new event dicts this frame.
        """
        new_evts = []

        # update internal state
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
            # anomaly check
            if spd > MAX_PLAUSIBLE_SPEED_CM_S:
                self._flag_anomaly(cid, spd, now)

        # car-to-car
        racing_ids = [c for c, d in cars.items() if d.get('racing', False)]
        for i in range(len(racing_ids)):
            for j in range(i+1, len(racing_ids)):
                e = self._check_car(racing_ids[i], racing_ids[j], now)
                if e: new_evts.append(e)

        # wall
        for cid, d in cars.items():
            if not d.get('racing', False): continue
            e = self._check_wall(cid, d['x'], d['y'], d.get('lap',0), now)
            if e: new_evts.append(e)

        self.events.extend(new_evts)
        return new_evts

    # ── car-to-car ─────────────────────────────────────────
    def _check_car(self, a, b, now):
        if self._is_ghost(a) or self._is_ghost(b):
            return None
        pa = self._pos.get(a); pb = self._pos.get(b)
        if not pa or not pb: return None

        dist = math.hypot(pa[0]-pb[0], pa[1]-pb[1])
        if dist > CAR_COLLISION_DISTANCE_CM: return None

        key = frozenset([a, b])
        if now - self._car_cd.get(key, 0) < CAR_COLLISION_COOLDOWN: return None
        self._car_cd[key] = now

        # identify attacker (faster car)
        sa = self._speeds.get(a, 0); sb = self._speeds.get(b, 0)
        if abs(sa-sb) >= SPEED_DIFF_THRESHOLD:
            attacker, victim = (a,b) if sa>=sb else (b,a)
        else:
            attacker, victim = a, b

        an = self._names.get(attacker, f"Car{attacker}")
        vn = self._names.get(victim,   f"Car{victim}")
        lap = self._laps.get(attacker, 0)

        if self.scoring:
            self.scoring.car_collision(attacker, victim)

        if PRINT_COLLISION_EVENTS:
            print(f"💥 CAR | {an} → {vn}  dist={dist:.1f}cm  Lap{lap}")

        return dict(type='car', attacker=attacker, victim=victim,
                    attacker_name=an, victim_name=vn,
                    dist=dist, lap=lap, time=now)

    # ── wall ───────────────────────────────────────────────
    def _check_wall(self, cid, x, y, lap, now):
        if not self.track or not self.track.has_width(): return None
        if now - self._wall_cd.get(cid, 0) < WALL_COLLISION_COOLDOWN: return None

        od = self._dist_boundary(x, y, self.track.get_outer_points())
        id_ = self._dist_boundary(x, y, self.track.get_inner_points())

        wall = None
        if od <= WALL_TOLERANCE_CM:  wall = 'outer'
        elif id_ <= WALL_TOLERANCE_CM: wall = 'inner'
        if not wall: return None

        self._wall_cd[cid] = now
        name = self._names.get(cid, f"Car{cid}")

        if self.scoring:
            self.scoring.wall_hit(cid)

        if PRINT_WALL_EVENTS:
            print(f"🚧 WALL | {name} hit {wall.upper()} wall  Lap{lap}")

        return dict(type='wall', car_id=cid, car_name=name,
                    wall=wall, lap=lap, time=now)

    # ── ghost logic ────────────────────────────────────────
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

    # ── anomaly ────────────────────────────────────────────
    def _flag_anomaly(self, cid, spd, now):
        name = self._names.get(cid, f"Car{cid}")
        rec  = dict(car_id=cid, name=name, speed=spd, time=now)
        self.anomalies.append(rec)
        if PRINT_ANOMALIES:
            print(f"⚠️  ANOMALY | {name} speed={spd:.0f}cm/s ({spd*0.036:.1f}km/h) – flagged")

    # ── geometry ───────────────────────────────────────────
    @staticmethod
    def _dist_boundary(px, py, pts):
        if not pts or len(pts) < 2: return float('inf')
        best = float('inf')
        for i in range(len(pts)):
            x1,y1=pts[i]; x2,y2=pts[(i+1)%len(pts)]
            dx,dy=x2-x1,y2-y1; denom=dx*dx+dy*dy
            if denom == 0:
                d=math.hypot(px-x1,py-y1)
            else:
                t=max(0,min(1,((px-x1)*dx+(py-y1)*dy)/denom))
                d=math.hypot(px-x1-t*dx, py-y1-t*dy)
            best=min(best,d)
        return best

    # ── queries ────────────────────────────────────────────
    def wall_hits(self, cid):
        return [e for e in self.events if e['type']=='wall' and e['car_id']==cid]

    def car_events(self, cid):
        return [e for e in self.events if e['type']=='car'
                and (e['attacker']==cid or e['victim']==cid)]

    def reset(self):
        self.events.clear(); self.anomalies.clear()
        self._car_cd.clear(); self._wall_cd.clear(); self._ghost_t.clear()
        self._spd_buf.clear()
        print("✓ Collision engine reset")
