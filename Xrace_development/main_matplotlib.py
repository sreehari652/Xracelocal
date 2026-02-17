"""
UWB Racing Tracker  –  main_matplotlib.py
Full implementation of the Technical Analysis & Validation Framework

Controls (type in console + Enter):
  start        – arm all cars (admin Start signal)
  q / quit     – quit
  r / reset    – reset race
  s / stats    – full statistics
  l / laps     – lap times
  f / feed     – incident feed
  lb           – ELP leaderboard
  d / debug    – toggle debug info
"""

import time, math, threading
from config         import *
from race_config    import *
from uwb_device     import Anchor, Tag
from network        import UDPReceiver
from scoring_engine import ScoringEngine
from lap_engine     import RaceManager
from collision_engine import CollisionEngine
from speed_tracker  import SpeedManager
from track_loader   import get_track
from matplotlib_renderer import MatplotlibRenderer


class RacingTrackerMatplotlib:

    def __init__(self):
        print("\n" + "="*60)
        print("🏎️   UWB RACING TRACKER  –  Full Framework Edition")
        print("="*60)
        print(f"  Cars: {TAG_COUNT}   Anchors: {ANCHOR_COUNT}   Laps: {TOTAL_LAPS}")
        print(f"  Wall penalty:  +{WALL_HIT_PENALTY}s per hit")
        print(f"  Atk penalty:   +{CAR_COLLISION_ATTACKER_PENALTY}s | "
              f"Victim bonus: -{CAR_COLLISION_VICTIM_BONUS}s")
        print(f"  Min laps to qualify: {MIN_LAPS_TO_QUALIFY}")
        print("="*60 + "\n")

        # ── Anchors & Tags ────────────────────────────────────
        self.anchors = [Anchor(i, *ANCHOR_POSITIONS[i]) for i in range(ANCHOR_COUNT)]
        self.tags    = [Tag(i) for i in range(TAG_COUNT)]

        print("Track Configuration:")
        for a in self.anchors:
            print(f"  {a.name}: ({a.x}, {a.y}) cm")
        print()

        # ── Engines ──────────────────────────────────────────
        self.scoring   = ScoringEngine()
        self.race_mgr  = RaceManager(self.scoring)
        self.col_engine = CollisionEngine()
        self.spd_mgr   = SpeedManager()

        for tag in self.tags:
            self.race_mgr.register_car(tag.id, tag.name)
            self.spd_mgr.register_car(tag.id, tag.name)
            self.col_engine.register(tag.id, tag.name)

        # ── Track ─────────────────────────────────────────────
        self.track = get_track('oval')
        self.col_engine.set_track(self.track)
        self.col_engine.set_scoring(self.scoring)
        print(f"✓ Track: {self.track.name if self.track else 'None'}")
        if self.track and self.track.has_width():
            print("  Wide track with inner/outer walls enabled")

        # ── Display ──────────────────────────────────────────
        scale = self._calc_scale()
        self.renderer = MatplotlibRenderer(scale)

        # ── Network ──────────────────────────────────────────
        self.udp = UDPReceiver(UDP_PORT, self.tags)

        # ── App state ────────────────────────────────────────
        self.running      = True
        self.show_debug   = False
        self.last_refresh = time.time()

        # ── Logging ──────────────────────────────────────────
        self.log_file = None
        if ENABLE_RACE_LOGGING:
            self._init_log()

        # ── Command thread ────────────────────────────────────
        self._cmd_thread = threading.Thread(target=self._cmd_loop, daemon=True)
        self._cmd_thread.start()

        print("\nSystem ready!  Type 'start' to arm the race.\n")
        self._print_help()

    # ─────────────────────────────────────────────────────────
    # Scaling
    # ─────────────────────────────────────────────────────────
    def _calc_scale(self):
        uw = SCREEN_X * 0.65
        uh = SCREEN_Y * 0.85
        tw = max(a.x for a in self.anchors) - min(a.x for a in self.anchors)
        th = max(a.y for a in self.anchors) - min(a.y for a in self.anchors)
        cm2p = min(uw/tw if tw else 1, uh/th if th else 1)
        xoff = (uw - tw*cm2p)/2 + 50
        yoff = (uh - th*cm2p)/2 + 50
        print(f"Display: {cm2p:.3f} px/cm  track {tw}×{th}cm")
        return dict(cm2p=cm2p, x_offset=xoff, y_offset=yoff)

    # ─────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────
    def _init_log(self):
        try:
            self.log_file = open(RACE_LOG_FILE, 'w')
            self.log_file.write("ts,car_id,car_name,event,lap,value,detail\n")
            print(f"📋 Logging → {RACE_LOG_FILE}")
        except Exception as e:
            print(f"⚠️  Log init failed: {e}")

    def _log(self, car_id, event, lap=0, value='', detail=''):
        if not self.log_file: return
        try:
            name = self.tags[car_id].name if car_id < len(self.tags) else f"Car{car_id}"
            self.log_file.write(f"{time.time():.3f},{car_id},{name},{event},{lap},{value},{detail}\n")
            self.log_file.flush()
        except Exception: pass

    # ─────────────────────────────────────────────────────────
    # Command loop
    # ─────────────────────────────────────────────────────────
    def _print_help(self):
        print("Commands: start | r/reset | s/stats | l/laps | f/feed | lb | d/debug | q/quit")

    def _cmd_loop(self):
        while self.running:
            try:
                cmd = input().strip().lower()
                if cmd in ('q','quit'):
                    self.running = False
                elif cmd == 'start':
                    self.race_mgr.admin_start()
                elif cmd in ('r','reset'):
                    self._reset()
                elif cmd in ('s','stats'):
                    self._print_stats()
                elif cmd in ('l','laps'):
                    self._print_laps()
                elif cmd in ('f','feed'):
                    self._print_feed()
                elif cmd == 'lb':
                    self._print_elp_lb()
                elif cmd in ('d','debug'):
                    self.show_debug = not self.show_debug
                    print(f"Debug: {self.show_debug}")
                else:
                    self._print_help()
            except (EOFError, KeyboardInterrupt):
                break

    # ─────────────────────────────────────────────────────────
    # Main update
    # ─────────────────────────────────────────────────────────
    def update(self):
        now = time.time()

        cars_data = {}
        for tag in self.tags:
            if not (tag.status and tag.is_active(TAG_TIMEOUT)):
                continue

            # Speed
            spd_info = self.spd_mgr.update_car_speed(tag.id, tag.x, tag.y, now)
            spd_cms  = spd_info.get('instantaneous_cms', 0) if spd_info else 0

            # Lap engine
            event = self.race_mgr.update_car(tag.id, tag.x, tag.y, spd_cms, now)
            if event:
                self._log(tag.id, event['type'], event.get('lap',0),
                          f"{event.get('raw_time',0):.3f}",
                          f"elp={event.get('elp',0):.3f}")

            lap_info = self.race_mgr.get_car_info(tag.id, now)
            is_racing = lap_info['is_racing'] if lap_info else False

            cars_data[tag.id] = dict(
                x=tag.x, y=tag.y,
                speed=spd_cms,
                lap=lap_info['current_lap'] if lap_info else 0,
                racing=is_racing
            )

        # Collision engine
        if cars_data:
            new_evts = self.col_engine.update(cars_data, now)
            for e in new_evts:
                if e['type'] == 'wall':
                    self._log(e['car_id'], 'wall_hit', e['lap'])
                elif e['type'] == 'car':
                    self._log(e['attacker'], 'car_atk', e['lap'],
                              detail=f"victim={e['victim']}")
                    self._log(e['victim'],   'car_vic', e['lap'],
                              detail=f"attacker={e['attacker']}")

    # ─────────────────────────────────────────────────────────
    # Console print helpers
    # ─────────────────────────────────────────────────────────
    def _reset(self):
        self.race_mgr.reset_race()
        self.col_engine.reset()
        self.spd_mgr.reset_all()
        for tag in self.tags:
            tag.reset_history()
        print("✓ Race reset")

    def _print_stats(self):
        now = time.time()
        print("\n" + "="*60)
        print("📊 RACE STATISTICS")
        print("="*60)
        for tag in self.tags:
            if not tag.is_active(5): continue
            info   = self.race_mgr.get_car_info(tag.id, now)
            s_info = self.spd_mgr.get_car_speed_info(tag.id)
            sc     = self.scoring.get_car_summary(tag.id)
            print(f"\n{tag.name}:")
            if info:
                print(f"  Laps done: {info['laps_done']}/{TOTAL_LAPS}")
                print(f"  Best raw:  {info['best_raw']:.2f}s")
            if sc:
                e = sc['best_elp']
                print(f"  Best ELP:  {e:.2f}s" if e < float('inf') else "  Best ELP: —")
                print(f"  Qualifies: {'Yes' if sc['qualifies'] else 'No'}")
            wh = len(self.col_engine.wall_hits(tag.id))
            ce = len(self.col_engine.car_events(tag.id))
            if wh: print(f"  Wall hits: {wh} (+{wh*WALL_HIT_PENALTY:.0f}s penalty)")
            if ce: print(f"  Car events: {ce}")
            if s_info:
                print(f"  Speed: {s_info.get('instantaneous',0):.1f} {s_info.get('unit','')}")
        print("="*60)

    def _print_laps(self):
        print("\n" + "="*60 + "\n⏱  LAP TIMES")
        for tag in self.tags:
            sc = self.scoring.get_car_summary(tag.id)
            if not sc or not sc['history']: continue
            print(f"\n{sc['car_name']}:")
            for lap in sc['history']:
                vo = " [VOIDED]" if lap['voided'] else ""
                print(f"  Lap {lap['lap']:2d}:  raw={lap['raw']:.2f}s  "
                      f"pen=+{lap['penalty']:.1f}s  bon=-{lap['bonus']:.1f}s  "
                      f"ELP={lap['elp']:.2f}s{vo}")
        print("="*60)

    def _print_feed(self):
        print("\n── INCIDENT FEED ──")
        for msg in self.scoring.get_feed(20):
            print(f"  {msg}")

    def _print_elp_lb(self):
        lb = self.race_mgr.get_leaderboard()
        print("\n── ELP LEADERBOARD ──")
        print(f"{'Pos':<4}{'Car':<10}{'BestELP':>9}{'Laps':>6}{'Q':>4}")
        for i, r in enumerate(lb):
            e = f"{r['best_elp']:.2f}s" if r['best_elp'] < float('inf') else "—"
            q = "✓" if r['qualifies'] else "✗"
            print(f"{i+1:<4}{r['car_name']:<10}{e:>9}{r['laps_done']:>6}{q:>4}")

    # ─────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────
    def run(self):
        print("🏁 Tracker running. Type 'start' to begin race.\n")
        try:
            while self.running:
                self.update()
                if time.time() - self.last_refresh > REFRESH_RATE:
                    self.renderer.render_frame(
                        self.anchors, self.tags,
                        self.race_mgr, self.spd_mgr,
                        self.col_engine,
                        show_debug=self.show_debug,
                        track=self.track
                    )
                    self.last_refresh = time.time()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n⚠️  Interrupted")
        finally:
            self._shutdown()

    def _shutdown(self):
        print("\n🛑 Shutting down…")
        self.udp.stop()
        if self.log_file:
            self.log_file.close()
            print(f"✓ Log saved → {RACE_LOG_FILE}")
        self.renderer.close()
        self._print_elp_lb()
        print("✓ Done")


def main():
    RacingTrackerMatplotlib().run()

if __name__ == "__main__":
    main()