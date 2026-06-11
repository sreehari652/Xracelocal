#!/usr/bin/env python3
"""
race_gui.py  —  UWB Racing  |  Native PyQt6 Dashboard
======================================================
Standalone replacement for the browser+WebSocket display.
• Reads UDP directly (same logic as ws_bridge.py)
• Renders at ~30 fps via QTimer — zero network hop
• Fetches tournament list & structure from Django API
• POSTs lap data to /api/record-lap/ after each lap
• POSTs group-live / group-finished to server

Run:
    pip install PyQt6
    python race_gui.py
"""

import sys, socket, json, math, time, threading, urllib.request, urllib.error
from collections import defaultdict, deque
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QFrame, QScrollArea, QSizePolicy, QHeaderView,
    QProgressBar, QGroupBox, QDialog, QDialogButtonBox,
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QRectF, QPointF, QSize,
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QLinearGradient, QRadialGradient, QFontDatabase, QPalette,
)

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

UDP_PORT        = 4210
DJANGO_API_BASE = 'https://xraceapi.zyberspace.in/api'

ANCHOR_POSITIONS = {0: (0,0), 1: (610,0), 2: (610,440), 3: (0,440)}
ANCHOR_COUNT = 4
TAG_COUNT    = 6
MIN_RANGE_CM = 10
MAX_RANGE_CM = 1450
TAG_TIMEOUT  = 5
TRAIL_LENGTH = 40

# Penalty defaults (overridden by tournament structure)
TOTAL_LAPS_DEFAULT                     = 10
WALL_HIT_PENALTY_DEFAULT               = 5.0
CAR_COLLISION_ATTACKER_PENALTY_DEFAULT = 5.0
CAR_COLLISION_VICTIM_BONUS_DEFAULT     = 2.0

MIN_LAP_TIME       = 3.0
MIN_LAPS_QUALIFY   = 3
LINE_CROSS_TOL     = 8
LINE_Y_TOL         = 30
CAR_COLL_DIST      = 25
CAR_COLL_COOLDOWN  = 1.0
WALL_TOL_CM        = 5.0
WALL_COLL_COOLDOWN = 0.5
SPEED_DIFF_THRESH  = 10.0
MAX_SPEED_CM_S     = 2800

# Car colours (one per tag id 0-5)
CAR_COLORS = [
    "#FF4444", "#44AAFF", "#44FF88", "#FFB344",
    "#CC44FF", "#FF44CC",
]

# ══════════════════════════════════════════════════════════════════════
# GLOBAL RACE STATE  (shared between UDP thread & GUI thread)
# ══════════════════════════════════════════════════════════════════════

g_lock = threading.Lock()

g_cfg = dict(
    total_laps   = TOTAL_LAPS_DEFAULT,
    wall_pen     = WALL_HIT_PENALTY_DEFAULT,
    atk_pen      = CAR_COLLISION_ATTACKER_PENALTY_DEFAULT,
    vic_bon      = CAR_COLLISION_VICTIM_BONUS_DEFAULT,
    sf_x         = 490.0,
    sf_y1        = 300.0,
    sf_y2        = 340.0,
    sf_dir       = 'left_to_right',
    checkpoints  = [],   # [(x,y,r), ...]
    track_outer  = [],
    track_inner  = [],
    track_center = [],
)

g_tags = {i: dict(
    id=i, name=f"Car{i}", x=0.0, y=0.0, active=False,
    last_update=0.0, quality='unknown', anchor_count=0,
    speed_cms=0.0, max_speed=0.0,
    trail=deque(maxlen=TRAIL_LENGTH),
    last_ranges=[0]*ANCHOR_COUNT,
    pkt_total=0, pkt_accepted=0, pkt_rejected=0,
    _prev=None,
) for i in range(TAG_COUNT)}

g_lap_engines  = {}   # tag_id → LapEngineState
g_scoring      = {}   # tag_id → ScoringState
g_leaderboard  = []
g_feed         = deque(maxlen=50)
g_events       = deque(maxlen=100)
g_race_active  = False
g_race_armed   = False
g_group_id     = None
g_tag_to_gp    = {}   # tag_id(int) → gp_id(int)
g_cp_touches   = {}   # cp_idx → [{"car_name":..,"lap":..}, ...]
g_udp_running  = False
g_start_time   = datetime.now()

# ══════════════════════════════════════════════════════════════════════
# TRACK CSV PARSER
# ══════════════════════════════════════════════════════════════════════

def parse_track_csv(csv_text: str) -> dict:
    result = dict(center=[], inner=[], outer=[], checkpoints={},
                  sf_x=490.0, sf_y1=300.0, sf_y2=340.0, sf_dir='left_to_right')
    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2: continue
        kind = parts[0].upper()
        try:
            if kind == 'CENTER' and len(parts) >= 3:
                result['center'].append((float(parts[1]), float(parts[2])))
            elif kind == 'INNER' and len(parts) >= 3:
                result['inner'].append((float(parts[1]), float(parts[2])))
            elif kind == 'OUTER' and len(parts) >= 3:
                result['outer'].append((float(parts[1]), float(parts[2])))
            elif kind == 'START_FINISH' and len(parts) >= 5:
                x1,y1 = float(parts[1]),float(parts[2])
                x2,y2 = float(parts[3]),float(parts[4])
                result['sf_x']  = (x1+x2)/2
                result['sf_y1'] = min(y1,y2); result['sf_y2'] = max(y1,y2)
                if len(parts) >= 6: result['sf_dir'] = parts[5].lower()
            elif kind == 'CHECKPOINT' and len(parts) >= 5:
                cp_id = int(parts[1])
                result['checkpoints'][cp_id] = (float(parts[2]),float(parts[3]),float(parts[4]))
        except: pass
    result['checkpoints'] = [result['checkpoints'][k]
                             for k in sorted(result['checkpoints'].keys())]
    return result

# ══════════════════════════════════════════════════════════════════════
# POSITIONING  (raw trilateration)
# ══════════════════════════════════════════════════════════════════════

def valid_anchors(ranges, ap):
    out = []
    for i, r in enumerate(ranges):
        if r <= 0 or i not in ap: continue
        if r < MIN_RANGE_CM or r > MAX_RANGE_CM: continue
        out.append({'id':i,'range':r,'x':ap[i][0],'y':ap[i][1]})
    return out

def tri3(a1,a2,a3):
    x1,y1,r1=a1['x'],a1['y'],a1['range']
    x2,y2,r2=a2['x'],a2['y'],a2['range']
    x3,y3,r3=a3['x'],a3['y'],a3['range']
    A=2*(x2-x1);B=2*(y2-y1);C=r1**2-r2**2-x1**2+x2**2-y1**2+y2**2
    D=2*(x3-x2);E=2*(y3-y2);F=r2**2-r3**2-x2**2+x3**2-y2**2+y3**2
    den=A*E-B*D
    if abs(den)<0.001:
        d=math.hypot(x2-x1,y2-y1); ratio=r1/(r1+r2) if (r1+r2)>0 else 0.5
        return x1+(x2-x1)*ratio, y1+(y2-y1)*ratio
    return (C*E-F*B)/den,(A*F-C*D)/den

