#!/usr/bin/env python3
"""
track_mapper.py  —  UWB Indoor Track Mapper
============================================
Use this tool to physically walk/drive the track boundaries and
export a track.csv compatible with race_gui.py.

HOW IT WORKS:
  1. Start the tool — it listens for live UWB tag position on UDP
  2. Use keyboard shortcuts to record:
       C  → record current point as CENTER line
       O  → record current point as OUTER boundary
       I  → record current point as INNER boundary
       S  → mark START/FINISH line (records two Y points to form a line)
       P  → place a CHECKPOINT at current position
       U  → undo last recorded point
       E  → export track.csv
       Q  → quit

  3. The canvas shows real-time position + all recorded points
  4. Press E to export — saved as track.csv

WORKFLOW:
  - Walk/drive the OUTER boundary first (press O every ~20cm)
  - Walk/drive the INNER boundary (press I every ~20cm)
  - Walk the CENTER line (press C every ~20cm)
  - Stand at the start/finish line, press S twice (left edge, then right edge)
  - Place checkpoints along the track (press P)
  - Press E to export

Run:
    pip install PyQt6
    python track_mapper.py
"""

import sys
import socket
import json
import math
import time
import csv
import threading
from collections import deque

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QStatusBar, QFileDialog,
    QGroupBox, QGridLayout, QSpinBox, QDoubleSpinBox, QMessageBox,
    QListWidget, QListWidgetItem, QSplitter,
)
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QSize
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QKeySequence, QShortcut, QPalette,
)

# ══════════════════════════════════════════════════════════════════
# CONFIG  — adjust to match your room & UWB setup
# ══════════════════════════════════════════════════════════════════

UDP_PORT         = 4210
ANCHOR_COUNT     = 4
MIN_RANGE_CM     = 10
MAX_RANGE_CM     = 1450
TAG_ID_TO_TRACK  = 0      # which tag id to use for mapping (change if needed)

ANCHOR_POSITIONS = {
    0: (0,   0),
    1: (610, 0),
    2: (610, 440),
    3: (0,   440),
}

ROOM_W = 610   # cm
ROOM_H = 440   # cm

CHECKPOINT_RADIUS_CM = 30   # default radius for checkpoints

# ══════════════════════════════════════════════════════════════════
# POSITIONING  (same trilateration as race_gui.py)
# ══════════════════════════════════════════════════════════════════

def valid_anchors(ranges, ap):
    out = []
    for i, r in enumerate(ranges):
        if r <= 0 or i not in ap: continue
        if r < MIN_RANGE_CM or r > MAX_RANGE_CM: continue
        out.append({'id': i, 'range': r, 'x': ap[i][0], 'y': ap[i][1]})
    return out

def tri3(a1, a2, a3):
    x1,y1,r1 = a1['x'],a1['y'],a1['range']
    x2,y2,r2 = a2['x'],a2['y'],a2['range']
    x3,y3,r3 = a3['x'],a3['y'],a3['range']
    A = 2*(x2-x1); B = 2*(y2-y1)
    C = r1**2 - r2**2 - x1**2 + x2**2 - y1**2 + y2**2
    D = 2*(x3-x2); E = 2*(y3-y2)
    F = r2**2 - r3**2 - x2**2 + x3**2 - y2**2 + y3**2
    den = A*E - B*D
    if abs(den) < 0.001:
        ratio = r1/(r1+r2) if (r1+r2) > 0 else 0.5
        return x1+(x2-x1)*ratio, y1+(y2-y1)*ratio
    return (C*E - F*B)/den, (A*F - C*D)/den

def multilat(va):
    combos = []
    for i in range(len(va)):
        for j in range(i+1, len(va)):
            for k in range(j+1, len(va)):
                px, py = tri3(va[i], va[j], va[k])
                combos.append((px, py))
    if not combos: return None
    return (sum(c[0] for c in combos)/len(combos),
            sum(c[1] for c in combos)/len(combos))

def calculate_position(ranges, ap):
    va = valid_anchors(ranges, ap)
    nv = len(va)
    if nv >= 4:
        pos = multilat(va); q = 'excellent'
    elif nv == 3:
        pos = tri3(*va[:3]); q = 'good'
    elif nv == 2:
        a1, a2 = va[0], va[1]
        ratio = a1['range']/(a1['range']+a2['range']) if (a1['range']+a2['range']) > 0 else 0.5
        pos = (a1['x']+(a2['x']-a1['x'])*ratio,
               a1['y']+(a2['y']-a1['y'])*ratio)
        q = 'fair'
    else:
        return None, 'poor', nv
    if pos is None: return None, q, nv
    return pos, q, nv

