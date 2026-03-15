# glaze-app/tests/test_monitor_layout.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monitor_layout import MonitorLayout, ZoneLayout, get_zones_for_monitor

def make_monitor(left, top, right, bottom):
    return {"left": left, "top": top, "right": right, "bottom": bottom,
            "name": "TEST", "id": 0}

def test_quadrant_2x2_top_left():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    # Ponto no quadrante superior esquerdo
    zone = _find_zone(zones, 100, 100)
    assert zone["quadrant"] == 0

def test_quadrant_2x2_bottom_right():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    zone = _find_zone(zones, 1800, 900)
    assert zone["quadrant"] == 3

def test_quadrant_4x1_leftmost():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "4x1")
    zone = _find_zone(zones, 100, 500)
    assert zone["quadrant"] == 0

def test_quadrant_4x1_rightmost():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "4x1")
    zone = _find_zone(zones, 1800, 500)
    assert zone["quadrant"] == 3

def test_point_outside_monitor_returns_none():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    zone = _find_zone(zones, 2000, 500)
    assert zone is None

def test_second_monitor_offset():
    m = make_monitor(1920, 0, 3840, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    # Ponto no monitor 2, superior esquerdo (coordenada absoluta)
    zone = _find_zone(zones, 2000, 100)
    assert zone["quadrant"] == 0

def _find_zone(zones, x, y):
    for z in zones:
        if z["left"] <= x < z["right"] and z["top"] <= y < z["bottom"]:
            return z
    return None