def multilat(va):
    combos=[]
    for i in range(len(va)):
        for j in range(i+1,len(va)):
            for k in range(j+1,len(va)):
                px,py=tri3(va[i],va[j],va[k]); combos.append((px,py))
    if not combos: return None
    return sum(c[0] for c in combos)/len(combos), sum(c[1] for c in combos)/len(combos)

def calculate_position(ranges, ap):
    va=valid_anchors(ranges,ap); nv=len(va)
    if nv>=4: pos=multilat(va); q='excellent'
    elif nv==3: pos=tri3(*va[:3]); q='good'
    elif nv==2:
        a1,a2=va[0],va[1]
        ratio=a1['range']/(a1['range']+a2['range']) if (a1['range']+a2['range'])>0 else 0.5
        pos=(a1['x']+(a2['x']-a1['x'])*ratio, a1['y']+(a2['y']-a1['y'])*ratio); q='fair'
    else: return None,'poor',nv
    if pos is None: return None,q,nv
    return (pos[0],pos[1]),q,nv

def reorder_by_ancid(slot_ranges, ancid, n=ANCHOR_COUNT):
    if not ancid or not any(a>=0 for a in ancid):
        return [float(r) for r in slot_ranges[:n]]
    out=[0.0]*n
    for slot,anc in enumerate(ancid):
        if 0<=anc<n and slot<len(slot_ranges): out[anc]=float(slot_ranges[slot])
    return out

# ══════════════════════════════════════════════════════════════════════
# LAP ENGINE STATE
# ══════════════════════════════════════════════════════════════════════

class LapEng:
    """Per-car lap tracking (runs in UDP thread, lock-protected)."""
    def __init__(self, cid, name):
        self.cid=cid; self.name=name
        self.is_racing=False; self.race_finished=False
        self.current_lap=0; self.laps_done=0
        self._lap_start=None; self._last_cross=0.0
        self._next_cp=0; self._sf_side=None
        self.lap_times=[]; self.cp_hits_this_lap=[]
        # scoring accumulators for current open lap
        self._pen=0.0; self._bon=0.0
        self.wall_hits=0; self.atk_hits=0; self.vic_hits=0
        self.corner_cuts=0

    def reset(self):
        self.__init__(self.cid, self.name)

    def arm(self):
        self.reset()

    def _on_line(self, y, cfg):
        return (cfg['sf_y1']-LINE_Y_TOL) <= y <= (cfg['sf_y2']+LINE_Y_TOL)

    def update(self, x, y, now, cfg, scoring_cb, lap_done_cb):
        """Call with lock held. Returns event dict or None."""
        cp_ev = self._check_cp(x, y, now, cfg) if self.is_racing else None
        sf_ev = self._check_sf(x, y, now, cfg, scoring_cb, lap_done_cb)
        return sf_ev or cp_ev

    def _check_sf(self, x, y, now, cfg, scoring_cb, lap_done_cb):
        tol=LINE_CROSS_TOL; sfx=cfg['sf_x']
        if   x < sfx-tol: new_side='left'
        elif x > sfx+tol: new_side='right'
        else: return None

        if self._sf_side is None: self._sf_side=new_side; return None
        prev=self._sf_side; self._sf_side=new_side
        crossing=(prev=='left' and new_side=='right') if cfg['sf_dir']=='left_to_right' \
                 else (prev=='right' and new_side=='left')
        if not crossing: return None
        if not self._on_line(y,cfg): return None
        if now-self._last_cross < MIN_LAP_TIME: return None
        self._last_cross=now
        return self._process_cross(now, cfg, scoring_cb, lap_done_cb)

    def _process_cross(self, now, cfg, scoring_cb, lap_done_cb):
        if not self.is_racing:
            self.is_racing=True; self.current_lap=1
            self._lap_start=now; self._next_cp=0; self.cp_hits_this_lap=[]
            self._pen=self._bon=0.0
            self.wall_hits=self.atk_hits=self.vic_hits=self.corner_cuts=0
            return dict(type='race_start',car_id=self.cid,car_name=self.name,lap=1,time=now)

        cps=cfg['checkpoints']
        if self._next_cp < len(cps):
            self._next_cp=0; self.cp_hits_this_lap=[]
            return None  # voided

        raw=now-self._lap_start
        lap_n=self.current_lap
        pen=self._pen; bon=self._bon
        wh=self.wall_hits; ah=self.atk_hits; vh=self.vic_hits; cc=self.corner_cuts
        elp=max(0.0, raw+pen-bon)

        self.laps_done+=1; self.lap_times.append(raw)
        self._next_cp=0; self.cp_hits_this_lap=[]
        self._pen=self._bon=0.0
        self.wall_hits=self.atk_hits=self.vic_hits=self.corner_cuts=0

        lap_done_cb(self.cid, lap_n, raw, elp, pen, bon, wh, ah, vh, cc)

        ev_type='race_finish' if self.laps_done>=cfg['total_laps'] else 'lap_done'
        if ev_type=='race_finish': self.is_racing=False; self.race_finished=True
        else:
            self.current_lap+=1; self._lap_start=now

        return dict(type=ev_type, car_id=self.cid, car_name=self.name,
                    lap=lap_n, raw_time=raw, elp=elp, time=now)

    def _check_cp(self, x, y, now, cfg):
        cps=cfg['checkpoints']
        if self._next_cp>=len(cps): return None
        cx,cy,cr=cps[self._next_cp]
        if math.hypot(x-cx,y-cy)<=cr:
            idx=self._next_cp; self._next_cp+=1
            self.cp_hits_this_lap.append(idx)
            return dict(type='checkpoint',car_id=self.cid,car_name=self.name,
                        cp_index=idx,total=len(cps),time=now)
        return None

    def elapsed(self, now):
        return (now-self._lap_start) if self._lap_start else 0.0

    def best_raw(self):
        return min(self.lap_times) if self.lap_times else 0.0

    def open_lap_elp(self, now):
        el=self.elapsed(now)
        return max(0.0, el+self._pen-self._bon)

# ══════════════════════════════════════════════════════════════════════
# API  (all calls in background threads)
# ══════════════════════════════════════════════════════════════════════

def api_get(path):
    url = f"{DJANGO_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={'Accept':'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[API GET] {path} → {e}")
        return None

