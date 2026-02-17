"""
UWB Racing Tracker – Matplotlib Renderer
Shows: wide track, ELP leaderboard, incident feed, tags with speed/lap info
"""
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import time, math
from race_config import *

TAG_COLORS = ['#00BFFF', '#FF4500', '#32CD32', '#FFD700', '#FF69B4']


class MatplotlibRenderer:
    def __init__(self, scale_params):
        self.cm2p     = scale_params['cm2p']
        self.x_offset = scale_params['x_offset']
        self.y_offset = scale_params['y_offset']

        self.fig, self.ax = plt.subplots(figsize=(14, 9))
        try:
            self.fig.canvas.manager.set_window_title('UWB Racing Tracker')
        except Exception:
            pass
        self.ax.set_xlim(0, SCREEN_X)
        self.ax.set_ylim(0, SCREEN_Y)
        self.ax.set_aspect('equal')
        self.ax.invert_yaxis()
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.fig.patch.set_facecolor('#1a1a2e')
        self.ax.set_facecolor('#1a1a2e')
        plt.ion()
        plt.tight_layout(pad=0.5)
        plt.show(block=False)

    # ── coordinate conversion ───────────────────────────────
    def cm2px(self, x, y):
        px = x * self.cm2p + self.x_offset
        py = SCREEN_Y - (y * self.cm2p + self.y_offset)
        return px, py

    # ── grid ────────────────────────────────────────────────
    def draw_grid(self):
        gs = GRID_SPACING_CM * self.cm2p
        for x in np.arange(0, SCREEN_X, gs):
            self.ax.axvline(x, color='#2a2a4a', linewidth=0.5)
        for y in np.arange(0, SCREEN_Y, gs):
            self.ax.axhline(y, color='#2a2a4a', linewidth=0.5)

    # ── track ───────────────────────────────────────────────
    def draw_track(self, track):
        if not track: return
        if track.has_width():
            op = [self.cm2px(x,y) for x,y in track.get_outer_points()]
            ip = [self.cm2px(x,y) for x,y in track.get_inner_points()]

            # Infield grass
            if ip:
                self.ax.add_patch(patches.Polygon(ip, closed=True,
                    facecolor='#2d5a27', alpha=0.5, edgecolor='none', zorder=1))

            # Asphalt surface
            if op and ip:
                self.ax.add_patch(patches.Polygon(op+ip[::-1], closed=True,
                    facecolor='#2a2a2a', alpha=0.95, edgecolor='none', zorder=2))

            # Outer wall (red + white dashes)
            if op:
                xs,ys = zip(*op)
                self.ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]],
                    color='#CC0000', lw=5, zorder=4)
                self.ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]],
                    color='white', lw=2, ls='--', dashes=(12,8), zorder=5)

            # Inner wall (red + white dashes)
            if ip:
                xs,ys = zip(*ip)
                self.ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]],
                    color='#CC0000', lw=5, zorder=4)
                self.ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]],
                    color='white', lw=2, ls='--', dashes=(12,8), zorder=5)

            # Yellow racing line
            if len(op)==len(ip):
                cl = [((op[i][0]+ip[i][0])/2,(op[i][1]+ip[i][1])/2) for i in range(len(op))]
                xs,ys = zip(*cl)
                self.ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]],
                    color='#FFD700', lw=1.5, ls='--', dashes=(18,10), alpha=0.6, zorder=3)
        else:
            pts = track.get_points()
            if len(pts) < 3: return
            pp = [self.cm2px(x,y) for x,y in pts]
            self.ax.add_patch(patches.Polygon(pp, closed=True,
                facecolor='#2a2a2a', alpha=0.9, edgecolor='white', lw=3, zorder=2))

    # ── start / finish line (checkered) ─────────────────────
    def draw_start_line(self):
        if not SHOW_START_LINE: return
        x1,y1 = self.cm2px(START_LINE_X, START_LINE_Y1)
        x2,y2 = self.cm2px(START_LINE_X, START_LINE_Y2)
        # checkered pattern
        sq = 18
        n  = max(1, int(abs(y2-y1)/sq))
        ylo, yhi = min(y1,y2), max(y1,y2)
        for i in range(n):
            ys = ylo + i*sq; ye = min(ys+sq, yhi)
            c  = 'black' if i%2==0 else 'white'
            self.ax.plot([x1,x1],[ys,ye], color=c, lw=9, solid_capstyle='butt', zorder=10)
        self.ax.plot([x1,x1],[ylo,yhi], color='#00FF00', lw=2, zorder=11)
        self.ax.text(x1+12, ylo-10, 'START/FINISH',
            fontsize=9, color='#00FF00', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='black', alpha=0.7), zorder=12)

    # ── anchors ─────────────────────────────────────────────
    def draw_anchors(self, anchors):
        for a in anchors:
            px,py = self.cm2px(a.x, a.y)
            self.ax.plot(px, py, 'o', ms=10, color='white', zorder=8)
            self.ax.text(px+8, py-8, f"{a.name}\n({int(a.x)},{int(a.y)})",
                fontsize=7, color='#aaaaaa', zorder=8)

    # ── tags ────────────────────────────────────────────────
    def draw_tags(self, tags, race_manager, speed_manager, collision_engine, now):
        for tag in tags:
            if not (tag.status and tag.is_active(TAG_TIMEOUT)):
                continue
            px, py = self.cm2px(tag.x, tag.y)
            color  = TAG_COLORS[tag.id % len(TAG_COLORS)]

            # trail
            if len(tag.history) >= 2:
                hx = [self.cm2px(h[0],h[1])[0] for h in tag.history]
                hy = [self.cm2px(h[0],h[1])[1] for h in tag.history]
                self.ax.plot(hx, hy, '-', color=color, lw=1.5, alpha=0.4, zorder=6)

            # dot
            self.ax.add_patch(plt.Circle((px,py), TAG_RADIUS,
                color=color, zorder=10))

            # collision flash
            ce = collision_engine.car_events(tag.id) if collision_engine else []
            recent = [e for e in ce if now-e['time'] < 0.8]
            if recent:
                self.ax.add_patch(plt.Circle((px,py), COLLISION_INDICATOR_RADIUS,
                    fill=False, edgecolor='red', lw=3, zorder=11))

            # label
            lap_info   = race_manager.get_car_info(tag.id, now)
            speed_info = speed_manager.get_car_speed_info(tag.id) if speed_manager else None
            score_sum  = race_manager.scoring.get_car_summary(tag.id)

            lines = [f"{tag.name} ({int(tag.x)},{int(tag.y)})"]
            if lap_info:
                if lap_info['is_racing']:
                    elapsed = lap_info['current_lap_elapsed']
                    lines.append(f"Lap {lap_info['current_lap']}/{TOTAL_LAPS}  {elapsed:.1f}s")
                elif lap_info['race_finished']:
                    elp = score_sum['best_elp']
                    lines.append(f"FINISHED  BestELP={elp:.2f}s")
            if speed_info:
                spd = speed_info.get('instantaneous', 0)
                lines.append(f"Speed: {spd:.1f} {speed_info.get('unit','')}")
            # wall penalty live counter
            wh = len(collision_engine.wall_hits(tag.id)) if collision_engine else 0
            if wh:
                lines.append(f"💥 Wall×{wh} (+{wh*WALL_HIT_PENALTY:.0f}s pen)")

            self.ax.text(px+TAG_RADIUS+4, py, '\n'.join(lines),
                fontsize=7.5, color=color, va='center',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a2e', alpha=0.7), zorder=12)

    # ── race status banner ──────────────────────────────────
    def draw_status(self, race_manager, now):
        if race_manager.is_race_active():
            elapsed = now - (race_manager.race_start_time or now)
            txt = f"● RACE IN PROGRESS  {elapsed:.1f}s"
            fc  = '#003300'
        else:
            txt = "⏸  WAITING FOR RACE START"
            fc  = '#1a1a3e'
        self.ax.text(SCREEN_X*0.5, SCREEN_Y*0.04, txt,
            fontsize=13, fontweight='bold', color='white', ha='center',
            bbox=dict(boxstyle='round,pad=0.4', facecolor=fc, edgecolor='white', lw=1.5),
            zorder=20)

    # ── ELP leaderboard ─────────────────────────────────────
    def draw_leaderboard(self, race_manager, now):
        lb = race_manager.get_leaderboard()
        lines  = ["═══ BEST-ELP LEADERBOARD ═══",
                  f"{'Pos':<4}{'Car':<8}{'BestELP':>9}{'Laps':>6}{'Qlfy':>6}",
                  "─"*38]
        for i, row in enumerate(lb):
            elp  = row['best_elp']
            elps = f"{elp:.2f}s" if elp < float('inf') else "—"
            q    = "✓" if row['qualifies'] else f"({row['laps_done']}/{MIN_LAPS_TO_QUALIFY})"
            lines.append(f"{i+1:<4}{row['car_name']:<8}{elps:>9}{row['laps_done']:>6}{q:>6}")

        # live lap info
        lines += ["", "─── LIVE LAPS ───"]
        legacy = race_manager.get_legacy_leaderboard(now)
        for r in legacy:
            if r['is_racing']:
                el = r.get('current_lap_elapsed', 0)
                lines.append(f"  {r['car_name']} Lap {r['current_lap']}/{TOTAL_LAPS}  {el:.1f}s")

        self.ax.text(LEADERBOARD_X, LEADERBOARD_Y, '\n'.join(lines),
            fontsize=8, family='monospace', color='white', va='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#111130', edgecolor='#4444aa', lw=1.5),
            zorder=20)

    # ── incident feed ────────────────────────────────────────
    def draw_incident_feed(self, scoring_engine):
        feed = scoring_engine.get_feed(8)
        if not feed: return
        lines = ["═══ INCIDENT FEED ═══"] + [f"  {m}" for m in reversed(feed)]
        self.ax.text(INCIDENT_X, INCIDENT_Y, '\n'.join(lines),
            fontsize=7.5, family='monospace', color='#ffdddd', va='top',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#200000', edgecolor='#880000', lw=1),
            zorder=20)

    # ── main render ─────────────────────────────────────────
    def render_frame(self, anchors, tags, race_manager, speed_manager,
                     collision_engine, show_debug=False, track=None):
        now = time.time()
        self.ax.clear()
        self.ax.set_xlim(0, SCREEN_X); self.ax.set_ylim(0, SCREEN_Y)
        self.ax.set_aspect('equal'); self.ax.invert_yaxis()
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.ax.set_facecolor('#1a1a2e')

        self.draw_grid()
        self.draw_track(track)
        self.draw_start_line()
        self.draw_anchors(anchors)
        self.draw_tags(tags, race_manager, speed_manager, collision_engine, now)
        self.draw_status(race_manager, now)
        self.draw_leaderboard(race_manager, now)
        self.draw_incident_feed(race_manager.scoring)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
        plt.pause(0.001)

    def close(self):
        plt.close(self.fig)