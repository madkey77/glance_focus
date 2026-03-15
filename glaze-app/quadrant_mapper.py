# glaze-app/quadrant_mapper.py
import time
from config import MIN_WINDOW_SIZE, QUADRANT_UPDATE_MS


def compute_intersection_area(rect_a, rect_b):
    """Área de interseção entre dois retângulos (dicts com left/top/right/bottom)."""
    ix1 = max(rect_a["left"],  rect_b["left"])
    iy1 = max(rect_a["top"],   rect_b["top"])
    ix2 = min(rect_a["right"], rect_b["right"])
    iy2 = min(rect_a["bottom"],rect_b["bottom"])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return (ix2 - ix1) * (iy2 - iy1)


def find_dominant_window(zone, windows):
    """
    Retorna a janela com maior área de interseção com a zona.
    Em caso de empate, prioriza z_order menor (mais ao topo).
    Retorna None se lista vazia.
    """
    if not windows:
        return None
    best = max(
        windows,
        key=lambda w: (compute_intersection_area(zone, w), -w["z_order"])
    )
    area = compute_intersection_area(zone, best)
    return best if area > 0 else None


def _is_valid_window(hwnd):
    import win32gui
    if not win32gui.IsWindowVisible(hwnd):
        return False
    if win32gui.IsIconic(hwnd):  # minimizada
        return False
    title = win32gui.GetWindowText(hwnd)
    if not title:
        return False
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w < MIN_WINDOW_SIZE or h < MIN_WINDOW_SIZE:
            return False
    except Exception:
        return False
    return True


class QuadrantMapper:
    def __init__(self, zones):
        self.zones = zones
        self._windows_cache = []
        self._last_update = 0
        self._dominant_cache = {}  # zone key → hwnd dict

    def _update_windows(self):
        """Atualiza lista de janelas visíveis com z_order."""
        import win32gui
        windows = []
        z_order = [0]

        def _enum(hwnd, _):
            if _is_valid_window(hwnd):
                rect = win32gui.GetWindowRect(hwnd)
                windows.append({
                    "hwnd": hwnd,
                    "title": win32gui.GetWindowText(hwnd),
                    "left": rect[0], "top": rect[1],
                    "right": rect[2], "bottom": rect[3],
                    "z_order": z_order[0],
                })
            z_order[0] += 1

        win32gui.EnumWindows(_enum, None)
        self._windows_cache = windows

    def get_dominant(self, zone):
        """
        Retorna janela dominante para a zona dada.
        Atualiza cache a cada QUADRANT_UPDATE_MS ms.
        """
        now = time.time() * 1000
        if now - self._last_update > QUADRANT_UPDATE_MS:
            self._update_windows()
            self._last_update = now
            self._dominant_cache = {}

        key = (zone["monitor_id"], zone["quadrant"])
        if key not in self._dominant_cache:
            self._dominant_cache[key] = find_dominant_window(zone, self._windows_cache)
        return self._dominant_cache[key]
