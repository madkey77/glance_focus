# glaze-app/tests/test_quadrant_mapper.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from quadrant_mapper import compute_intersection_area, find_dominant_window

def make_rect(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}

def test_intersection_full_overlap():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(0, 0, 960, 540)
    assert compute_intersection_area(zone, win) == 960 * 540

def test_intersection_partial():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(480, 0, 1440, 540)
    assert compute_intersection_area(zone, win) == 480 * 540

def test_intersection_no_overlap():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(1000, 0, 1920, 540)
    assert compute_intersection_area(zone, win) == 0

def test_find_dominant_picks_largest_overlap():
    zone = make_rect(0, 0, 960, 540)
    windows = [
        {"hwnd": 1, "title": "A", "left": 0,   "top": 0, "right": 500, "bottom": 540, "z_order": 0},
        {"hwnd": 2, "title": "B", "left": 0,   "top": 0, "right": 900, "bottom": 540, "z_order": 1},
    ]
    dominant = find_dominant_window(zone, windows)
    assert dominant["hwnd"] == 2

def test_find_dominant_tiebreak_z_order():
    zone = make_rect(0, 0, 960, 540)
    windows = [
        {"hwnd": 1, "title": "A", "left": 0, "top": 0, "right": 960, "bottom": 540, "z_order": 2},
        {"hwnd": 2, "title": "B", "left": 0, "top": 0, "right": 960, "bottom": 540, "z_order": 0},
    ]
    dominant = find_dominant_window(zone, windows)
    # z_order menor = mais ao topo
    assert dominant["hwnd"] == 2

def test_find_dominant_returns_none_no_windows():
    zone = make_rect(0, 0, 960, 540)
    assert find_dominant_window(zone, []) is None
