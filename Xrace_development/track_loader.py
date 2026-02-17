"""
Racing Track Loader - Enhanced for Wide Tracks
Supports tracks with visible width (inner and outer boundaries)
"""

import csv
import math
from typing import List, Tuple


class Track:
    """Represents a racing track with width"""
    
    def __init__(self, name: str, outer_points: List[Tuple[float, float]], 
                 inner_points: List[Tuple[float, float]] = None):
        """
        Initialize track
        
        Args:
            name: Track name
            outer_points: Outer boundary points (x, y) in cm
            inner_points: Inner boundary points (optional)
        """
        self.name = name
        self.outer_points = outer_points
        self.inner_points = inner_points if inner_points else []
        self.closed = True
        
    def get_points(self):
        """Get outer boundary points for compatibility"""
        return self.outer_points
    
    def get_outer_points(self):
        """Get outer boundary points"""
        return self.outer_points
    
    def get_inner_points(self):
        """Get inner boundary points"""
        return self.inner_points
    
    def has_width(self):
        """Check if track has visible width"""
        return len(self.inner_points) > 0
    
    def get_track_length(self):
        """Calculate track length (using outer boundary)"""
        length = 0
        points = self.outer_points
        for i in range(len(points)):
            p1 = points[i]
            p2 = points[(i + 1) % len(points)]
            length += math.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)
        return length


class TrackLoader:
    """Loads tracks from CSV files"""
    
    @staticmethod
    def load_from_csv(filename: str, track_name: str = None):
        """Load track outer boundary from CSV"""
        points = []
        
        try:
            with open(filename, 'r') as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith('#'):
                        continue
                    
                    try:
                        x = float(row[0].strip())
                        y = float(row[1].strip())
                        points.append((x, y))
                    except (ValueError, IndexError):
                        continue
            
            if not track_name:
                track_name = filename.split('/')[-1].split('\\')[-1].split('.')[0]
            
            if len(points) > 0:
                print(f"✓ Loaded track '{track_name}' with {len(points)} points")
                return Track(track_name, points)
            else:
                print(f"✗ No valid points found in {filename}")
                return None
                
        except FileNotFoundError:
            print(f"✗ Track file not found: {filename}")
            return None
        except Exception as e:
            print(f"✗ Error loading track: {e}")
            return None
    
    @staticmethod
    def load_wide_track(outer_file: str, inner_file: str, track_name: str = "Wide Track"):
        """
        Load a track with both inner and outer boundaries
        
        Args:
            outer_file: CSV file for outer boundary
            inner_file: CSV file for inner boundary
            track_name: Track name
            
        Returns:
            Track object with width
        """
        # Load outer boundary
        outer_points = []
        try:
            with open(outer_file, 'r') as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith('#'):
                        continue
                    try:
                        x = float(row[0].strip())
                        y = float(row[1].strip())
                        outer_points.append((x, y))
                    except (ValueError, IndexError):
                        continue
        except:
            print(f"✗ Could not load outer boundary: {outer_file}")
            return None
        
        # Load inner boundary
        inner_points = []
        try:
            with open(inner_file, 'r') as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith('#'):
                        continue
                    try:
                        x = float(row[0].strip())
                        y = float(row[1].strip())
                        inner_points.append((x, y))
                    except (ValueError, IndexError):
                        continue
        except:
            print(f"✗ Could not load inner boundary: {inner_file}")
            return None
        
        if len(outer_points) > 0 and len(inner_points) > 0:
            print(f"✓ Loaded wide track '{track_name}'")
            print(f"  Outer: {len(outer_points)} points, Inner: {len(inner_points)} points")
            return Track(track_name, outer_points, inner_points)
        else:
            print(f"✗ Invalid track boundaries")
            return None
    
    @staticmethod
    def create_oval_track(center_x: float, center_y: float,
                         width: float, height: float, 
                         track_width: float = 30, num_points: int = 40):
        """
        Create an oval track with visible width
        
        Args:
            center_x: Center X
            center_y: Center Y
            width: Oval width (x-radius)
            height: Oval height (y-radius)
            track_width: Width of racing surface
            num_points: Number of points
            
        Returns:
            Track with inner and outer boundaries
        """
        outer_points = []
        inner_points = []
        
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            
            # Outer boundary
            x_outer = center_x + width * math.cos(angle)
            y_outer = center_y + height * math.sin(angle)
            outer_points.append((x_outer, y_outer))
            
            # Inner boundary (offset toward center)
            scale = (width - track_width) / width
            x_inner = center_x + (width - track_width) * math.cos(angle)
            y_inner = center_y + (height - track_width) * math.sin(angle)
            inner_points.append((x_inner, y_inner))
        
        print(f"✓ Created oval track (width={track_width}cm)")
        return Track("Oval Track", outer_points, inner_points)


def get_track(track_type: str = 'oval', csv_file: str = None, 
              outer_file: str = None, inner_file: str = None):
    """
    Get a track by type or from CSV
    
    Args:
        track_type: 'oval', 'csv', or 'wide'
        csv_file: Single CSV file (outer boundary only)
        outer_file: Outer boundary CSV (for wide tracks)
        inner_file: Inner boundary CSV (for wide tracks)
        
    Returns:
        Track object or None
    """
    if track_type == 'wide' and outer_file and inner_file:
        return TrackLoader.load_wide_track(outer_file, inner_file)
    elif track_type == 'csv' and csv_file:
        return TrackLoader.load_from_csv(csv_file)
    elif track_type == 'oval':
        # Create default wide oval track
        return TrackLoader.create_oval_track(100, 110, 85, 70, track_width=30, num_points=40)
    else:
        print(f"✗ Unknown track type: {track_type}")
        return None
