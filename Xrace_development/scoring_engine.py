"""Scoring Engine – ELP = Raw Lap Time + penalties - bonuses"""
import time
from collections import defaultdict
from race_config import *

class LapScore:
    def __init__(self, car_id, car_name, lap_number):
        self.car_id=car_id; self.car_name=car_name; self.lap_number=lap_number
        self.raw_time=0.0; self.closed_at=None
        self.wall_hits=0; self.atk_hits=0; self.vic_hits=0
        self.corner_cuts=0; self.overspeed=False; self.voided=False
        self._pen=0.0; self._bon=0.0

    def add_wall_hit(self):
        self.wall_hits+=1; self._pen+=WALL_HIT_PENALTY
        if PRINT_WALL_EVENTS:
            print(f"  🚧 WALL  | {self.car_name} Lap {self.lap_number} +{WALL_HIT_PENALTY}s (penalty={self._pen:.1f}s)")

    def add_attacker_penalty(self):
        self.atk_hits+=1; self._pen+=CAR_COLLISION_ATTACKER_PENALTY
        if PRINT_COLLISION_EVENTS:
            print(f"  🔴 ATK   | {self.car_name} Lap {self.lap_number} +{CAR_COLLISION_ATTACKER_PENALTY}s")

    def add_victim_bonus(self):
        self.vic_hits+=1; self._bon+=CAR_COLLISION_VICTIM_BONUS
        if PRINT_COLLISION_EVENTS:
            print(f"  🟢 VIC   | {self.car_name} Lap {self.lap_number} -{CAR_COLLISION_VICTIM_BONUS}s bonus")

    def add_corner_cut(self):
        self.corner_cuts+=1
        if CORNER_CUT_VOID_LAP:
            self.voided=True
            print(f"  ⛔ VOID  | {self.car_name} Lap {self.lap_number}")
        else:
            self._pen+=CORNER_CUT_PENALTY
            print(f"  🔶 CUT   | {self.car_name} Lap {self.lap_number} +{CORNER_CUT_PENALTY}s")

    def add_overspeed(self):
        if not self.overspeed:
            self.overspeed=True; self._pen+=PIT_ZONE_OVERSPEED_PENALTY
            print(f"  🚨 SPD   | {self.car_name} Lap {self.lap_number} +{PIT_ZONE_OVERSPEED_PENALTY}s")

    @property
    def elp(self):
        return float('inf') if self.voided else max(0.0, self.raw_time+self._pen-self._bon)

    def to_dict(self):
        return dict(car_id=self.car_id,car_name=self.car_name,lap=self.lap_number,
                    raw=round(self.raw_time,3),penalty=round(self._pen,3),
                    bonus=round(self._bon,3),elp=round(self.elp,3),
                    voided=self.voided,closed_at=self.closed_at)


class ScoringEngine:
    def __init__(self):
        self._history=defaultdict(list)
        self._open={}; self._names={}; self._feed=[]

    def register(self,car_id,car_name): self._names[car_id]=car_name

    def open_lap(self,car_id,lap_number):
        self._open[car_id]=LapScore(car_id,self._names.get(car_id,f"Car{car_id}"),lap_number)

    def close_lap(self,car_id,raw_time):
        lap=self._open.pop(car_id,None)
        if lap is None:
            lap=LapScore(car_id,self._names.get(car_id,f"Car{car_id}"),0)
        lap.raw_time=raw_time; lap.closed_at=time.time()
        self._history[car_id].append(lap)
        msg=(f"📊 LAP | {lap.car_name} Lap {lap.lap_number} "
             f"raw={raw_time:.2f}s ELP={lap.elp:.2f}s")
        if PRINT_LAP_EVENTS: print(msg)
        self._feed.append(msg)
        return lap

    def current_lap(self,car_id): return self._open.get(car_id)

    def wall_hit(self,car_id):
        lap=self._open.get(car_id)
        if lap:
            lap.add_wall_hit()
            self._feed.append(f"🚧 WALL {self._names.get(car_id,'?')} Lap {lap.lap_number} +{WALL_HIT_PENALTY}s")

    def car_collision(self,attacker_id,victim_id):
        a=self._open.get(attacker_id); v=self._open.get(victim_id)
        if a: a.add_attacker_penalty()
        if v: v.add_victim_bonus()
        an=self._names.get(attacker_id,'?'); vn=self._names.get(victim_id,'?')
        self._feed.append(f"💥 {an}→{vn} | {an}+{CAR_COLLISION_ATTACKER_PENALTY}s | {vn}-{CAR_COLLISION_VICTIM_BONUS}s")

    def corner_cut(self,car_id):
        lap=self._open.get(car_id)
        if lap: lap.add_corner_cut()

    def overspeed(self,car_id):
        lap=self._open.get(car_id)
        if lap: lap.add_overspeed()

    def best_elp(self,car_id):
        valid=[l.elp for l in self._history.get(car_id,[]) if not l.voided]
        return min(valid) if valid else float('inf')

    def laps_done(self,car_id): return len(self._history.get(car_id,[]))
    def qualifies(self,car_id): return self.laps_done(car_id)>=MIN_LAPS_TO_QUALIFY

    def get_leaderboard(self):
        rows=[]
        for cid,laps in self._history.items():
            valid=[l for l in laps if not l.voided]
            if not valid: continue
            best=min(valid,key=lambda l:(l.elp,l.closed_at or 0))
            rows.append(dict(car_id=cid,car_name=self._names.get(cid,f"Car{cid}"),
                best_elp=best.elp,best_raw=best.raw_time,best_lap=best.lap_number,
                laps_done=len(laps),qualifies=self.qualifies(cid),
                penalty_total=sum(l._pen for l in laps),
                bonus_total=sum(l._bon for l in laps)))
        rows.sort(key=lambda r:(r['best_elp'],r['best_lap']))
        return rows

    def get_car_summary(self,car_id):
        laps=self._history.get(car_id,[]); op=self._open.get(car_id)
        return dict(car_id=car_id,car_name=self._names.get(car_id,f"Car{car_id}"),
                    laps_done=len(laps),best_elp=self.best_elp(car_id),
                    qualifies=self.qualifies(car_id),
                    open_lap=op.to_dict() if op else None,
                    history=[l.to_dict() for l in laps])

    def get_feed(self,n=8): return self._feed[-n:]

    def reset(self):
        self._history.clear(); self._open.clear(); self._feed.clear()
        print("📊 Scoring engine reset")
