"""
UWB Position Tracker - Visualization Module
Handles all drawing and rendering operations
"""

import pygame
import time
import math
from config import *


class Renderer:
    """Handles all visualization and drawing"""

    def __init__(self, screen, scale_params):
        """
        Initialize renderer
        
        Args:
            screen: Pygame screen surface
            scale_params: Dictionary with scaling parameters
        """
        self.screen = screen
        self.cm2p = scale_params['cm2p']
        self.x_offset = scale_params['x_offset']
        self.y_offset = scale_params['y_offset']
        self.center_x_pixel = scale_params['center_x_pixel']
        self.center_y_pixel = scale_params['center_y_pixel']
        
        # Initialize fonts
        self.font_title = pygame.font.SysFont(*FONT_TITLE)
        self.font_label = pygame.font.SysFont(*FONT_LABEL)
        self.font_info = pygame.font.SysFont(*FONT_INFO)
        self.font_small = pygame.font.SysFont(*FONT_SMALL)
        self.font_quality = pygame.font.SysFont(*FONT_QUALITY)

    def cm_to_pixels(self, x_cm, y_cm):
        """
        Convert cm coordinates to pixel coordinates
        
        Args:
            x_cm: X position in cm
            y_cm: Y position in cm
            
        Returns:
            tuple: (pixel_x, pixel_y)
        """
        pixel_x = int(x_cm * self.cm2p + self.x_offset)
        pixel_y = SCREEN_Y - int(y_cm * self.cm2p + self.y_offset)
        return pixel_x, pixel_y

    def draw_grid(self):
        """Draw background grid"""
        grid_spacing = int(GRID_SPACING_CM * self.cm2p)
        
        # Draw grid lines
        for x in range(0, SCREEN_X, grid_spacing):
            pygame.draw.line(self.screen, LIGHT_GRAY, (x, 0), (x, SCREEN_Y), 1)
        for y in range(0, SCREEN_Y, grid_spacing):
            pygame.draw.line(self.screen, LIGHT_GRAY, (0, y), (SCREEN_X, y), 1)

        # Draw center lines
        pygame.draw.line(self.screen, GRAY, 
                        (self.center_x_pixel, 0), 
                        (self.center_x_pixel, SCREEN_Y), 2)
        pygame.draw.line(self.screen, GRAY, 
                        (0, self.center_y_pixel), 
                        (SCREEN_X, self.center_y_pixel), 2)

    def draw_tracking_area(self, anchors):
        """
        Draw the tracking area boundary
        
        Args:
            anchors: List of anchor objects
        """
        if len(anchors) < 3:
            return
            
        corner_points = []
        for anchor in anchors:
            px, py = self.cm_to_pixels(anchor.x, anchor.y)
            corner_points.append((px, py))
        
        if len(corner_points) >= 3:
            pygame.draw.polygon(self.screen, BLUE, corner_points, 3)

    def draw_anchor(self, anchor):
        """
        Draw an anchor device
        
        Args:
            anchor: Anchor object
        """
        pixel_x, pixel_y = self.cm_to_pixels(anchor.x, anchor.y)
        
        # Draw label
        temp_str = f"{anchor.name} ({anchor.x},{anchor.y})"
        surf = self.font_info.render(temp_str, True, BLACK)
        self.screen.blit(surf, [pixel_x + 12, pixel_y - 8])

        # Draw anchor circle
        pygame.draw.circle(self.screen, BLACK, [pixel_x, pixel_y], ANCHOR_RADIUS, 0)
        pygame.draw.circle(self.screen, WHITE, [pixel_x, pixel_y], ANCHOR_RADIUS + 2, 2)

    def draw_tag_trail(self, tag):
        """
        Draw position history trail for a tag
        
        Args:
            tag: Tag object
        """
        if len(tag.history) < 2:
            return
            
        points = []
        for hx, hy, ht in tag.history:
            px, py = self.cm_to_pixels(hx, hy)
            points.append((px, py))

        # Draw trail with gradient
        if len(points) > 1:
            for i in range(len(points) - 1):
                alpha = int(255 * (i + 1) / len(points))
                pygame.draw.line(self.screen, YELLOW, points[i], points[i + 1], 3)

    def get_quality_color(self, quality):
        """
        Get color based on quality level
        
        Args:
            quality: Quality string
            
        Returns:
            list: RGB color
        """
        quality_colors = {
            "excellent": GREEN,
            "good": BLUE,
            "fair": ORANGE,
            "poor": RED,
            "unknown": GRAY
        }
        return quality_colors.get(quality, GRAY)

    def draw_tag(self, tag):
        """
        Draw a tag device
        
        Args:
            tag: Tag object
        """
        pixel_x, pixel_y = self.cm_to_pixels(tag.x, tag.y)
        
        # Get quality color
        color = self.get_quality_color(tag.quality)
        
        # Draw trail
        self.draw_tag_trail(tag)
        
        # Draw position label
        temp_str = f"{tag.name} ({int(tag.x)},{int(tag.y)})"
        surf = self.font_label.render(temp_str, True, color)
        self.screen.blit(surf, [pixel_x + 18, pixel_y - 12])

        # Draw quality indicator
        qual_text = f"{tag.quality} [{tag.anchor_count}A]"
        qual_surf = self.font_quality.render(qual_text, True, GRAY)
        self.screen.blit(qual_surf, [pixel_x + 18, pixel_y + 5])

        # Draw tag circle
        pygame.draw.circle(self.screen, color, [pixel_x, pixel_y], TAG_RADIUS, 0)
        pygame.draw.circle(self.screen, WHITE, [pixel_x, pixel_y], TAG_RADIUS + 2, 2)

        # Active pulse indicator
        if (time.time() - tag.last_update) < UPDATE_RECENT_THRESHOLD:
            pulse_r = int(TAG_RADIUS + PULSE_MULTIPLIER * math.sin(time.time() * 10))
            pygame.draw.circle(self.screen, color, [pixel_x, pixel_y], pulse_r, 2)

    def draw_range_circles(self, tags, anchors):
        """
        Draw range circles from anchors to tags (debug feature)
        
        Args:
            tags: List of tag objects
            anchors: List of anchor objects
        """
        for tag in tags:
            if not tag.is_active(1):
                continue
                
            for i, dist in enumerate(tag.range_list):
                if dist > 0 and i < len(anchors):
                    anc_x, anc_y = self.cm_to_pixels(anchors[i].x, anchors[i].y)
                    radius = int(dist * self.cm2p)
                    pygame.draw.circle(self.screen, RANGE_CIRCLE_COLOR, 
                                     [anc_x, anc_y], radius, 1)

    def draw_info_panel(self, tags, udp_receiver):
        """
        Draw information panel with statistics
        
        Args:
            tags: List of tag objects
            udp_receiver: UDPReceiver object
        """
        y_pos = 10

        # Title
        title_surf = self.font_title.render(
            "UWB Position Tracker - Enhanced (3 Tags, 4 Anchors)", True, BLUE)
        self.screen.blit(title_surf, [10, y_pos])
        y_pos += 40

        # Connection status
        if udp_receiver.is_connected(CONNECTION_TIMEOUT):
            status_color = GREEN
            status_text = "● CONNECTED"
        else:
            status_color = RED
            status_text = "● WAITING FOR DATA"

        status_surf = self.font_info.render(status_text, True, status_color)
        self.screen.blit(status_surf, [10, y_pos])
        y_pos += 25

        # Statistics
        stats = udp_receiver.get_statistics()
        stat_lines = [
            f"UDP Port: {udp_receiver.port}",
            f"Total Packets: {stats['packets_received']}",
            f"Update Rate: {stats['packets_per_second']} Hz",
            f"Active Tags: {sum(1 for t in tags if t.is_active(1))}",
        ]

        for line in stat_lines:
            surf = self.font_small.render(line, True, BLACK)
            self.screen.blit(surf, [10, y_pos])
            y_pos += 20

        # Tag details
        y_pos += 10
        header = self.font_info.render("Tag Details:", True, BLACK)
        self.screen.blit(header, [10, y_pos])
        y_pos += 25

        for tag in tags:
            if tag.is_active(3):
                q_color = self.get_quality_color(tag.quality)
                detail = f"{tag.name}: {tag.quality.upper()} ({tag.anchor_count} anchors)"
                surf = self.font_small.render(detail, True, q_color)
                self.screen.blit(surf, [20, y_pos])
                y_pos += 18

    def render_frame(self, anchors, tags, udp_receiver, show_debug=False):
        """
        Render complete frame
        
        Args:
            anchors: List of anchor objects
            tags: List of tag objects
            udp_receiver: UDPReceiver object
            show_debug: Show debug features like range circles
        """
        # Clear screen
        self.screen.fill(WHITE)

        # Draw background elements
        self.draw_grid()
        self.draw_tracking_area(anchors)

        # Optional debug features
        if show_debug:
            self.draw_range_circles(tags, anchors)

        # Draw devices
        for anchor in anchors:
            self.draw_anchor(anchor)

        for tag in tags:
            if tag.status:
                # Check for timeout
                if not tag.is_active(TAG_TIMEOUT):
                    tag.status = False
                else:
                    self.draw_tag(tag)

        # Draw info panel
        self.draw_info_panel(tags, udp_receiver)

        # Update display
        pygame.display.flip()