def api_post(path, body: dict, cb=None):
    def _go():
        url=f"{DJANGO_API_BASE}{path}"
        try:
            data=json.dumps(body).encode()
            req=urllib.request.Request(url,data=data,
                headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(req,timeout=8) as r:
                resp=json.loads(r.read().decode())
                if cb: cb(True, resp)
        except urllib.error.HTTPError as e:
            err=e.read().decode(errors='replace')
            print(f"[API POST] {path} HTTP {e.code}: {err[:200]}")
            if cb: cb(False, {'error': str(e)})
        except Exception as e:
            print(f"[API POST] {path} {e}")
            if cb: cb(False, {'error': str(e)})
    threading.Thread(target=_go, daemon=True).start()

def api_patch(path, body: dict, cb=None):
    def _go():
        url=f"{DJANGO_API_BASE}{path}"
        try:
            data=json.dumps(body).encode()
            req=urllib.request.Request(url,data=data,
                headers={'Content-Type':'application/json'},method='PATCH')
            with urllib.request.urlopen(req,timeout=8) as r:
                resp=json.loads(r.read().decode())
                if cb: cb(True, resp)
        except Exception as e:
            print(f"[API PATCH] {path} {e}")
            if cb: cb(False, {'error': str(e)})
    threading.Thread(target=_go, daemon=True).start()

def post_lap(tag_id, lap_number, raw, elp, pen, bon, wh, ah, vh, cc):
    gp = g_tag_to_gp.get(tag_id)
    if not gp:
        print(f"[LAP] tag {tag_id} not in tag_to_gp — skip post"); return
    api_post('/record-lap/', dict(
        gp_id=gp, lap_number=lap_number,
        raw_time=round(raw,3), elp_time=round(elp,3),
        penalty=round(pen,3), bonus=round(bon,3),
        wall_hits=wh, atk_hits=ah, vic_hits=vh, corner_cuts=cc, voided=False,
    ))
    print(f"[LAP POST] tag={tag_id} gp={gp} lap={lap_number} raw={raw:.2f} elp={elp:.2f}")

# ══════════════════════════════════════════════════════════════════════
# COLLISION ENGINE  (lock-protected, called from UDP thread)
# ══════════════════════════════════════════════════════════════════════

_car_cd  = {}
_wall_cd = {}

def check_collisions(active_cars, now, cfg):
    """active_cars: list of (tag_id, x, y, speed_cms, lap_eng)"""
    events = []
    racing = [(t,x,y,s,e) for t,x,y,s,e in active_cars if e.is_racing]

    # car–car
    for i in range(len(racing)):
        for j in range(i+1, len(racing)):
            ta,xa,ya,sa,ea = racing[i]
            tb,xb,yb,sb,eb = racing[j]
            dist = math.hypot(xa-xb, ya-yb)
            if dist > CAR_COLL_DIST: continue
            key = frozenset([ta,tb])
            if now - _car_cd.get(key,0) < CAR_COLL_COOLDOWN: continue
            _car_cd[key] = now
            if abs(sa-sb)>=SPEED_DIFF_THRESH and sa>=sb: atk,vic,ea2,eb2=ta,tb,ea,eb
            elif abs(sa-sb)>=SPEED_DIFF_THRESH: atk,vic,ea2,eb2=tb,ta,eb,ea
            else: atk,vic,ea2,eb2=ta,tb,ea,eb
            ea2._pen += cfg['atk_pen']; ea2.atk_hits += 1
            eb2._bon += cfg['vic_bon']; eb2.vic_hits += 1
            msg = f"💥 {g_tags[atk]['name']}→{g_tags[vic]['name']}  +{cfg['atk_pen']}s / -{cfg['vic_bon']}s"
            events.append(('event', msg))
            print(msg)

    # wall
    outer = cfg['track_outer']; inner_pts = cfg['track_inner']
    if outer and inner_pts:
        for ta,xa,ya,sa,ea in racing:
            if not ea.is_racing: continue
            if now - _wall_cd.get(ta,0) < WALL_COLL_COOLDOWN: continue
            od = dist_to_boundary(xa,ya,outer)
            id_ = dist_to_boundary(xa,ya,inner_pts)
            wall = 'outer' if od<=WALL_TOL_CM else ('inner' if id_<=WALL_TOL_CM else None)
            if not wall: continue
            _wall_cd[ta]=now
            ea._pen += cfg['wall_pen']; ea.wall_hits += 1
            msg = f"🚧 WALL {g_tags[ta]['name']} {wall}  +{cfg['wall_pen']}s"
            events.append(('event', msg))
            print(msg)

    return events

def dist_to_boundary(px, py, pts):
    if not pts or len(pts)<2: return float('inf')
    best=float('inf'); n=len(pts)
    for i in range(n):
        x1,y1=pts[i]; x2,y2=pts[(i+1)%n]
        dx,dy=x2-x1,y2-y1; den=dx*dx+dy*dy
        if den==0: d=math.hypot(px-x1,py-y1)
        else:
            t=max(0,min(1,((px-x1)*dx+(py-y1)*dy)/den))
            d=math.hypot(px-x1-t*dx,py-y1-t*dy)
        best=min(best,d)
    return best

# ══════════════════════════════════════════════════════════════════════
# UDP THREAD
# ══════════════════════════════════════════════════════════════════════

def udp_thread_func(event_queue):
    global g_udp_running, g_race_active
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT)); sock.settimeout(0.1)
    print(f"[UDP] Listening on :{UDP_PORT}")

    while g_udp_running:
        try:
            data, _ = sock.recvfrom(2048)
            try: uwb = json.loads(data.decode('utf-8', errors='ignore').strip())
            except: continue

            if 'id' not in uwb or 'range' not in uwb: continue
            tid = int(uwb['id'])
            if tid not in g_tags: continue
            slot_ranges = uwb['range']
            if not isinstance(slot_ranges, list) or len(slot_ranges) < ANCHOR_COUNT: continue

            ancid      = uwb.get('ancid', [])
            raw_ranges = reorder_by_ancid(slot_ranges, ancid, ANCHOR_COUNT)
            now        = time.time()

            pos, quality, anc_count = calculate_position(raw_ranges, ANCHOR_POSITIONS)
            if pos is None: continue

            rx, ry = pos

            with g_lock:
                tag = g_tags[tid]
                tag['pkt_total'] += 1

                # speed
                prev = tag['_prev']
                if prev:
                    dt = now - prev[2]
                    if dt > 0:
                        tag['speed_cms'] = math.hypot(rx-prev[0], ry-prev[1]) / dt
                        tag['max_speed'] = max(tag['max_speed'], tag['speed_cms'])
                tag['_prev'] = (rx, ry, now)

                tag['x']=rx; tag['y']=ry
                tag['quality']=quality; tag['anchor_count']=anc_count
                tag['active']=True; tag['last_update']=now
                tag['trail'].append((rx, ry))
                tag['last_ranges']=[int(r) for r in raw_ranges]
                tag['pkt_accepted'] += 1

                # lap engine
                cfg = g_cfg
                if not g_race_armed: continue

                eng = g_lap_engines.get(tid)
                if eng is None: continue

                def lap_done_cb(cid, lap_n, raw, elp, pen, bon, wh, ah, vh, cc):
                    post_lap(cid, lap_n, raw, elp, pen, bon, wh, ah, vh, cc)
                    msg = f"📊 LAP  {g_tags[cid]['name']}  #{lap_n}  ELP={elp:.2f}s  raw={raw:.2f}s"
                    g_feed.appendleft(msg)
                    event_queue.append(('lap', dict(car_id=cid, lap=lap_n, elp=elp, raw=raw)))

                def scoring_cb(*a): pass

                ev = eng.update(rx, ry, now, cfg, scoring_cb, lap_done_cb)
                if ev:
                    if ev['type']=='race_start': g_race_active=True
                    elif ev['type']=='race_finish':
                        if all(e.race_finished for e in g_lap_engines.values()):
                            g_race_active=False
                    msg = _ev_to_msg(ev)
                    if msg: g_feed.appendleft(msg)
                    event_queue.append(('event', ev))

                # checkpoints touch history
                if ev and ev['type']=='checkpoint':
                    idx=ev['cp_index']
                    if idx not in g_cp_touches: g_cp_touches[idx]=[]
                    g_cp_touches[idx].append({'car_name':ev['car_name'],'lap':ev.get('lap',0)})

                # collision
                active = []
                for t2id, t2 in g_tags.items():
                    if t2['active'] and (now-t2['last_update'])<TAG_TIMEOUT:
                        e2 = g_lap_engines.get(t2id)
                        if e2: active.append((t2id, t2['x'], t2['y'], t2['speed_cms'], e2))
                coll_evts = check_collisions(active, now, cfg)
                for ev2 in coll_evts:
                    g_feed.appendleft(ev2[1])
                    event_queue.append(ev2)

        except socket.timeout: continue
        except Exception as e:
            if g_udp_running: print(f"[UDP] {e}")
    sock.close(); print("[UDP] Stopped")

