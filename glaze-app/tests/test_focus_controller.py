# glaze-app/tests/test_focus_controller.py
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from focus_controller import SaccadeDetector

def make_zone(monitor_id, quadrant):
    return {"monitor_id": monitor_id, "quadrant": quadrant,
            "left": 0, "top": 0, "right": 960, "bottom": 540}

def test_no_trigger_on_first_zone():
    sd = SaccadeDetector(stable_ms=150)
    triggered = sd.update(make_zone(0, 0), time.time())
    assert triggered is False

def test_no_trigger_before_stable_time():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    # Muda de zona mas não esperou 150ms
    triggered = sd.update(make_zone(0, 1), t0 + 0.05)
    assert triggered is False

def test_trigger_after_stable_time():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    # Muda de zona e espera 150ms
    sd.update(make_zone(0, 1), t0 + 0.01)
    triggered = sd.update(make_zone(0, 1), t0 + 0.20)
    assert triggered is True

def test_no_trigger_if_zone_changes_before_stable():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    sd.update(make_zone(0, 1), t0 + 0.05)  # muda
    triggered = sd.update(make_zone(0, 2), t0 + 0.10)  # muda de novo antes dos 150ms
    assert triggered is False

def test_no_double_trigger_same_zone():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    sd.update(make_zone(0, 1), t0 + 0.01)
    sd.update(make_zone(0, 1), t0 + 0.20)   # primeiro trigger
    triggered2 = sd.update(make_zone(0, 1), t0 + 0.30)  # mesmo quadrante — não deve re-triggar
    assert triggered2 is False
