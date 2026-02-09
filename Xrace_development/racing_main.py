"""
UWB Racing Tracker - Main Racing Application
Integrates lap timing, speed tracking, and collision detection

Controls:
- ESC or Q: Quit
- D: Toggle debug mode
- R: Reset race
- S: Print statistics
- L: Print lap times
- C: Print collision report
- P: Print points leaderboard
"""

import pygame
import time
import math

# Import base modules
from config import *
from uwb_device import Anchor, Tag
from network import UDPReceiver
from renderer import Renderer

# Import racing modules
from race_config import *
from lap_tracker import RaceManager
from speed_tracker import SpeedManager
from collision_detector import CollisionDetector
from race_renderer import RaceRenderer


class RacingTracker:
    """Main racing application class"""

    def __init__(self):
        """Initialize the UWB racing tracking system"""
        print("\n" + "=" * 60)
        print("🏎️  UWB RACING TRACKER - Enhanced Edition")
        print("=" * 60)
        print(f"Configuration:")
        print(f"  Cars: {TAG_COUNT}")
        print(f"  Anchors: {ANCHOR_COUNT}")
        print(f"  Total Laps: {TOTAL_LAPS}")
        print(f"  Collision Detection: {'ENABLED' if COLLISION_DISTANCE_THRESHOLD else 'DISABLED'}")
        print("=" * 60 + "\n")

        # Initialize Pygame
        pygame.init()
        self.screen = pygame.display.set_mode([SCREEN_X, SCREEN_Y])
        pygame.display.set_caption("UWB Racing Tracker - Professional")
        self.clock = pygame.time.Clock()

        # Create anchors
        self.anchors = []
        for i in range(ANCHOR_COUNT):
            x, y = ANCHOR_POSITIONS[i]
            self.anchors.append(Anchor(i, x, y))

        # Create tags (racing cars)
        self.tags = []
        for i in range(TAG_COUNT):
            self.tags.append(Tag(i))

        print("Track Configuration:")
        for anchor in self.anchors:
            print(f"  {anchor.name}: ({anchor.x}, {anchor.y}) cm")
        print()

        # Calculate display scaling
        scale_params = self._calculate_scaling()
        
        # Initialize base renderer
        base_renderer = Renderer(self.screen, scale_params)
        
        # Initialize racing renderer
        self.race_renderer = RaceRenderer(base_renderer)

        # Initialize racing modules
        self.race_manager = RaceManager()
        self.speed_manager = SpeedManager()
        self.collision_detector = CollisionDetector()
        
        # Link collision detector with speed manager
        self.collision_detector.set_speed_manager(self.speed_manager)

        # Register all cars in racing systems
        for tag in self.tags:
            self.race_manager.register_car(tag.id, tag.name)
            self.speed_manager.register_car(tag.id, tag.name)
            self.collision_detector.register_car(tag.id, tag.name)

        # Initialize network receiver
        self.udp_receiver = UDPReceiver(UDP_PORT, self.tags)

        # Application state
        self.running = True
        self.show_debug = SHOW_RANGE_CIRCLES or SHOW_COLLISION_CIRCLES
        self.last_refresh = time.time()

        # Data logging
        if ENABLE_RACE_LOGGING:
            self._init_logging()

        print("System Ready!")
        self._print_controls()

    def _calculate_scaling(self):
        """Calculate display scaling parameters"""
        center_x = sum(a.x for a in self.anchors) / len(self.anchors)
        center_y = sum(a.y for a in self.anchors) / len(self.anchors)

        max_radius = 0
        for anchor in self.anchors:
            dist = math.sqrt((anchor.x - center_x) ** 2 + (anchor.y - center_y) ** 2)
            max_radius = max(max_radius, dist)

        cm2p = min(SCREEN_X - 400, SCREEN_Y - 50) / 2 * 0.85 / max_radius
        #x_offset = 350 + (SCREEN_X - 400) / 2 - center_x * cm2p
        LEFT_MARGIN = 60
        RIGHT_PANEL_WIDTH = 420  # space for leaderboard

        usable_width = SCREEN_X - RIGHT_PANEL_WIDTH - LEFT_MARGIN
        x_offset = LEFT_MARGIN + (usable_width / 2) - center_x * cm2p

        y_offset = (SCREEN_Y / 2) - center_y * cm2p
        center_x_pixel = int(center_x * cm2p + x_offset)
        center_y_pixel = int(center_y * cm2p + y_offset)

        print(f"Display Configuration:")
        print(f"  Scaling: {cm2p:.3f} pixels/cm")
        max_x = max(a.x for a in self.anchors)
        max_y = max(a.y for a in self.anchors)
        print(f"  Track area: {max_x}cm × {max_y}cm")
        print()

        return {
            'cm2p': cm2p,
            'x_offset': x_offset,
            'y_offset': y_offset,
            'center_x_pixel': center_x_pixel,
            'center_y_pixel': center_y_pixel
        }

    def _init_logging(self):
        """Initialize race data logging"""
        try:
            self.log_file = open(RACE_LOG_FILE, 'w')
            self.log_file.write('timestamp,car_id,car_name,event_type,lap,value,details\n')
            print(f"📊 Race logging enabled: {RACE_LOG_FILE}")
        except Exception as e:
            print(f"Warning: Could not initialize logging: {e}")
            self.log_file = None

    def _log_event(self, car_id, event_type, lap, value='', details=''):
        """Log a race event"""
        if not ENABLE_RACE_LOGGING or not self.log_file:
            return
        
        try:
            car_name = self.tags[car_id].name if car_id < len(self.tags) else f"Car{car_id}"
            timestamp = time.time()
            self.log_file.write(f"{timestamp},{car_id},{car_name},{event_type},"
                              f"{lap},{value},{details}\n")
            self.log_file.flush()
        except Exception as e:
            print(f"Logging error: {e}")

    def _print_controls(self):
        """Print control instructions"""
        print("\n" + "=" * 60)
        print("Controls:")
        print("  ESC or Q - Quit application")
        print("  D - Toggle debug mode (collision circles, etc.)")
        print("  R - Reset race (clear all data)")
        print("  S - Print full statistics")
        print("  L - Print lap times for all cars")
        print("  C - Print collision report")
        print("  P - Print points leaderboard")
        print("=" * 60 + "\n")

    def handle_events(self):
        """Handle user input events"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                    self.running = False
                    
                elif event.key == pygame.K_d:
                    self.show_debug = not self.show_debug
                    print(f"🔧 Debug mode: {'ON' if self.show_debug else 'OFF'}")
                    
                elif event.key == pygame.K_r:
                    self.reset_race()
                    
                elif event.key == pygame.K_s:
                    self.print_full_statistics()
                    
                elif event.key == pygame.K_l:
                    self.print_lap_times()
                    
                elif event.key == pygame.K_c:
                    self.print_collision_report()
                    
                elif event.key == pygame.K_p:
                    self.print_points_leaderboard()

    def update_race_systems(self):
        """Update all racing systems"""
        current_time = time.time()
        
        # Collect car data for collision detection
        cars_data = {}
        
        for tag in self.tags:
            if not tag.is_active(1):
                continue
            
            # Update position calculations
            if tag.range_list and len(tag.range_list) > 0:
                tag.calculate_position(self.anchors)
            
            # Update speed tracking
            self.speed_manager.update_car_position(tag.id, tag.x, tag.y, current_time)
            
            # Update lap tracking
            lap_event = self.race_manager.update_car_position(
                tag.id, tag.x, tag.y, current_time
            )
            
            # Handle lap completion
            if lap_event and lap_event['type'] == 'lap_completed':
                lap_info = lap_event['lap_info']
                
                # Notify speed manager
                self.speed_manager.on_lap_complete(tag.id)
                
                # Process lap collisions
                self.collision_detector.process_lap_complete(
                    tag.id, lap_info['current_lap'] - 1  # Previous lap
                )
                
                # Log lap completion
                if LOG_LAP_TIMES:
                    self._log_event(tag.id, 'lap_complete', lap_info['current_lap'] - 1,
                                  lap_info['last_lap_time'], '')
            
            # Prepare data for collision detection
            lap_info = self.race_manager.get_car_lap_info(tag.id)
            if lap_info and lap_info['is_racing']:
                cars_data[tag.id] = {
                    'x': tag.x,
                    'y': tag.y,
                    'lap': lap_info['current_lap']
                }
        
        # Update collision detection
        if cars_data:
            new_collisions = self.collision_detector.update(cars_data, current_time)
            
            # Log collisions
            if LOG_COLLISIONS:
                for collision in new_collisions:
                    self._log_event(
                        collision.initiator_id, 'collision',
                        collision.lap_number, '',
                        f"with_car_{collision.victim_id}"
                    )

    def reset_race(self):
        """Reset race data"""
        print("\n" + "=" * 60)
        print("🔄 RESETTING RACE...")
        
        # Reset all racing systems
        self.race_manager.reset_race()
        self.speed_manager.reset_all()
        self.collision_detector.reset_all()
        
        # Reset tag histories
        for tag in self.tags:
            tag.reset_history()
        
        print("✓ Race reset complete - ready for new race")
        print("=" * 60 + "\n")

    def print_full_statistics(self):
        """Print comprehensive race statistics"""
        print("\n" + "=" * 60)
        print("📊 FULL RACE STATISTICS")
        print("=" * 60)
        
        # Race status
        print(f"\n🏁 Race Status: {'ACTIVE' if self.race_manager.is_race_active() else 'WAITING'}")
        
        # Leaderboard
        leaderboard = self.race_manager.get_leaderboard()
        print(f"\n🏆 Current Standings:")
        for idx, car_info in enumerate(leaderboard):
            print(f"  {idx + 1}. {car_info['car_name']}: "
                  f"Lap {car_info['current_lap']}/{car_info['total_laps']}, "
                  f"Time: {car_info['total_time']:.2f}s")
        
        # Individual car stats
        print(f"\n📈 Individual Statistics:")
        for tag in self.tags:
            if not tag.is_active(3):
                continue
                
            print(f"\n  {tag.name}:")
            
            # Lap info
            lap_info = self.race_manager.get_car_lap_info(tag.id)
            if lap_info:
                print(f"    Laps: {lap_info['total_laps_completed']}/{TOTAL_LAPS}")
                if lap_info['best_lap_time'] > 0:
                    print(f"    Best Lap: {lap_info['best_lap_time']:.2f}s")
            
            # Speed info
            speed_info = self.speed_manager.get_car_speed_info(tag.id)
            if speed_info:
                print(f"    Current Speed: {speed_info['instantaneous']:.1f} {speed_info['unit']}")
                print(f"    Max Speed: {speed_info['max']:.1f} {speed_info['unit']}")
            
            # Collision info
            collision_info = self.collision_detector.get_car_collision_info(tag.id)
            if collision_info:
                print(f"    Collisions: {collision_info['total_collisions']} "
                      f"(Init: {collision_info['initiated']}, Recv: {collision_info['received']})")
                print(f"    Points: {collision_info['points']}")
        
        print("=" * 60 + "\n")

    def print_lap_times(self):
        """Print detailed lap times"""
        print("\n" + "=" * 60)
        print("⏱️  LAP TIMES")
        print("=" * 60)
        
        all_lap_info = self.race_manager.get_all_lap_info()
        
        for car_id, lap_info in all_lap_info.items():
            print(f"\n{lap_info['car_name']}:")
            
            if not lap_info['lap_times']:
                print("  No laps completed yet")
                continue
            
            for idx, lap_time in enumerate(lap_info['lap_times']):
                lap_num = idx + 1
                marker = " 🏆" if lap_time == lap_info['best_lap_time'] else ""
                print(f"  Lap {lap_num}: {lap_time:.2f}s{marker}")
            
            if lap_info['lap_times']:
                avg_time = sum(lap_info['lap_times']) / len(lap_info['lap_times'])
                print(f"  Average: {avg_time:.2f}s")
                print(f"  Best: {lap_info['best_lap_time']:.2f}s")
        
        print("=" * 60 + "\n")

    def print_collision_report(self):
        """Print detailed collision report"""
        print("\n" + "=" * 60)
        print("💥 COLLISION REPORT")
        print("=" * 60)
        
        all_collision_info = self.collision_detector.get_all_collision_info()
        
        total_collisions = sum(info['total_collisions'] for info in all_collision_info.values())
        print(f"\nTotal Collisions: {total_collisions}")
        
        for car_id, info in all_collision_info.items():
            print(f"\n{info['car_name']}:")
            print(f"  Total: {info['total_collisions']}")
            print(f"  Initiated: {info['initiated']}")
            print(f"  Received: {info['received']}")
            print(f"  Points: {info['points']}")
            
            if info['collisions_per_lap']:
                print(f"  Per Lap: {dict(info['collisions_per_lap'])}")
        
        print("=" * 60 + "\n")

    def print_points_leaderboard(self):
        """Print points-based leaderboard"""
        print("\n" + "=" * 60)
        print("🏆 POINTS LEADERBOARD")
        print("=" * 60)
        
        leaderboard = self.collision_detector.get_points_leaderboard()
        
        for idx, car_data in enumerate(leaderboard):
            position = idx + 1
            medal = "🥇" if position == 1 else "🥈" if position == 2 else "🥉" if position == 3 else "  "
            
            print(f"{medal} {position}. {car_data['car_name']}: {car_data['points']:+4d} points "
                  f"({car_data['total_collisions']} collisions)")
        
        print("=" * 60 + "\n")

    def run(self):
        """Main application loop"""
        try:
            while self.running:
                # Handle events
                self.handle_events()

                # Update racing systems
                self.update_race_systems()

                # Refresh display
                if (time.time() - self.last_refresh) > REFRESH_RATE:
                    self.race_renderer.render_race_frame(
                        self.anchors, 
                        self.tags,
                        self.race_manager,
                        self.speed_manager,
                        self.collision_detector,
                        self.show_debug
                    )
                    self.last_refresh = time.time()

                # Maintain frame rate
                self.clock.tick(FPS)

        except KeyboardInterrupt:
            print("\n⚠️  Interrupted by user")

        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown"""
        print("\n🛑 Shutting down...")
        
        # Stop network receiver
        self.udp_receiver.stop()
        
        # Close log file
        if hasattr(self, 'log_file') and self.log_file:
            self.log_file.close()
            print(f"✓ Race data saved to {RACE_LOG_FILE}")
        
        # Print final statistics
        print("\n" + "=" * 60)
        print("FINAL RACE SUMMARY")
        print("=" * 60)
        self.print_lap_times()
        self.print_points_leaderboard()
        
        # Quit pygame
        pygame.quit()
        
        print("✓ Program terminated")


def main():
    """Entry point"""
    tracker = RacingTracker()
    tracker.run()


if __name__ == "__main__":
    main()