def _ev_to_msg(ev):
    t=ev.get('type','')
    n=ev.get('car_name','?')
    if t=='race_start':   return f"🏁 START  {n}  Lap 1"
    if t=='lap_done':     return f"🔄 LAP    {n}  #{ev['lap']}  ELP={ev['elp']:.2f}s"
    if t=='race_finish':  return f"🏆 FINISH  {n}  {ev['lap']} laps"
    if t=='checkpoint':   return f"✔ CP{ev['cp_index']}  {n}"
    return None

# ══════════════════════════════════════════════════════════════════════
# TRACK CANVAS  (QWidget with QPainter)
# ══════════════════════════════════════════════════════════════════════

TRACK_W, TRACK_H = 610, 440   # UWB coordinate space

class TrackCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background:#0a0a12;")
        self._scale = 1.0; self._ox = 0.0; self._oy = 0.0

    def _compute_transform(self, W, H):
        margin = 32
        sx = (W - 2*margin) / TRACK_W
        sy = (H - 2*margin) / TRACK_H
        self._scale = min(sx, sy)
        self._ox = (W - TRACK_W*self._scale) / 2
        self._oy = (H - TRACK_H*self._scale) / 2

    def tp(self, x, y):
        return QPointF(self._ox + x*self._scale,
                       self._oy + y*self._scale)

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        self._compute_transform(W, H)
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background gradient
        bg = QLinearGradient(0,0,0,H)
        bg.setColorAt(0, QColor("#0a0a14")); bg.setColorAt(1, QColor("#060610"))
        p.fillRect(0,0,W,H, QBrush(bg))

        with g_lock:
            cfg   = dict(g_cfg)
            tags  = {tid: dict(t) for tid, t in g_tags.items()}
            engs  = {tid: (e.current_lap, e.laps_done, e.is_racing,
                           list(e.cp_hits_this_lap), e._next_cp)
                     for tid,e in g_lap_engines.items()}

        self._draw_grid(p)
        self._draw_track(p, cfg)
        self._draw_sf_line(p, cfg)
        self._draw_checkpoints(p, cfg)
        self._draw_anchors(p)
        self._draw_cars(p, tags, engs, cfg)
        p.end()

    def _draw_grid(self, p):
        pen = QPen(QColor(255,255,255,12)); pen.setWidth(1); p.setPen(pen)
        for gx in range(0, TRACK_W+1, 100):
            a = self.tp(gx,0); b = self.tp(gx,TRACK_H)
            p.drawLine(a, b)
        for gy in range(0, TRACK_H+1, 80):
            a = self.tp(0,gy); b = self.tp(TRACK_W,gy)
            p.drawLine(a, b)

    def _draw_track(self, p, cfg):
        def draw_poly(pts, color, width, dashed=False):
            if len(pts)<2: return
            pen=QPen(QColor(color)); pen.setWidthF(width)
            if dashed: pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            path=QPainterPath()
            path.moveTo(self.tp(*pts[0]))
            for pt in pts[1:]: path.lineTo(self.tp(*pt))
            path.closeSubpath(); p.drawPath(path)

        # Fill between outer and inner
        outer=cfg['track_outer']; inner=cfg['track_inner']
        if outer and inner and len(outer)>2 and len(inner)>2:
            track_path=QPainterPath()
            track_path.moveTo(self.tp(*outer[0]))
            for pt in outer[1:]: track_path.lineTo(self.tp(*pt))
            track_path.closeSubpath()
            inner_path=QPainterPath()
            inner_path.moveTo(self.tp(*inner[0]))
            for pt in inner[1:]: inner_path.lineTo(self.tp(*pt))
            inner_path.closeSubpath()
            road=track_path.subtracted(inner_path)
            p.fillPath(road, QBrush(QColor(30,32,50,180)))
            draw_poly(outer, "#3355AA", 2.5)
            draw_poly(inner, "#223388", 2.0)
        elif outer:
            draw_poly(outer, "#3355AA", 2.5)

        # Center line
        center=cfg['track_center']
        if center and len(center)>2:
            draw_poly(center, "#FFFFFF22", 1.5, dashed=True)

    def _draw_sf_line(self, p, cfg):
        sfx=cfg['sf_x']; y1=cfg['sf_y1']; y2=cfg['sf_y2']
        a=self.tp(sfx,y1); b=self.tp(sfx,y2)
        pen=QPen(QColor("#FFDD00")); pen.setWidthF(3)
        pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
        p.drawLine(a, b)
        # Label
        lbl_pt=self.tp(sfx+6, (y1+y2)/2)
        p.setPen(QColor("#FFDD00"))
        p.setFont(QFont("Courier New",8,QFont.Weight.Bold))
        p.drawText(lbl_pt, "S/F")

    def _draw_checkpoints(self, p, cfg):
        cps=cfg['checkpoints']
        for i,(cx,cy,cr) in enumerate(cps):
            pt=self.tp(cx,cy); r=cr*self._scale
            pen=QPen(QColor(0,255,160,120)); pen.setWidthF(1.5); p.setPen(pen)
            p.setBrush(QBrush(QColor(0,255,160,20)))
            p.drawEllipse(pt, r, r)
            p.setPen(QColor(0,220,130,180))
            p.setFont(QFont("Courier New",7))
            p.drawText(QPointF(pt.x()+r+2, pt.y()+4), str(i))

    def _draw_anchors(self, p):
        for aid, (ax,ay) in ANCHOR_POSITIONS.items():
            pt=self.tp(ax,ay)
            p.setPen(QPen(QColor("#FF8800"), 1))
            p.setBrush(QBrush(QColor("#FF880088")))
            p.drawRect(int(pt.x())-5, int(pt.y())-5, 10, 10)
            p.setPen(QColor("#FF8800"))
            p.setFont(QFont("Courier New",7,QFont.Weight.Bold))
            p.drawText(QPointF(pt.x()+6, pt.y()-2), f"A{aid}")

    def _draw_cars(self, p, tags, engs, cfg):
        now=time.time()
        for tid, tag in tags.items():
            if not tag['active'] or (now-tag['last_update'])>TAG_TIMEOUT: continue
            color=QColor(CAR_COLORS[tid % len(CAR_COLORS)])
            trail=list(tag['trail'])

            # Trail
            if len(trail)>1:
                for k in range(1,len(trail)):
                    alpha=int(180*(k/len(trail)))
                    tc=QColor(color); tc.setAlpha(alpha)
                    pen=QPen(tc); pen.setWidthF(2.0); p.setPen(pen)
                    a=self.tp(*trail[k-1]); b=self.tp(*trail[k])
                    p.drawLine(a,b)

            # Car dot
            pt=self.tp(tag['x'],tag['y']); r=8*self._scale
            grad=QRadialGradient(pt, r*1.5)
            grad.setColorAt(0, color); grad.setColorAt(1, QColor(color.red()//3, color.green()//3, color.blue()//3, 0))
            p.setBrush(QBrush(grad)); p.setPen(QPen(color, 1.5))
            p.drawEllipse(pt, r, r)

            # Label
            eng_info=engs.get(tid)
            lap_str = f"L{eng_info[0]}" if eng_info and eng_info[2] else ""
            name=tag['name']
            spd=tag['speed_cms']*0.036
            label=f"{name}  {spd:.0f}km/h  {lap_str}"
            p.setPen(QColor("#FFFFFF"))
            p.setFont(QFont("Courier New",8,QFont.Weight.Bold))
            p.drawText(QPointF(pt.x()+r+3, pt.y()-4), label)

            # CP progress arc
            if eng_info and eng_info[2]:
                total_cps=len(cfg['checkpoints'])
                if total_cps>0:
                    done=eng_info[4]
                    frac=done/total_cps
                    pen=QPen(QColor(0,255,160,180)); pen.setWidthF(2); p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    arc_r=r+5
                    p.drawArc(QRectF(pt.x()-arc_r, pt.y()-arc_r, arc_r*2, arc_r*2),
                              90*16, -int(360*16*frac))

# ══════════════════════════════════════════════════════════════════════
# LEADERBOARD WIDGET
# ══════════════════════════════════════════════════════════════════════

class LeaderboardWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(4)
        title=QLabel("LEADERBOARD"); title.setStyleSheet(
            "color:#FFDD00;font-family:'Courier New';font-size:13px;font-weight:bold;"
            "padding:4px 8px;background:#111122;border-bottom:1px solid #333366;")
        layout.addWidget(title)
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(["#","Car","Best ELP","Laps","Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setStyleSheet("""
            QTableWidget{background:#0c0c1a;color:#E0E0FF;
                gridline-color:#1a1a33;border:none;font-family:'Courier New';font-size:11px;}
            QHeaderView::section{background:#111133;color:#8888BB;
                border:none;padding:4px;font-size:10px;}
            QTableWidget::item{padding:3px 6px;}
        """)
        layout.addWidget(self.table)

        # Per-car elapsed
        self.elapsed_labels={}
        elapsed_frame=QFrame(); elapsed_frame.setStyleSheet("background:#0c0c1a;")
        ef_lay=QVBoxLayout(elapsed_frame); ef_lay.setContentsMargins(4,4,4,4); ef_lay.setSpacing(2)
        lbl=QLabel("CURRENT LAP"); lbl.setStyleSheet("color:#888;font-family:'Courier New';font-size:10px;")
        ef_lay.addWidget(lbl)
        for tid in range(TAG_COUNT):
            row=QHBoxLayout()
            color=CAR_COLORS[tid%len(CAR_COLORS)]
            dot=QLabel("●"); dot.setStyleSheet(f"color:{color};font-size:14px;")
            name=QLabel(f"Car{tid}"); name.setStyleSheet(
                "color:#aaa;font-family:'Courier New';font-size:10px;"); name.setFixedWidth(42)
            val=QLabel("—"); val.setStyleSheet(
                "color:#EEE;font-family:'Courier New';font-size:11px;font-weight:bold;")
            self.elapsed_labels[tid]=(name, val)
            row.addWidget(dot); row.addWidget(name); row.addWidget(val); row.addStretch()
            ef_lay.addLayout(row)
        layout.addWidget(elapsed_frame)

    def refresh(self):
        now=time.time()
        with g_lock:
            engs=list(g_lap_engines.items())
            tags_snap={tid:dict(t) for tid,t in g_tags.items()}

        # Leaderboard rows
        rows=[]
        for tid, eng in engs:
            if eng.laps_done==0 and not eng.is_racing: continue
            best_elp=None
            if eng.lap_times:
                best_elp=min(eng.lap_times)   # raw; penalty stored separately in API
            rows.append(dict(
                car_id=tid, car_name=g_tags[tid]['name'],
                laps_done=eng.laps_done, best_elp=best_elp,
                racing=eng.is_racing, finished=eng.race_finished))
        rows.sort(key=lambda r: (r['best_elp'] if r['best_elp'] else float('inf'), r['laps_done']*-1))

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            color=QColor(CAR_COLORS[r['car_id']%len(CAR_COLORS)])
            items=[
                QTableWidgetItem(str(i+1)),
                QTableWidgetItem(r['car_name']),
                QTableWidgetItem(f"{r['best_elp']:.3f}s" if r['best_elp'] else "—"),
                QTableWidgetItem(str(r['laps_done'])),
                QTableWidgetItem("🏆" if r['finished'] else ("🏎" if r['racing'] else "⏳")),
            ]
            for item in items:
                item.setForeground(QBrush(color if i==0 else QColor("#CCCCEE")))
                self.table.setItem(i, items.index(item), item)

        # Elapsed
        for tid, (name_lbl, val_lbl) in self.elapsed_labels.items():
            eng=g_lap_engines.get(tid)
            tag=tags_snap.get(tid,{})
            name_lbl.setText(g_tags[tid]['name'])
            if eng and eng.is_racing:
                el=eng.elapsed(now)
                pen=eng._pen; bon=eng._bon
                elp=max(0.0, el+pen-bon)
                val_lbl.setText(f"{elp:.1f}s")
                val_lbl.setStyleSheet("color:#44FF88;font-family:'Courier New';font-size:11px;font-weight:bold;")
            else:
                val_lbl.setText("—")
                val_lbl.setStyleSheet("color:#555;font-family:'Courier New';font-size:11px;")

# ══════════════════════════════════════════════════════════════════════
# CHECKPOINT PROGRESS WIDGET
# ══════════════════════════════════════════════════════════════════════

class CheckpointWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(2)
        title=QLabel("CHECKPOINT PROGRESS")
        title.setStyleSheet(
            "color:#00FFAA;font-family:'Courier New';font-size:12px;font-weight:bold;"
            "padding:4px 8px;background:#051a10;border-bottom:1px solid #003322;")
        layout.addWidget(title)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#060f0a;}")
        self.inner=QWidget(); self.inner.setStyleSheet("background:#060f0a;")
        self.grid=QGridLayout(self.inner); self.grid.setSpacing(3); self.grid.setContentsMargins(6,6,6,6)
        scroll.setWidget(self.inner); layout.addWidget(scroll)
        self._bars={}; self._labels={}

    def refresh(self):
        with g_lock:
            engs=list(g_lap_engines.items())
            cfg=dict(g_cfg)
            cp_touches=dict(g_cp_touches)

        total_cps=len(cfg['checkpoints'])
        # clear and rebuild if car count changed
        n_cars=len(engs)
        if not self._bars or len(self._bars)!=n_cars:
            # clear
            while self.grid.count():
                item=self.grid.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self._bars.clear(); self._labels.clear()
            self.grid.addWidget(QLabel("Car"), 0, 0)
            self.grid.addWidget(QLabel("Progress"), 0, 1)
            self.grid.addWidget(QLabel("CPs"), 0, 2)
            for col in [0,1,2]:
                lbl=self.grid.itemAtPosition(0,col).widget()
                lbl.setStyleSheet("color:#00FFAA77;font-family:'Courier New';font-size:9px;")
            for row,(tid,eng) in enumerate(engs, 1):
                color=CAR_COLORS[tid%len(CAR_COLORS)]
                car_lbl=QLabel(g_tags[tid]['name'])
                car_lbl.setStyleSheet(f"color:{color};font-family:'Courier New';font-size:10px;")
                bar=QProgressBar(); bar.setRange(0, max(total_cps,1))
                bar.setValue(0); bar.setTextVisible(False)
                bar.setStyleSheet(f"""
                    QProgressBar{{background:#0a1a10;border:1px solid #1a3325;height:12px;}}
                    QProgressBar::chunk{{background:{color};}}
                """)
                cp_lbl=QLabel("0/0")
                cp_lbl.setStyleSheet("color:#aaa;font-family:'Courier New';font-size:10px;")
                self.grid.addWidget(car_lbl, row, 0)
                self.grid.addWidget(bar, row, 1)
                self.grid.addWidget(cp_lbl, row, 2)
                self._bars[tid]=bar; self._labels[tid]=cp_lbl
        else:
            for tid, eng in engs:
                bar=self._bars.get(tid); lbl=self._labels.get(tid)
                if bar and lbl:
                    bar.setMaximum(max(total_cps,1))
                    bar.setValue(eng._next_cp if eng.is_racing else 0)
                    lbl.setText(f"{eng._next_cp if eng.is_racing else 0}/{total_cps}")

# ══════════════════════════════════════════════════════════════════════
# EVENT FEED WIDGET
# ══════════════════════════════════════════════════════════════════════

class FeedWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        title=QLabel("EVENT FEED")
        title.setStyleSheet(
            "color:#FF8844;font-family:'Courier New';font-size:12px;font-weight:bold;"
            "padding:4px 8px;background:#1a0a00;border-bottom:1px solid #442200;")
        layout.addWidget(title)
        self.text=QTextEdit(); self.text.setReadOnly(True)
        self.text.setStyleSheet("""
            QTextEdit{background:#0c0800;color:#DDBB88;
                font-family:'Courier New';font-size:10px;border:none;padding:4px;}
        """)
        layout.addWidget(self.text)

    def refresh(self):
        with g_lock:
            items=list(g_feed)
        html=""
        for msg in items:
            if "WALL" in msg or "🚧" in msg: color="#FF8844"
            elif "💥" in msg:               color="#FF4466"
            elif "🏆" in msg:               color="#FFDD00"
            elif "LAP" in msg or "🔄" in msg:color="#44AAFF"
            elif "CP" in msg or "✔" in msg: color="#44FF88"
            elif "START" in msg:            color="#FF44FF"
            else:                           color="#AAAAAA"
            safe=msg.replace("&","&amp;").replace("<","&lt;")
            html+=f'<div style="color:{color};margin:1px 0;">{safe}</div>'
        self.text.setHtml(html)
        sb=self.text.verticalScrollBar(); sb.setValue(0)

# ══════════════════════════════════════════════════════════════════════
# CONTROL PANEL
# ══════════════════════════════════════════════════════════════════════

class ControlPanel(QWidget):
    def __init__(self, main_win):
        super().__init__(); self.main=main_win
        layout=QVBoxLayout(self); layout.setContentsMargins(8,8,8,8); layout.setSpacing(10)
        self.setStyleSheet("background:#0a0a1a;")

        # ── Status indicator ──
        self.status_lbl=QLabel("⬤  IDLE")
        self.status_lbl.setStyleSheet("color:#888;font-family:'Courier New';font-size:14px;font-weight:bold;padding:6px;")
        layout.addWidget(self.status_lbl)

        # ── Tournament selector ──
        t_box=QGroupBox("Tournament")
        t_box.setStyleSheet(self._gbox_style("#334"))
        t_lay=QVBoxLayout(t_box)
        self.tournament_combo=QComboBox()
        self.tournament_combo.setStyleSheet(self._combo_style())
        self.tournament_combo.addItem("— loading …  —")
        t_lay.addWidget(self.tournament_combo)
        self.load_tournament_btn=QPushButton("↻ Load Structure")
        self.load_tournament_btn.setStyleSheet(self._btn_style("#334488","#AAAAFF"))
        self.load_tournament_btn.clicked.connect(self.main.on_load_tournament)
        t_lay.addWidget(self.load_tournament_btn)
        layout.addWidget(t_box)

        # ── Round / Group selector ──
        rg_box=QGroupBox("Round → Group")
        rg_box.setStyleSheet(self._gbox_style("#343"))
        rg_lay=QVBoxLayout(rg_box)
        self.round_combo=QComboBox(); self.round_combo.setStyleSheet(self._combo_style())
        self.round_combo.addItem("— select tournament first —")
        self.round_combo.currentIndexChanged.connect(self.main.on_round_changed)
        rg_lay.addWidget(self.round_combo)
        self.group_combo=QComboBox(); self.group_combo.setStyleSheet(self._combo_style())
        self.group_combo.addItem("— select round first —")
        self.group_combo.currentIndexChanged.connect(self.main.on_group_changed)
        rg_lay.addWidget(self.group_combo)
        layout.addWidget(rg_box)

        # ── Tag assignments ──
        tag_box=QGroupBox("Tag Assignments")
        tag_box.setStyleSheet(self._gbox_style("#433"))
        tag_lay=QVBoxLayout(tag_box)
        self.tag_table=QTableWidget(0,3)
        self.tag_table.setHorizontalHeaderLabels(["Player","Tag","Status"])
        self.tag_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tag_table.setMaximumHeight(130)
        self.tag_table.setStyleSheet("""
            QTableWidget{background:#0a0a0a;color:#CCC;border:none;
                font-family:'Courier New';font-size:10px;gridline-color:#222;}
            QHeaderView::section{background:#111;color:#888;border:none;padding:2px;}
        """)
        tag_lay.addWidget(self.tag_table)
        layout.addWidget(tag_box)

        # ── Race config display ──
        cfg_box=QGroupBox("Race Config")
        cfg_box.setStyleSheet(self._gbox_style("#334"))
        cfg_lay=QGridLayout(cfg_box)
        self.cfg_labels={}
        for row,(key,label) in enumerate([
            ('total_laps','Laps'), ('wall_pen','Wall Pen (s)'),
            ('atk_pen','Atk Pen (s)'),('vic_bon','Vic Bonus (s)')
        ]):
            k_lbl=QLabel(label+":"); k_lbl.setStyleSheet("color:#888;font-family:'Courier New';font-size:10px;")
            v_lbl=QLabel("—"); v_lbl.setStyleSheet("color:#AAFF88;font-family:'Courier New';font-size:10px;font-weight:bold;")
            cfg_lay.addWidget(k_lbl, row, 0)
            cfg_lay.addWidget(v_lbl, row, 1)
            self.cfg_labels[key]=v_lbl
        layout.addWidget(cfg_box)

        # ── Action buttons ──
        self.start_btn=QPushButton("▶  ARM RACE")
        self.start_btn.setStyleSheet(self._btn_style("#225522","#44FF88"))
        self.start_btn.clicked.connect(self.main.on_arm_race)
        layout.addWidget(self.start_btn)

        self.finish_btn=QPushButton("⏹  MARK GROUP FINISHED")
        self.finish_btn.setStyleSheet(self._btn_style("#442200","#FF8844"))
        self.finish_btn.clicked.connect(self.main.on_mark_finished)
        layout.addWidget(self.finish_btn)

        self.reset_btn=QPushButton("↺  RESET")
        self.reset_btn.setStyleSheet(self._btn_style("#220022","#FF44FF"))
        self.reset_btn.clicked.connect(self.main.on_reset)
        layout.addWidget(self.reset_btn)

        # ── UDP stats ──
        self.udp_lbl=QLabel("UDP: —")
        self.udp_lbl.setStyleSheet("color:#555;font-family:'Courier New';font-size:9px;padding:4px;")
        layout.addWidget(self.udp_lbl)

        layout.addStretch()

    def update_status(self, armed, active):
        if active:
            self.status_lbl.setText("⬤  RACING"); self.status_lbl.setStyleSheet(
                "color:#44FF44;font-family:'Courier New';font-size:14px;font-weight:bold;padding:6px;")
        elif armed:
            self.status_lbl.setText("⬤  ARMED"); self.status_lbl.setStyleSheet(
                "color:#FFAA00;font-family:'Courier New';font-size:14px;font-weight:bold;padding:6px;")
        else:
            self.status_lbl.setText("⬤  IDLE"); self.status_lbl.setStyleSheet(
                "color:#555555;font-family:'Courier New';font-size:14px;font-weight:bold;padding:6px;")

    def update_cfg_display(self):
        with g_lock: cfg=dict(g_cfg)
        self.cfg_labels['total_laps'].setText(str(cfg['total_laps']))
        self.cfg_labels['wall_pen'].setText(f"{cfg['wall_pen']:.1f}")
        self.cfg_labels['atk_pen'].setText(f"{cfg['atk_pen']:.1f}")
        self.cfg_labels['vic_bon'].setText(f"{cfg['vic_bon']:.1f}")

    def _gbox_style(self, accent):
        return f"""
            QGroupBox{{color:#AAAACC;font-family:'Courier New';font-size:11px;
                border:1px solid {accent};border-radius:4px;margin-top:8px;padding-top:4px;}}
            QGroupBox::title{{subcontrol-origin:margin;left:8px;color:#BBBBDD;}}
        """
    def _combo_style(self):
        return ("QComboBox{background:#111128;color:#CCCCFF;border:1px solid #334;"
                "font-family:'Courier New';font-size:10px;padding:3px;}"
                "QComboBox QAbstractItemView{background:#111128;color:#CCCCFF;}")
    def _btn_style(self, bg, fg):
        return (f"QPushButton{{background:{bg};color:{fg};"
                "font-family:'Courier New';font-size:11px;font-weight:bold;"
                "border:1px solid "+fg+";padding:7px;border-radius:3px;}"
                f"QPushButton:hover{{background:{fg}22;}}"
                "QPushButton:pressed{opacity:0.7;}")

# ══════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ UWB Race Dashboard")
        self.setMinimumSize(1280, 740)
        self.resize(1440, 860)
        self.setStyleSheet("QMainWindow{background:#080810;}")

        # Data
        self._tournaments = []   # [{id, name, slug}, ...]
        self._structure   = None
        self._selected_tournament = None
        self._selected_round_idx  = -1
        self._selected_group      = None   # {group_id, group_name, players:[...]}
        self._event_queue = []   # cross-thread event list (lock-free append is ok)

        # Build UI
        central=QWidget(); self.setCentralWidget(central)
        root=QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Left: track canvas
        self.canvas=TrackCanvas(); self.canvas.setMinimumWidth(480)

        # Middle: leaderboard + checkpoints
        mid=QWidget(); mid.setMaximumWidth(320); mid.setStyleSheet("background:#080812;")
        mid_lay=QVBoxLayout(mid); mid_lay.setContentsMargins(0,0,0,0); mid_lay.setSpacing(0)
        self.leaderboard=LeaderboardWidget(); mid_lay.addWidget(self.leaderboard, 55)
        sep=QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet("color:#222;")
        mid_lay.addWidget(sep)
        self.cp_widget=CheckpointWidget(); mid_lay.addWidget(self.cp_widget, 45)

        # Right: control + feed
        right=QWidget(); right.setMaximumWidth(320); right.setStyleSheet("background:#080810;")
        right_lay=QVBoxLayout(right); right_lay.setContentsMargins(0,0,0,0); right_lay.setSpacing(0)
        self.control=ControlPanel(self); right_lay.addWidget(self.control, 60)
        sep2=QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setStyleSheet("color:#222;")
        right_lay.addWidget(sep2)
        self.feed=FeedWidget(); right_lay.addWidget(self.feed, 40)

        root.addWidget(self.canvas, 1)
        root.addWidget(mid)
        root.addWidget(right)

        # Timers
        self._render_timer=QTimer(); self._render_timer.timeout.connect(self._tick)
        self._render_timer.start(33)   # ~30 fps

        self._cleanup_timer=QTimer(); self._cleanup_timer.timeout.connect(self._cleanup_inactive)
        self._cleanup_timer.start(2000)

        # Start UDP
        global g_udp_running
        g_udp_running=True
        self._udp_thread=threading.Thread(
            target=udp_thread_func, args=(self._event_queue,), daemon=True, name="UDP")
        self._udp_thread.start()

        # Fetch tournaments in background
        self._fetch_tournaments()

    # ── Timer tick (GUI thread) ─────────────────────────────────────

    def _tick(self):
        self.canvas.update()
        self.leaderboard.refresh()
        self.cp_widget.refresh()
        self.feed.refresh()
        with g_lock:
            armed=g_race_armed; active=g_race_active
            total=sum(t['pkt_total'] for t in g_tags.values())
            accepted=sum(t['pkt_accepted'] for t in g_tags.values())
        self.control.update_status(armed, active)
        self.control.udp_lbl.setText(f"UDP  rcv:{total}  ok:{accepted}  err:{total-accepted}")

    def _cleanup_inactive(self):
        now=time.time()
        with g_lock:
            for t in g_tags.values():
                if t['active'] and (now-t['last_update'])>TAG_TIMEOUT:
                    t['active']=False

    # ── Tournament API ──────────────────────────────────────────────

    def _fetch_tournaments(self):
        def _go():
            resp=api_get('/tournaments/')
            if resp and resp.get('success'):
                data=resp['data']
                QTimer.singleShot(0, lambda: self._on_tournaments(data))
        threading.Thread(target=_go, daemon=True).start()

    def _on_tournaments(self, data):
        self._tournaments=data
        self.control.tournament_combo.clear()
        self.control.tournament_combo.addItem("— select tournament —", None)
        for t in data:
            self.control.tournament_combo.addItem(t['name'], t)

    def on_load_tournament(self):
        idx=self.control.tournament_combo.currentIndex()
        t=self.control.tournament_combo.itemData(idx)
        if not t: return
        self._selected_tournament=t
        slug=t['slug']
        def _go():
            resp=api_get(f'/tournament-structure/?slug={slug}')
            if resp and resp.get('success'):
                QTimer.singleShot(0, lambda: self._on_structure(resp['data']))
        threading.Thread(target=_go, daemon=True).start()

    def _on_structure(self, data):
        self._structure=data
        # Apply track + config
        with g_lock:
            global g_cfg
            g_cfg['total_laps']  = int(data.get('total_laps', TOTAL_LAPS_DEFAULT))
            g_cfg['wall_pen']    = float(data.get('object_collision_time', WALL_HIT_PENALTY_DEFAULT))
            g_cfg['atk_pen']     = float(data.get('collision_creating_time', CAR_COLLISION_ATTACKER_PENALTY_DEFAULT))
            g_cfg['vic_bon']     = float(data.get('collision_absorbing_time', CAR_COLLISION_VICTIM_BONUS_DEFAULT))
            track_csv=data.get('track_csv','')
            if track_csv:
                td=parse_track_csv(track_csv)
                g_cfg['track_center'] = td['center']
                g_cfg['track_inner']  = td['inner']
                g_cfg['track_outer']  = td['outer']
                g_cfg['checkpoints']  = td['checkpoints']
                g_cfg['sf_x']   = td['sf_x']
                g_cfg['sf_y1']  = td['sf_y1']
                g_cfg['sf_y2']  = td['sf_y2']
                g_cfg['sf_dir'] = td['sf_dir']

        self.control.update_cfg_display()

        # Populate rounds
        rounds=data.get('rounds',[])
        self.control.round_combo.clear()
        self.control.round_combo.addItem("— select round —", None)
        for r in rounds:
            label=f"Round {r['round_no']}  {r['round_name']}"
            if r['is_final']: label+="  ★ FINAL"
            self.control.round_combo.addItem(label, r)

    def on_round_changed(self, _):
        idx=self.control.round_combo.currentIndex()
        r=self.control.round_combo.itemData(idx)
        if not r: return
        self._selected_round_idx=idx
        groups=r.get('groups',[])
        self.control.group_combo.clear()
        self.control.group_combo.addItem("— select group —", None)
        for g in groups:
            n_players=len([p for p in g['players'] if p['player_id']])
            label=f"{g['group_name']}  [{n_players} players]  {g['group_status']}"
            self.control.group_combo.addItem(label, g)

    def on_group_changed(self, _):
        idx=self.control.group_combo.currentIndex()
        g=self.control.group_combo.itemData(idx)
        if not g: return
        self._selected_group=g
        players=g['players']
        # Populate tag table
        self.control.tag_table.setRowCount(len(players))
        for row, p in enumerate(players):
            self.control.tag_table.setItem(row,0,QTableWidgetItem(p['player_name']))
            self.control.tag_table.setItem(row,1,QTableWidgetItem(str(p['tag_id'] or '—')))
            self.control.tag_table.setItem(row,2,QTableWidgetItem(p['player_status']))

    # ── Race control ────────────────────────────────────────────────

    def on_arm_race(self):
        if not self._selected_group:
            self._show_msg("No group selected!"); return

        global g_race_armed, g_race_active, g_tag_to_gp, g_group_id, g_cp_touches
        players=self._selected_group['players']

        with g_lock:
            g_tag_to_gp={}
            for p in players:
                tid_raw=p.get('tag_id')
                if tid_raw is None: continue
                try: tid=int(tid_raw)
                except: continue
                g_tag_to_gp[tid]=p['gp_id']
                g_tags[tid]['name']=p['player_name']

            g_group_id=self._selected_group['group_id']
            g_cp_touches.clear()
            g_race_active=False; g_race_armed=True

            # Reset + register lap engines
            g_lap_engines.clear()
            _car_cd.clear(); _wall_cd.clear()
            for tid in range(TAG_COUNT):
                eng=LapEng(tid, g_tags[tid]['name'])
                eng.arm()
                g_lap_engines[tid]=eng
            g_feed.clear()

        # Mark group live on server
        gid=self._selected_group['group_id']
        api_patch('/mark-group-live/', {'group_id': gid},
                  cb=lambda ok,r: print(f"[LIVE] {ok} {r}"))

        # POST admin_start to backend (optional notification)
        # (no dedicated endpoint — mark-group-live covers it)

        self._show_msg(f"Race ARMED — {g_cfg['total_laps']} laps\nGroup: {self._selected_group['group_name']}")
        print(f"[ARM] group={gid}  tag_to_gp={g_tag_to_gp}  laps={g_cfg['total_laps']}")

    def on_mark_finished(self):
        if not self._selected_group:
            self._show_msg("No group selected!"); return
        gid=self._selected_group['group_id']
        api_patch('/mark-group-finished/', {'group_id': gid},
                  cb=lambda ok,r: print(f"[FINISH] {ok} {r}"))
        global g_race_armed, g_race_active
        with g_lock:
            g_race_armed=False; g_race_active=False
        self._show_msg("Group marked as FINISHED")

    def on_reset(self):
        global g_race_armed, g_race_active, g_tag_to_gp, g_group_id, g_cp_touches
        with g_lock:
            g_race_armed=False; g_race_active=False
            g_tag_to_gp={}; g_group_id=None; g_cp_touches.clear()
            g_lap_engines.clear(); _car_cd.clear(); _wall_cd.clear()
            g_feed.clear()
            for t in g_tags.values(): t['trail'].clear(); t['active']=False
        self._show_msg("Race RESET")

    def _show_msg(self, msg):
        dlg=QDialog(self); dlg.setWindowTitle("Info")
        dlg.setStyleSheet("background:#0a0a1a;color:#EEE;font-family:'Courier New';")
        lay=QVBoxLayout(dlg)
        lbl=QLabel(msg); lbl.setStyleSheet("color:#AAFF88;font-size:12px;padding:12px;")
        lay.addWidget(lbl)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.setStyleSheet("QPushButton{background:#223;color:#AFF;border:1px solid #44F;padding:6px 16px;}")
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb); dlg.exec()

    def closeEvent(self, e):
        global g_udp_running
        g_udp_running=False; super().closeEvent(e)

# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def main():
    app=QApplication(sys.argv)
    app.setStyle("Fusion")

    dark=QPalette()
    dark.setColor(QPalette.ColorRole.Window,        QColor("#080810"))
    dark.setColor(QPalette.ColorRole.WindowText,    QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Base,          QColor("#0c0c1a"))
    dark.setColor(QPalette.ColorRole.AlternateBase, QColor("#111122"))
    dark.setColor(QPalette.ColorRole.Text,          QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Button,        QColor("#111128"))
    dark.setColor(QPalette.ColorRole.ButtonText,    QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Highlight,     QColor("#334488"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(dark)

    win=MainWindow(); win.show()
    sys.exit(app.exec())

if __name__=="__main__":
    main()