def reorder_by_ancid(slot_ranges, ancid, n=ANCHOR_COUNT):
    if not ancid or not any(a >= 0 for a in ancid):
        return [float(r) for r in slot_ranges[:n]]
    out = [0.0] * n
    for slot, anc in enumerate(ancid):
        if 0 <= anc < n and slot < len(slot_ranges):
            out[anc] = float(slot_ranges[slot])
    return out

# ══════════════════════════════════════════════════════════════════
# SHARED STATE
# ══════════════════════════════════════════════════════════════════

state_lock = threading.Lock()

live_pos   = {'x': 0.0, 'y': 0.0, 'quality': 'unknown', 'active': False, 'last_update': 0.0}
udp_running = False

# Recorded track data
track_data = {
    'outer':        [],   # [(x,y), ...]
    'inner':        [],
    'center':       [],
    'sf':           [],   # [(x,y), (x,y)]  exactly 2 points = the S/F line
    'checkpoints':  [],   # [(x,y,radius), ...]
    'history':      [],   # for undo: ('type', index)
}

# ══════════════════════════════════════════════════════════════════
# UDP LISTENER THREAD
# ══════════════════════════════════════════════════════════════════

def udp_thread():
    global udp_running
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', UDP_PORT))
        sock.settimeout(0.1)
        print(f"[UDP] Listening on port {UDP_PORT} for tag {TAG_ID_TO_TRACK}")
    except Exception as e:
        print(f"[UDP] Failed to bind: {e}")
        return

    while udp_running:
        try:
            data, _ = sock.recvfrom(2048)
            try:
                uwb = json.loads(data.decode('utf-8', errors='ignore').strip())
            except:
                continue

            if 'id' not in uwb or 'range' not in uwb:
                continue
            tid = int(uwb['id'])
            if tid != TAG_ID_TO_TRACK:
                continue

            slot_ranges = uwb['range']
            if not isinstance(slot_ranges, list) or len(slot_ranges) < ANCHOR_COUNT:
                continue

            ancid = uwb.get('ancid', [])
            raw_ranges = reorder_by_ancid(slot_ranges, ancid, ANCHOR_COUNT)
            pos, quality, _ = calculate_position(raw_ranges, ANCHOR_POSITIONS)

            if pos is None:
                continue

            with state_lock:
                live_pos['x'] = pos[0]
                live_pos['y'] = pos[1]
                live_pos['quality'] = quality
                live_pos['active'] = True
                live_pos['last_update'] = time.time()

        except socket.timeout:
            continue
        except Exception as e:
            if udp_running:
                print(f"[UDP] Error: {e}")

    sock.close()
    print("[UDP] Stopped")

# ══════════════════════════════════════════════════════════════════
# CANVAS
# ══════════════════════════════════════════════════════════════════

COLORS = {
    'outer':       '#3366FF',
    'inner':       '#FF6633',
    'center':      '#AAAAAA',
    'sf':          '#FFDD00',
    'checkpoint':  '#00FFAA',
    'car':         '#FF44AA',
    'anchor':      '#FF8800',
    'grid':        '#FFFFFF0D',
}

POINT_SIZE = {
    'outer': 7, 'inner': 7, 'center': 5,
}

class MapCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(600, 440)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0
        self._flash = None   # ('type', countdown)
        self._mode = 'none'  # current recording mode hint

    def set_flash(self, label):
        self._flash = [label, 8]

    def _compute_transform(self, W, H):
        margin = 36
        sx = (W - 2*margin) / ROOM_W
        sy = (H - 2*margin) / ROOM_H
        self._scale = min(sx, sy)
        self._ox = (W - ROOM_W*self._scale) / 2
        self._oy = (H - ROOM_H*self._scale) / 2

    def tp(self, x, y):
        return QPointF(self._ox + x*self._scale,
                       self._oy + y*self._scale)

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        self._compute_transform(W, H)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, W, H, QColor("#0a0a14"))

        with state_lock:
            snap = {k: list(v) if isinstance(v, list) else dict(v)
                    for k, v in track_data.items()}
            pos = dict(live_pos)
            snap['sf'] = list(track_data['sf'])
            snap['checkpoints'] = list(track_data['checkpoints'])

        self._draw_grid(p)
        self._draw_room_border(p)
        self._draw_anchors(p)
        self._draw_track_lines(p, snap)
        self._draw_sf(p, snap['sf'])
        self._draw_checkpoints(p, snap['checkpoints'])
        self._draw_car(p, pos)
        self._draw_flash(p, W, H)
        p.end()

    def _draw_grid(self, p):
        pen = QPen(QColor(COLORS['grid'])); pen.setWidth(1); p.setPen(pen)
        for gx in range(0, ROOM_W+1, 100):
            a = self.tp(gx, 0); b = self.tp(gx, ROOM_H)
            p.drawLine(a, b)
        for gy in range(0, ROOM_H+1, 80):
            a = self.tp(0, gy); b = self.tp(ROOM_W, gy)
            p.drawLine(a, b)

    def _draw_room_border(self, p):
        pen = QPen(QColor("#334")); pen.setWidth(2); p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tl = self.tp(0, 0); br = self.tp(ROOM_W, ROOM_H)
        p.drawRect(QRectF(tl, br))

    def _draw_anchors(self, p):
        for aid, (ax, ay) in ANCHOR_POSITIONS.items():
            pt = self.tp(ax, ay)
            p.setPen(QPen(QColor(COLORS['anchor']), 1.5))
            p.setBrush(QBrush(QColor("#FF880066")))
            p.drawRect(int(pt.x())-6, int(pt.y())-6, 12, 12)
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.setPen(QColor(COLORS['anchor']))
            p.drawText(QPointF(pt.x()+8, pt.y()-2), f"A{aid}")

    def _draw_track_lines(self, p, snap):
        configs = [
            ('outer',  COLORS['outer'],  2.5, POINT_SIZE['outer']),
            ('inner',  COLORS['inner'],  2.0, POINT_SIZE['inner']),
            ('center', COLORS['center'], 1.5, POINT_SIZE['center']),
        ]
        for key, color, lw, ps in configs:
            pts = snap[key]
            if not pts: continue
            # Line
            pen = QPen(QColor(color)); pen.setWidthF(lw)
            pen.setStyle(Qt.PenStyle.DashLine if key == 'center' else Qt.PenStyle.SolidLine)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            if len(pts) > 1:
                path = QPainterPath()
                path.moveTo(self.tp(*pts[0]))
                for pt in pts[1:]:
                    path.lineTo(self.tp(*pt))
                p.drawPath(path)
            # Dots
            p.setBrush(QBrush(QColor(color)))
            p.setPen(Qt.PenStyle.NoPen)
            for pt in pts:
                tp = self.tp(*pt)
                r = ps / 2
                p.drawEllipse(tp, r, r)

            # Index numbers
            p.setFont(QFont("Courier New", 7))
            p.setPen(QColor(color))
            for i, pt in enumerate(pts):
                tp = self.tp(*pt)
                p.drawText(QPointF(tp.x()+4, tp.y()-3), str(i+1))

    def _draw_sf(self, p, sf_pts):
        if not sf_pts: return
        pen = QPen(QColor(COLORS['sf'])); pen.setWidthF(3)
        pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
        if len(sf_pts) == 1:
            pt = self.tp(*sf_pts[0])
            p.drawEllipse(pt, 8, 8)
        elif len(sf_pts) >= 2:
            a = self.tp(*sf_pts[0]); b = self.tp(*sf_pts[-1])
            p.drawLine(a, b)
        p.setPen(QColor(COLORS['sf']))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        if sf_pts:
            mid = self.tp(
                sum(s[0] for s in sf_pts)/len(sf_pts),
                sum(s[1] for s in sf_pts)/len(sf_pts)
            )
            p.drawText(QPointF(mid.x()+6, mid.y()-6), "S/F")

    def _draw_checkpoints(self, p, cps):
        for i, (cx, cy, cr) in enumerate(cps):
            pt = self.tp(cx, cy)
            r = cr * self._scale
            pen = QPen(QColor(COLORS['checkpoint'])); pen.setWidthF(1.5); p.setPen(pen)
            p.setBrush(QBrush(QColor("#00FFAA20")))
            p.drawEllipse(pt, r, r)
            p.setPen(QColor(COLORS['checkpoint']))
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.drawText(QPointF(pt.x()+r+3, pt.y()+4), f"CP{i}")

    def _draw_car(self, p, pos):
        if not pos['active'] or (time.time() - pos['last_update']) > 3.0:
            return
        pt = self.tp(pos['x'], pos['y'])
        color = QColor(COLORS['car'])
        pen = QPen(color, 2); p.setPen(pen)
        p.setBrush(QBrush(QColor("#FF44AA44")))
        p.drawEllipse(pt, 10, 10)
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(color)
        p.drawText(QPointF(pt.x()+12, pt.y()-4),
                   f"({pos['x']:.0f},{pos['y']:.0f})  {pos['quality']}")

    def _draw_flash(self, p, W, H):
        if not self._flash: return
        label, count = self._flash
        if count <= 0:
            self._flash = None; return
        self._flash[1] -= 1
        alpha = int(255 * (count / 8))
        color = QColor(255, 255, 100, alpha)
        p.setFont(QFont("Courier New", 22, QFont.Weight.Bold))
        p.setPen(color)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label)
        p.drawText(QPointF(W//2 - tw//2, H//2), label)

# ══════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════

class TrackMapperWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🗺  UWB Track Mapper")
        self.setMinimumSize(1100, 640)
        self.resize(1200, 720)
        self.setStyleSheet("QMainWindow{background:#080810;}")

        self._cp_radius = CHECKPOINT_RADIUS_CM
        self._sf_mode_armed = False   # True while waiting for 2nd S/F click

        self._build_ui()
        self._setup_shortcuts()

        # Timers
        self._render_timer = QTimer()
        self._render_timer.timeout.connect(self._tick)
        self._render_timer.start(40)   # 25fps

        # UDP
        global udp_running
        udp_running = True
        self._udp_thread = threading.Thread(target=udp_thread, daemon=True, name="UDP")
        self._udp_thread.start()

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Canvas (left) ──
        self.canvas = MapCanvas()
        root.addWidget(self.canvas, 1)

        # ── Sidebar (right) ──
        sidebar = QWidget(); sidebar.setFixedWidth(280)
        sidebar.setStyleSheet("background:#0c0c1a;")
        sb_lay = QVBoxLayout(sidebar); sb_lay.setContentsMargins(10,10,10,10); sb_lay.setSpacing(8)

        title = QLabel("UWB TRACK MAPPER")
        title.setStyleSheet("color:#FFDD00;font-family:'Courier New';font-size:14px;font-weight:bold;")
        sb_lay.addWidget(title)

        # Live position
        pos_box = QGroupBox("Live Position")
        pos_box.setStyleSheet(self._gbox("#334"))
        pos_lay = QGridLayout(pos_box)
        self.pos_x_lbl = QLabel("X: —"); self.pos_y_lbl = QLabel("Y: —")
        self.pos_q_lbl = QLabel("Quality: —")
        for lbl in [self.pos_x_lbl, self.pos_y_lbl, self.pos_q_lbl]:
            lbl.setStyleSheet("color:#AAFFAA;font-family:'Courier New';font-size:11px;")
        pos_lay.addWidget(self.pos_x_lbl, 0, 0)
        pos_lay.addWidget(self.pos_y_lbl, 0, 1)
        pos_lay.addWidget(self.pos_q_lbl, 1, 0, 1, 2)
        sb_lay.addWidget(pos_box)

        # Recording buttons
        rec_box = QGroupBox("Record Points  (keyboard or click)")
        rec_box.setStyleSheet(self._gbox("#343"))
        rec_lay = QVBoxLayout(rec_box)

        btn_defs = [
            ("O  — OUTER boundary",    '#3366FF', self.record_outer),
            ("I  — INNER boundary",    '#FF6633', self.record_inner),
            ("C  — CENTER line",       '#AAAAAA', self.record_center),
            ("S  — START/FINISH line", '#FFDD00', self.record_sf),
            ("P  — CHECKPOINT",        '#00FFAA', self.record_checkpoint),
            ("U  — UNDO last point",   '#FF4488', self.undo_last),
        ]
        self._rec_btns = {}
        for label, color, slot in btn_defs:
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"QPushButton{{background:#111128;color:{color};"
                "font-family:'Courier New';font-size:10px;font-weight:bold;"
                f"border:1px solid {color}44;padding:6px;text-align:left;}}"
                f"QPushButton:hover{{background:{color}22;}}"
            )
            btn.clicked.connect(slot)
            rec_lay.addWidget(btn)
            key = label.split('—')[1].strip().split()[0].upper()
            self._rec_btns[key] = btn
        sb_lay.addWidget(rec_box)

        # CP radius
        cp_box = QGroupBox("Checkpoint Radius (cm)")
        cp_box.setStyleSheet(self._gbox("#334"))
        cp_lay = QHBoxLayout(cp_box)
        self.cp_radius_spin = QSpinBox()
        self.cp_radius_spin.setRange(10, 200)
        self.cp_radius_spin.setValue(CHECKPOINT_RADIUS_CM)
        self.cp_radius_spin.setStyleSheet(
            "QSpinBox{background:#111128;color:#AAFFAA;border:1px solid #334;"
            "font-family:'Courier New';padding:4px;}")
        self.cp_radius_spin.valueChanged.connect(self._on_radius_changed)
        cp_lay.addWidget(self.cp_radius_spin)
        cp_lay.addWidget(QLabel("cm"), )
        sb_lay.addWidget(cp_box)

        # Point counts
        counts_box = QGroupBox("Recorded Points")
        counts_box.setStyleSheet(self._gbox("#334"))
        counts_lay = QGridLayout(counts_box)
        self._count_labels = {}
        for row, (key, color) in enumerate([
            ('outer','#3366FF'), ('inner','#FF6633'),
            ('center','#AAAAAA'), ('sf','#FFDD00'),
            ('checkpoints','#00FFAA'),
        ]):
            k_lbl = QLabel(key.capitalize() + ":")
            k_lbl.setStyleSheet(f"color:{color};font-family:'Courier New';font-size:10px;")
            v_lbl = QLabel("0")
            v_lbl.setStyleSheet("color:#EEE;font-family:'Courier New';font-size:10px;font-weight:bold;")
            counts_lay.addWidget(k_lbl, row, 0)
            counts_lay.addWidget(v_lbl, row, 1)
            self._count_labels[key] = v_lbl
        sb_lay.addWidget(counts_box)

        # Export / Clear
        export_btn = QPushButton("E  — EXPORT  track.csv")
        export_btn.setStyleSheet(
            "QPushButton{background:#224422;color:#44FF88;font-family:'Courier New';"
            "font-size:11px;font-weight:bold;border:1px solid #44FF88;padding:8px;}"
            "QPushButton:hover{background:#335533;}"
        )
        export_btn.clicked.connect(self.export_csv)
        sb_lay.addWidget(export_btn)

        clear_btn = QPushButton("CLEAR ALL")
        clear_btn.setStyleSheet(
            "QPushButton{background:#221122;color:#FF44FF;font-family:'Courier New';"
            "font-size:10px;font-weight:bold;border:1px solid #FF44FF44;padding:6px;}"
            "QPushButton:hover{background:#332233;}"
        )
        clear_btn.clicked.connect(self.clear_all)
        sb_lay.addWidget(clear_btn)

        # Legend
        legend = QLabel(
            "<span style='color:#555;font-size:9px;font-family:Courier New;'>"
            "Keyboard: O I C S P U E Q</span>"
        )
        legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb_lay.addWidget(legend)
        sb_lay.addStretch()

        # Status bar
        self.status_lbl = QLabel("Ready — waiting for UWB data…")
        self.status_lbl.setStyleSheet("color:#666;font-family:'Courier New';font-size:9px;padding:2px 6px;")
        sb_lay.addWidget(self.status_lbl)

        root.addWidget(sidebar)

    def _setup_shortcuts(self):
        shortcuts = {
            'O': self.record_outer,
            'I': self.record_inner,
            'C': self.record_center,
            'S': self.record_sf,
            'P': self.record_checkpoint,
            'U': self.undo_last,
            'E': self.export_csv,
            'Q': self.close,
        }
        for key, slot in shortcuts.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)

    # ── Tick ──────────────────────────────────────────────────────

    def _tick(self):
        with state_lock:
            pos = dict(live_pos)
            counts = {
                'outer':       len(track_data['outer']),
                'inner':       len(track_data['inner']),
                'center':      len(track_data['center']),
                'sf':          len(track_data['sf']),
                'checkpoints': len(track_data['checkpoints']),
            }

        now = time.time()
        active = pos['active'] and (now - pos['last_update']) < 3.0

        if active:
            self.pos_x_lbl.setText(f"X: {pos['x']:.1f}")
            self.pos_y_lbl.setText(f"Y: {pos['y']:.1f}")
            q = pos['quality']
            color = {'excellent':'#44FF88','good':'#AAFF44','fair':'#FFAA44','poor':'#FF4444'}.get(q,'#888')
            self.pos_q_lbl.setText(f"Quality: <span style='color:{color}'>{q}</span>")
            self.pos_q_lbl.setTextFormat(Qt.TextFormat.RichText)
        else:
            self.pos_x_lbl.setText("X: —")
            self.pos_y_lbl.setText("Y: —")
            self.pos_q_lbl.setText("Quality: no signal")

        for key, lbl in self._count_labels.items():
            lbl.setText(str(counts[key]))

        self.canvas.update()

    # ── Recording actions ─────────────────────────────────────────

    def _get_live_pos(self):
        with state_lock:
            pos = dict(live_pos)
        now = time.time()
        if not pos['active'] or (now - pos['last_update']) > 3.0:
            self._set_status("⚠ No live UWB signal — cannot record")
            return None
        return pos['x'], pos['y']

    def record_outer(self):
        pt = self._get_live_pos()
        if pt is None: return
        with state_lock:
            track_data['outer'].append(pt)
            track_data['history'].append(('outer', len(track_data['outer'])-1))
        self.canvas.set_flash("OUTER")
        self._set_status(f"Outer point {len(track_data['outer'])} recorded: {pt[0]:.1f},{pt[1]:.1f}")

    def record_inner(self):
        pt = self._get_live_pos()
        if pt is None: return
        with state_lock:
            track_data['inner'].append(pt)
            track_data['history'].append(('inner', len(track_data['inner'])-1))
        self.canvas.set_flash("INNER")
        self._set_status(f"Inner point {len(track_data['inner'])} recorded: {pt[0]:.1f},{pt[1]:.1f}")

    def record_center(self):
        pt = self._get_live_pos()
        if pt is None: return
        with state_lock:
            track_data['center'].append(pt)
            track_data['history'].append(('center', len(track_data['center'])-1))
        self.canvas.set_flash("CENTER")
        self._set_status(f"Center point {len(track_data['center'])} recorded: {pt[0]:.1f},{pt[1]:.1f}")

    def record_sf(self):
        pt = self._get_live_pos()
        if pt is None: return
        with state_lock:
            n = len(track_data['sf'])
            if n >= 2:
                # reset
                track_data['sf'].clear()
                n = 0
            track_data['sf'].append(pt)
            track_data['history'].append(('sf', len(track_data['sf'])-1))
            n = len(track_data['sf'])

        if n == 1:
            self.canvas.set_flash("S/F Pt1")
            self._set_status("S/F point 1 set — press S again at 2nd edge of line")
        else:
            self.canvas.set_flash("S/F LINE ✓")
            self._set_status(f"S/F line defined with 2 points")

    def record_checkpoint(self):
        pt = self._get_live_pos()
        if pt is None: return
        r = self.cp_radius_spin.value()
        with state_lock:
            idx = len(track_data['checkpoints'])
            track_data['checkpoints'].append((pt[0], pt[1], r))
            track_data['history'].append(('checkpoints', idx))
        self.canvas.set_flash(f"CP{idx}")
        self._set_status(f"Checkpoint {idx} at {pt[0]:.1f},{pt[1]:.1f} r={r}cm")

    def undo_last(self):
        with state_lock:
            if not track_data['history']:
                self._set_status("Nothing to undo"); return
            kind, idx = track_data['history'].pop()
            lst = track_data[kind]
            if lst and idx == len(lst)-1:
                lst.pop()
        self.canvas.set_flash("UNDO")
        self._set_status(f"Undid last {kind} point")

    # ── Export ────────────────────────────────────────────────────

    def export_csv(self):
        with state_lock:
            outer    = list(track_data['outer'])
            inner    = list(track_data['inner'])
            center   = list(track_data['center'])
            sf       = list(track_data['sf'])
            cps      = list(track_data['checkpoints'])

        if not outer and not center:
            QMessageBox.warning(self, "No Data",
                "No track points recorded yet.\n"
                "Walk/drive the track boundaries first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Track CSV", "track.csv", "CSV files (*.csv)")
        if not path: return

        try:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)

                # Header comment
                writer.writerow(['# UWB Track Map — generated by track_mapper.py'])
                writer.writerow([f'# Exported: {time.strftime("%Y-%m-%d %H:%M:%S")}'])
                writer.writerow([f'# Room: {ROOM_W}cm x {ROOM_H}cm'])
                writer.writerow([])
                writer.writerow(['# type', 'x', 'y', '(or x1,y1,x2,y2,dir for START_FINISH)'])
                writer.writerow([])

                # OUTER
                if outer:
                    writer.writerow(['# --- OUTER BOUNDARY ---'])
                    for x, y in outer:
                        writer.writerow(['OUTER', f'{x:.2f}', f'{y:.2f}'])
                    writer.writerow([])

                # INNER
                if inner:
                    writer.writerow(['# --- INNER BOUNDARY ---'])
                    for x, y in inner:
                        writer.writerow(['INNER', f'{x:.2f}', f'{y:.2f}'])
                    writer.writerow([])

                # CENTER
                if center:
                    writer.writerow(['# --- CENTER LINE ---'])
                    for x, y in center:
                        writer.writerow(['CENTER', f'{x:.2f}', f'{y:.2f}'])
                    writer.writerow([])

                # START/FINISH
                if len(sf) >= 2:
                    x1,y1 = sf[0]; x2,y2 = sf[1]
                    # direction based on which side has smaller x
                    direction = 'left_to_right' if x1 < x2 else 'right_to_left'
                    writer.writerow(['# --- START/FINISH LINE ---'])
                    writer.writerow(['START_FINISH',
                                     f'{x1:.2f}', f'{y1:.2f}',
                                     f'{x2:.2f}', f'{y2:.2f}',
                                     direction])
                    writer.writerow([])
                elif len(sf) == 1:
                    # single point — write a vertical line centered on it
                    x, y = sf[0]
                    writer.writerow(['# --- START/FINISH (1-point fallback) ---'])
                    writer.writerow(['START_FINISH',
                                     f'{x:.2f}', f'{max(0,y-30):.2f}',
                                     f'{x:.2f}', f'{min(ROOM_H,y+30):.2f}',
                                     'left_to_right'])
                    writer.writerow([])

                # CHECKPOINTS
                if cps:
                    writer.writerow(['# --- CHECKPOINTS ---'])
                    for i, (cx, cy, cr) in enumerate(cps):
                        writer.writerow(['CHECKPOINT', str(i),
                                         f'{cx:.2f}', f'{cy:.2f}', f'{cr:.2f}'])
                    writer.writerow([])

            QMessageBox.information(self, "Exported",
                f"Track saved to:\n{path}\n\n"
                f"Outer: {len(outer)} pts\n"
                f"Inner: {len(inner)} pts\n"
                f"Center: {len(center)} pts\n"
                f"Checkpoints: {len(cps)}\n"
                f"S/F line: {'yes' if len(sf)>=2 else 'partial' if sf else 'no'}")
            self._set_status(f"Exported → {path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def clear_all(self):
        reply = QMessageBox.question(self, "Clear All",
            "Delete ALL recorded track points?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        with state_lock:
            for key in ['outer','inner','center','sf','checkpoints','history']:
                track_data[key].clear()
        self._set_status("All points cleared")

    def _on_radius_changed(self, val):
        self._cp_radius = val

    def _set_status(self, msg):
        self.status_lbl.setText(msg)
        print(f"[STATUS] {msg}")

    def _gbox(self, accent):
        return (f"QGroupBox{{color:#AAAACC;font-family:'Courier New';font-size:10px;"
                f"border:1px solid {accent};border-radius:4px;margin-top:8px;padding-top:4px;}}"
                "QGroupBox::title{subcontrol-origin:margin;left:8px;color:#BBBBDD;}")

    def closeEvent(self, e):
        global udp_running
        udp_running = False
        super().closeEvent(e)

# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window,          QColor("#080810"))
    dark.setColor(QPalette.ColorRole.WindowText,      QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Base,            QColor("#0c0c1a"))
    dark.setColor(QPalette.ColorRole.AlternateBase,   QColor("#111122"))
    dark.setColor(QPalette.ColorRole.Text,            QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Button,          QColor("#111128"))
    dark.setColor(QPalette.ColorRole.ButtonText,      QColor("#CCCCEE"))
    dark.setColor(QPalette.ColorRole.Highlight,       QColor("#334488"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(dark)

    win = TrackMapperWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()