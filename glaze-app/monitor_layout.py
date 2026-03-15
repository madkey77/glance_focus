# glaze-app/monitor_layout.py


def get_monitors():
    """
    Retorna lista de monitores com nome, posição e dimensões no desktop virtual.
    Cada monitor: {"id", "name", "left", "top", "right", "bottom"}
    """
    import win32api
    monitors = []
    raw = win32api.EnumDisplayMonitors(None, None)
    for i, (hmon, _, rect) in enumerate(raw):
        info = win32api.GetMonitorInfo(hmon)
        left, top, right, bottom = info["Monitor"]
        # Nome legível via EnumDisplayDevices
        try:
            device = win32api.EnumDisplayDevices(info["Device"], 0)
            name = device.DeviceString
        except Exception:
            name = info["Device"]
        monitors.append({
            "id": i,
            "name": name,
            "device": info["Device"],
            "left": left, "top": top,
            "right": right, "bottom": bottom,
        })
    return monitors


def get_zones_for_monitor(monitor, layout="2x2"):
    """
    Divide um monitor em zonas conforme o layout.
    Retorna lista de dicts: {"monitor_id", "quadrant", "left", "top", "right", "bottom"}

    Layouts suportados:
      "2x2" — 4 quadrantes (2 colunas x 2 linhas)
      "4x1" — 4 faixas verticais
      "1x4" — 4 faixas horizontais
    """
    l, t, r, b = monitor["left"], monitor["top"], monitor["right"], monitor["bottom"]
    w = r - l
    h = b - t
    mid = monitor.get("id", 0)
    zones = []

    if layout == "2x2":
        halfw, halfh = w // 2, h // 2
        grid = [
            (0, l,        t,        l+halfw, t+halfh),
            (1, l+halfw,  t,        r,       t+halfh),
            (2, l,        t+halfh,  l+halfw, b),
            (3, l+halfw,  t+halfh,  r,       b),
        ]
    elif layout == "4x1":
        qw = w // 4
        grid = [(i, l+i*qw, t, l+(i+1)*qw if i < 3 else r, b) for i in range(4)]
    elif layout == "1x4":
        qh = h // 4
        grid = [(i, l, t+i*qh, r, t+(i+1)*qh if i < 3 else b) for i in range(4)]
    else:
        raise ValueError(f"Layout desconhecido: {layout}")

    for quadrant, zl, zt, zr, zb in grid:
        zones.append({
            "monitor_id": mid,
            "quadrant": quadrant,
            "left": zl, "top": zt,
            "right": zr, "bottom": zb,
        })
    return zones


# Alias for potential import
ZoneLayout = str


class MonitorLayout:
    def __init__(self, layout="2x2"):
        self.layout = layout
        self.monitors = get_monitors()
        self.zones = []
        for m in self.monitors:
            self.zones.extend(get_zones_for_monitor(m, layout))

    def get_zone(self, x_abs, y_abs):
        """Retorna zona (dict) onde o ponto (x_abs, y_abs) cai, ou None."""
        for z in self.zones:
            if z["left"] <= x_abs < z["right"] and z["top"] <= y_abs < z["bottom"]:
                return z
        return None

    def get_monitor_names(self):
        return [(m["id"], m["name"], m["device"]) for m in self.monitors]
