"""
UWB Position Tracker - Positioning Algorithms
Contains all trilateration and multilateration algorithms
"""

import math
from config import (
    RSSI_EXCELLENT, RSSI_POOR, RSSI_MIN_WEIGHT, 
    RSSI_NORMALIZATION, PRINT_CALCULATION_DETAILS
)


class PositioningAlgorithms:
    """Collection of positioning calculation methods"""

    @staticmethod
    def calculate_rssi_weight(rssi):
        """
        Calculate weight based on RSSI value
        
        Args:
            rssi: RSSI value in dBm (negative number)
            
        Returns:
            float: Weight value between RSSI_MIN_WEIGHT and 1.0
        """
        if rssi >= 0:
            return 1.0
        
        # RSSI is negative, closer to 0 is better
        # -60 dBm is excellent, -90 dBm is poor
        normalized_rssi = (rssi + (RSSI_EXCELLENT + RSSI_POOR) / 2) / RSSI_NORMALIZATION
        weight = max(RSSI_MIN_WEIGHT, 1.0 + normalized_rssi)
        return weight

    @staticmethod
    def get_valid_anchors(range_list, rssi_list, anchors):
        """
        Extract valid anchor measurements with weights
        
        Args:
            range_list: List of ranges to each anchor
            rssi_list: List of RSSI values
            anchors: List of anchor objects
            
        Returns:
            list: List of dicts with anchor info and weights
        """
        valid_anchors = []
        
        for i in range(min(len(range_list), len(anchors))):
            if range_list[i] > 0:
                # Calculate weight by RSSI
                rssi = rssi_list[i] if i < len(rssi_list) else 0
                weight = PositioningAlgorithms.calculate_rssi_weight(rssi)
                
                valid_anchors.append({
                    'id': i,
                    'range': range_list[i],
                    'rssi': rssi,
                    'weight': weight,
                    'anchor': anchors[i]
                })
        
        return valid_anchors

    @staticmethod
    def weighted_multilateration(valid_anchors):
        """
        Advanced: Use all 4+ anchors with least squares optimization
        
        Args:
            valid_anchors: List of valid anchor measurements
            
        Returns:
            tuple: (x, y) calculated position
        """
        if len(valid_anchors) < 3:
            return 0, 0
        
        combinations = []
        
        # Generate all possible 3-anchor combinations
        for i in range(len(valid_anchors)):
            for j in range(i + 1, len(valid_anchors)):
                for k in range(j + 1, len(valid_anchors)):
                    a1, a2, a3 = valid_anchors[i], valid_anchors[j], valid_anchors[k]
                    
                    # Get anchor positions
                    x1, y1 = a1['anchor'].x, a1['anchor'].y
                    x2, y2 = a2['anchor'].x, a2['anchor'].y
                    x3, y3 = a3['anchor'].x, a3['anchor'].y
                    
                    # Calculate position using this combination
                    px, py = PositioningAlgorithms.trilaterate_3points(
                        x1, y1, a1['range'],
                        x2, y2, a2['range'],
                        x3, y3, a3['range']
                    )
                    
                    # Weight by combined RSSI quality
                    weight = (a1['weight'] + a2['weight'] + a3['weight']) / 3
                    combinations.append((px, py, weight))
        
        # Weighted average of all combinations
        if combinations:
            total_w = sum(c[2] for c in combinations)
            if total_w > 0:
                x = sum(c[0] * c[2] for c in combinations) / total_w
                y = sum(c[1] * c[2] for c in combinations) / total_w
                
                if PRINT_CALCULATION_DETAILS:
                    print(f"Multilateration: {len(combinations)} combinations, result: ({x:.1f}, {y:.1f})")
                
                return x, y
        
        return 0, 0

    @staticmethod
    def trilaterate_3points(x1, y1, r1, x2, y2, r2, x3, y3, r3):
        """
        Analytical trilateration using 3 circles
        
        Args:
            x1, y1: First anchor position
            r1: Range to first anchor
            x2, y2: Second anchor position
            r2: Range to second anchor
            x3, y3: Third anchor position
            r3: Range to third anchor
            
        Returns:
            tuple: (x, y) calculated position
        """
        # Using the analytical solution for 3-circle intersection
        A = 2 * (x2 - x1)
        B = 2 * (y2 - y1)
        C = r1 ** 2 - r2 ** 2 - x1 ** 2 + x2 ** 2 - y1 ** 2 + y2 ** 2

        D = 2 * (x3 - x2)
        E = 2 * (y3 - y2)
        F = r2 ** 2 - r3 ** 2 - x2 ** 2 + x3 ** 2 - y2 ** 2 + y3 ** 2

        # Solve system of equations
        denom = A * E - B * D
        if abs(denom) < 0.001:
            # Circles are collinear, use 2-point method
            return PositioningAlgorithms.two_circles(x1, y1, x2, y2, r1, r2)

        x = (C * E - F * B) / denom
        y = (C * D - A * F) / denom * -1

        return x, y

    @staticmethod
    def two_circles(x1, y1, x2, y2, r1, r2):
        """
        Calculate intersection of two circles
        
        Args:
            x1, y1: First circle center
            x2, y2: Second circle center
            r1: First circle radius
            r2: Second circle radius
            
        Returns:
            tuple: (x, y) intersection point
        """
        d = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        
        if d == 0:
            return x1, y1

        if r1 + r2 <= d:
            # Circles don't intersect - return point on line between centers
            ratio = r1 / (r1 + r2)
            return x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio
        else:
            # Circles intersect - calculate intersection point
            a = (r1 ** 2 - r2 ** 2 + d ** 2) / (2 * d)
            h = math.sqrt(max(0, r1 ** 2 - a ** 2))
            px = x1 + a * (x2 - x1) / d
            py = y1 + a * (y2 - y1) / d
            return px, py

    @staticmethod
    def calculate_position_quality(anchor_count):
        """
        Determine position quality based on number of anchors
        
        Args:
            anchor_count: Number of valid anchors
            
        Returns:
            str: Quality descriptor
        """
        from config import (
            QUALITY_EXCELLENT_ANCHORS,
            QUALITY_GOOD_ANCHORS,
            QUALITY_FAIR_ANCHORS
        )
        
        if anchor_count >= QUALITY_EXCELLENT_ANCHORS:
            return "excellent"
        elif anchor_count >= QUALITY_GOOD_ANCHORS:
            return "good"
        elif anchor_count >= QUALITY_FAIR_ANCHORS:
            return "fair"
        else:
            return "poor"
