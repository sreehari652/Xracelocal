"""
UWB Racing Tracker - Racing Renderer Module
Handles all racing-specific visualization
"""

import pygame
import time
import math
from config import *
from race_config import *


class RaceRenderer:
    """Extends basic renderer with racing-specific visualizations"""
    
    def __init__(self, base_renderer):
        """
        Initialize race renderer
        
        Args:
            base_renderer: Base Renderer object
        """
        self.base_renderer = base_renderer
        self.screen = base_renderer.screen
        
        # Initialize fonts
        self.font_race_title = pygame.font.SysFont("Consolas", 22, bold=True)
        self.font_lap_info = pygame.font.SysFont("Consolas", 14)
        self.font_speed = pygame.font.SysFont("Consolas", 12)
        self.font_leaderboard = pygame.font.SysFont("Consolas", 16, bold=True)
        self.font_leaderboard_item = pygame.font.SysFont("Consolas", 14)
    
    def draw_start_line(self):
        """Draw the start/finish line"""
        if not SHOW_START_LINE:
            return
        
        if START_LINE_ORIENTATION == 'vertical':
            # Vertical line
            x_pixel, y1_pixel = self.base_renderer.cm_to_pixels(START_LINE_X, START_LINE_Y1)
            _, y2_pixel = self.base_renderer.cm_to_pixels(START_LINE_X, START_LINE_Y2)
            
            # Draw line
            pygame.draw.line(self.screen, START_LINE_COLOR, 
                           (x_pixel, y1_pixel), (x_pixel, y2_pixel), 
                           START_LINE_WIDTH)
            
            # Draw "START/FINISH" text
            label = self.font_race_title.render("START/FINISH", True, START_LINE_COLOR)
            self.screen.blit(label, [x_pixel + 10, y1_pixel + 10])
            
            # Draw checkered pattern
            self._draw_checkered_pattern(x_pixel, y1_pixel, x_pixel, y2_pixel)
            
        else:  # horizontal
            x1_pixel, y_pixel = self.base_renderer.cm_to_pixels(START_LINE_X1, START_LINE_Y)
            x2_pixel, _ = self.base_renderer.cm_to_pixels(START_LINE_X2, START_LINE_Y)
            
            pygame.draw.line(self.screen, START_LINE_COLOR, 
                           (x1_pixel, y_pixel), (x2_pixel, y_pixel), 
                           START_LINE_WIDTH)
            
            label = self.font_race_title.render("START/FINISH", True, START_LINE_COLOR)
            self.screen.blit(label, [x1_pixel + 10, y_pixel - 25])
            
            self._draw_checkered_pattern(x1_pixel, y_pixel, x2_pixel, y_pixel)
    
    def _draw_checkered_pattern(self, x1, y1, x2, y2):
        """Draw checkered pattern along the start line"""
        # Simplified checkered pattern
        square_size = 10
        
        if x1 == x2:  # Vertical line
            num_squares = int((y2 - y1) / square_size)
            for i in range(num_squares):
                y = y1 + i * square_size
                color = WHITE if i % 2 == 0 else BLACK
                pygame.draw.rect(self.screen, color, 
                               (x1 - square_size//2, y, square_size, square_size))
        else:  # Horizontal line
            num_squares = int((x2 - x1) / square_size)
            for i in range(num_squares):
                x = x1 + i * square_size
                color = WHITE if i % 2 == 0 else BLACK
                pygame.draw.rect(self.screen, color, 
                               (x, y1 - square_size//2, square_size, square_size))
    
    def draw_car_lap_info(self, tag, lap_info):
        """
        Draw lap information next to car
        
        Args:
            tag: Tag object
            lap_info: Lap information dict
        """
        pixel_x, pixel_y = self.base_renderer.cm_to_pixels(tag.x, tag.y)
        
        # Draw lap number
        if lap_info['is_racing']:
            lap_text = f"Lap {lap_info['current_lap']}/{lap_info['total_laps']}"
            lap_surf = self.font_lap_info.render(lap_text, True, BLUE)
            self.screen.blit(lap_surf, 
                           [pixel_x + LAP_DISPLAY_OFFSET_X, 
                            pixel_y + LAP_DISPLAY_OFFSET_Y])
        elif lap_info['race_finished']:
            finish_text = "FINISHED"
            finish_surf = self.font_lap_info.render(finish_text, True, GREEN)
            self.screen.blit(finish_surf, 
                           [pixel_x + LAP_DISPLAY_OFFSET_X, 
                            pixel_y + LAP_DISPLAY_OFFSET_Y])
    
    def draw_car_speed(self, tag, speed_info):
        """
        Draw speed information next to car
        
        Args:
            tag: Tag object
            speed_info: Speed information dict
        """
        pixel_x, pixel_y = self.base_renderer.cm_to_pixels(tag.x, tag.y)
        
        # Show instantaneous and average speed
        if SPEED_CALC_METHOD == 'both':
            speed_text = (f"S: {speed_info['instantaneous']:.1f} "
                         f"(Avg: {speed_info['average']:.1f})")
        elif SPEED_CALC_METHOD == 'instantaneous':
            speed_text = f"S: {speed_info['instantaneous']:.1f} {speed_info['unit']}"
        else:  # average
            speed_text = f"S: {speed_info['average']:.1f} {speed_info['unit']}"
        
        speed_surf = self.font_speed.render(speed_text, True, GRAY)
        self.screen.blit(speed_surf, 
                       [pixel_x + SPEED_DISPLAY_OFFSET_X, 
                        pixel_y + SPEED_DISPLAY_OFFSET_Y])
    
    def draw_collision_indicator(self, tag, collision_info):
        """
        Draw collision indicator around car
        
        Args:
            tag: Tag object
            collision_info: Collision information dict
        """
        if not collision_info['is_in_collision']:
            return
        
        pixel_x, pixel_y = self.base_renderer.cm_to_pixels(tag.x, tag.y)
        
        # Flashing red circle
        alpha = int(128 + 127 * math.sin(time.time() * 10))
        
        # Draw collision indicator
        pygame.draw.circle(self.screen, COLLISION_INDICATOR_COLOR, 
                         [pixel_x, pixel_y], COLLISION_INDICATOR_RADIUS, 3)
    
    def draw_collision_debug(self, cars_positions):
        """
        Draw collision detection circles (debug)
        
        Args:
            cars_positions: List of (tag, x, y) tuples
        """
        if not SHOW_COLLISION_CIRCLES:
            return
        
        for tag, x, y in cars_positions:
            pixel_x, pixel_y = self.base_renderer.cm_to_pixels(x, y)
            pygame.draw.circle(self.screen, (255, 200, 200), 
                             [pixel_x, pixel_y], 
                             int(COLLISION_DISTANCE_THRESHOLD * self.base_renderer.cm2p), 
                             1)
    
    def draw_race_status(self, race_manager):
        """
        Draw overall race status
        
        Args:
            race_manager: RaceManager object
        """
        y_pos = RACE_STATUS_Y
        
        # Race status
        if race_manager.is_race_active():
            status_text = "🏁 RACE IN PROGRESS"
            color = GREEN
            
            # Show race time
            if race_manager.race_start_time:
                elapsed = time.time() - race_manager.race_start_time
                time_text = f"Time: {elapsed:.1f}s"
                time_surf = self.font_race_title.render(time_text, True, BLUE)
                self.screen.blit(time_surf, [RACE_STATUS_X, y_pos + 25])
        else:
            status_text = "⏸️  WAITING FOR RACE START"
            color = ORANGE
        
        status_surf = self.font_race_title.render(status_text, True, color)
        self.screen.blit(status_surf, [RACE_STATUS_X, y_pos])
    
    def draw_leaderboard(self, race_manager, collision_detector):
        """
        Draw race leaderboard
        
        Args:
            race_manager: RaceManager object
            collision_detector: CollisionDetector object
        """
        y_pos = LEADERBOARD_Y
        x_pos = LEADERBOARD_X
        
        # Title
        title = self.font_leaderboard.render("LEADERBOARD", True, BLUE)
        self.screen.blit(title, [x_pos, y_pos])
        y_pos += 30
        
        # Get leaderboard data
        leaderboard = race_manager.get_leaderboard()
        
        # Draw header
        header = "Pos  Car       Lap    Time    Points"
        header_surf = self.font_leaderboard_item.render(header, True, BLACK)
        self.screen.blit(header_surf, [x_pos, y_pos])
        y_pos += 25
        
        # Draw separator
        pygame.draw.line(self.screen, GRAY, 
                        (x_pos, y_pos), (x_pos + 350, y_pos), 2)
        y_pos += 10
        
        # Draw each car
        for idx, car_info in enumerate(leaderboard):
            position = idx + 1
            car_name = car_info['car_name']
            current_lap = car_info['current_lap']
            total_laps = car_info['total_laps']
            total_time = car_info['total_time']
            
            # Get collision info for points
            collision_info = collision_detector.get_car_collision_info(car_info['car_id'])
            points = collision_info['points'] if collision_info else 0
            
            # Format line
            if car_info['race_finished']:
                lap_str = "FIN"
            else:
                lap_str = f"{current_lap}/{total_laps}"
            
            line_text = f"{position}.   {car_name}    {lap_str:6}  {total_time:6.1f}s  {points:+4d}"
            
            # Color based on position
            if position == 1:
                color = [255, 215, 0]  # Gold
            elif position == 2:
                color = [192, 192, 192]  # Silver
            elif position == 3:
                color = [205, 127, 50]  # Bronze
            else:
                color = BLACK
            
            line_surf = self.font_leaderboard_item.render(line_text, True, color)
            self.screen.blit(line_surf, [x_pos, y_pos])
            y_pos += 20
        
        # Draw collision summary
        y_pos += 20
        collision_title = self.font_leaderboard.render("COLLISIONS", True, RED)
        self.screen.blit(collision_title, [x_pos, y_pos])
        y_pos += 25
        
        for car_info in leaderboard:
            collision_info = collision_detector.get_car_collision_info(car_info['car_id'])
            if collision_info:
                col_text = (f"{collision_info['car_name']}: "
                           f"{collision_info['total_collisions']} "
                           f"(Init: {collision_info['initiated']}, "
                           f"Recv: {collision_info['received']})")
                col_surf = self.font_leaderboard_item.render(col_text, True, BLACK)
                self.screen.blit(col_surf, [x_pos + 10, y_pos])
                y_pos += 18
    
    def draw_lap_times_panel(self, race_manager, selected_car_id=None):
        """
        Draw detailed lap times panel
        
        Args:
            race_manager: RaceManager object
            selected_car_id: Car ID to show details for (None for all)
        """
        # This could show detailed lap time breakdown
        # Implementation depends on available screen space
        pass
    
    def render_race_frame(self, anchors, tags, race_manager, speed_manager, 
                         collision_detector, show_debug=False):
        """
        Render complete racing frame
        
        Args:
            anchors: List of anchor objects
            tags: List of tag objects
            race_manager: RaceManager object
            speed_manager: SpeedManager object
            collision_detector: CollisionDetector object
            show_debug: Show debug visualizations
        """
        # First, draw base elements (grid, anchors, etc.)
        self.base_renderer.draw_grid()
        self.base_renderer.draw_tracking_area(anchors)
        
        # Draw start/finish line
        self.draw_start_line()
        
        # Draw anchors
        for anchor in anchors:
            self.base_renderer.draw_anchor(anchor)
        
        # Collect car positions for collision debug
        car_positions = []
        
        # Draw tags with racing info
        for tag in tags:
            if tag.status and tag.is_active(TAG_TIMEOUT):
                # Draw base tag
                self.base_renderer.draw_tag(tag)
                
                # Get racing data
                lap_info = race_manager.get_car_lap_info(tag.id)
                speed_info = speed_manager.get_car_speed_info(tag.id)
                collision_info = collision_detector.get_car_collision_info(tag.id)
                
                # Draw racing overlays
                if lap_info:
                    self.draw_car_lap_info(tag, lap_info)
                
                if speed_info:
                    self.draw_car_speed(tag, speed_info)
                
                if collision_info:
                    self.draw_collision_indicator(tag, collision_info)
                
                car_positions.append((tag, tag.x, tag.y))
        
        # Debug visualizations
        if show_debug:
            self.draw_collision_debug(car_positions)
        
        # Draw race status
        self.draw_race_status(race_manager)
        
        # Draw leaderboard
        self.draw_leaderboard(race_manager, collision_detector)
        
        # Update display
        pygame.display.flip()
