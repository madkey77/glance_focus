# glaze-app/focus_controller.py
import time
import threading
from config import SACCADE_STABLE_MS


class SaccadeDetector:
    """
    Detecta quando o olhar estabilizou em uma nova zona por stable_ms milissegundos.
    Retorna True no frame onde o trigger acontece (uma única vez por transição).
    """
    def __init__(self, stable_ms=None):
        self.stable_ms = stable_ms if stable_ms is not None else SACCADE_STABLE_MS
        self._candidate_zone = None
        self._candidate_since = None
        self._last_triggered_key = None

    def _zone_key(self, zone):
        return (zone["monitor_id"], zone["quadrant"])

    def update(self, zone, now=None):
        if now is None:
            now = time.time()
        key = self._zone_key(zone)

        if self._candidate_zone is None:
            # Estado inicial
            self._candidate_zone = zone
            self._candidate_since = now
            return False

        if key != self._zone_key(self._candidate_zone):
            # Nova zona — reinicia candidato
            self._candidate_zone = zone
            self._candidate_since = now
            return False

        # Mesma zona
        elapsed_ms = (now - self._candidate_since) * 1000
        if elapsed_ms >= self.stable_ms and key != self._last_triggered_key:
            self._last_triggered_key = key
            return True

        return False


def _apply_foreground_lock_timeout():
    """
    Define ForegroundLockTimeout=0 para a sessão atual via SystemParametersInfo.
    Permite que SetForegroundWindow funcione sem restrições de foreground lock.
    Chamado uma vez na inicialização.
    """
    import ctypes
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x0002
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, SPIF_SENDCHANGE
    )


def _force_foreground(hwnd):
    """
    Força foco para hwnd usando ALT-key trick (mais confiável no Windows 10/11).
    O Alt sintético concede ao processo a permissão de "último input event",
    desbloqueando SetForegroundWindow sem precisar de elevação.
    """
    import ctypes
    import win32gui

    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return

    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if user32.GetForegroundWindow() == hwnd:
        return  # já está em foco

    VK_MENU        = 0x12
    KEYEVENTF_KEYUP = 0x0002

    # Restaura se minimizada
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    # ALT trick: injetar press+release concede "last input event" token
    if not (user32.GetAsyncKeyState(VK_MENU) & 0x8000):
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)

    # Fallback: AttachThreadInput se ainda não focou
    if user32.GetForegroundWindow() != hwnd:
        fg_hwnd    = user32.GetForegroundWindow()
        fg_tid     = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        my_tid     = kernel32.GetCurrentThreadId()
        attached   = False
        if fg_tid and fg_tid != my_tid:
            attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(my_tid, fg_tid, False)


class OverlayBorder:
    """
    Janela transparente sempre-no-topo que desenha uma borda colorida
    ao redor da janela atualmente em foco pelo gaze.
    Cada hwnd recebe uma cor distinta da paleta (round-robin).
    Roda em thread própria. Todas as chamadas públicas são thread-safe.
    """
    COLORS = [
        "#00FF88", "#00CFFF", "#FFD700", "#FF6B35",
        "#FF00FF", "#4FC3F7", "#FF4444", "#FFFFFF",
    ]

    def __init__(self):
        self._lock        = threading.Lock()
        self._hwnd_target = None
        self._visible     = False
        self._color_map   = {}   # hwnd → color string
        self._color_idx   = 0
        self._root        = None
        threading.Thread(target=self._run, daemon=True).start()

    def _get_color(self, hwnd):
        """Retorna cor atribuída ao hwnd, criando se necessário.
        NÃO é thread-safe — deve ser chamado dentro de self._lock."""
        if hwnd not in self._color_map:
            self._color_map[hwnd] = self.COLORS[self._color_idx % len(self.COLORS)]
            self._color_idx += 1
        return self._color_map[hwnd]

    def _run(self):
        import tkinter as tk
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "black")
        self._root.configure(bg="black")
        self._canvas = tk.Canvas(self._root, bg="black", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._root.withdraw()
        self._root.after(50, self._update_loop)
        self._root.mainloop()

    def _update_loop(self):
        import win32gui
        with self._lock:
            target  = self._hwnd_target
            visible = self._visible
            color   = self._get_color(target) if target else None

        if visible and target and color:
            try:
                rect = win32gui.GetWindowRect(target)
                l, t, r, b = rect
                thickness = 4
                self._root.geometry(f"{r-l}x{b-t}+{l}+{t}")
                self._canvas.delete("all")
                self._canvas.create_rectangle(
                    thickness//2, thickness//2,
                    r-l-thickness//2, b-t-thickness//2,
                    outline=color, width=thickness
                )
                self._root.deiconify()
            except Exception:
                self._root.withdraw()
        else:
            self._root.withdraw()
        self._root.after(50, self._update_loop)

    def set_target(self, hwnd):
        with self._lock:
            self._hwnd_target = hwnd

    def show(self):
        with self._lock:
            self._visible = True

    def hide(self):
        with self._lock:
            self._visible = False

    def toggle(self):
        with self._lock:
            self._visible = not self._visible


class GazeDot:
    """
    Janela Tkinter transparente que exibe um ponto de gaze pulsante
    na posição absoluta estimada no desktop.
    Roda em thread própria. Todas as chamadas públicas são thread-safe.

    Mecanismo de estabilização: set_stable(True) registra _stable_until = now + 0.5.
    _update_loop verifica (now < _stable_until) para saber se está no período sólido.
    FocusController nunca chama set_stable(False) — o timer é interno.
    """
    SIZE     = 40    # tamanho da janela em px
    RING     = 24    # diâmetro do anel externo
    DOT      = 8     # diâmetro do ponto central
    PULSE_MS = 33    # ~30fps

    def __init__(self):
        self._lock         = threading.Lock()
        self._ax           = 0
        self._ay           = 0
        self._visible      = False
        self._stable_until = 0.0   # timestamp até quando exibe anel sólido
        self._last_update  = 0.0   # timestamp do último set_position
        self._root              = None
        self._pulse_phase_tk    = 0.0   # 0..1 para animação pulsante; only accessed from Tk thread — no lock needed
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import tkinter as tk
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "black")
        self._root.configure(bg="black")
        self._canvas = tk.Canvas(
            self._root, bg="black", highlightthickness=0,
            width=self.SIZE, height=self.SIZE
        )
        self._canvas.pack()
        self._root.withdraw()
        self._root.after(self.PULSE_MS, self._update_loop)
        self._root.mainloop()

    def _update_loop(self):
        now = time.time()
        with self._lock:
            visible      = self._visible
            ax           = self._ax
            ay           = self._ay
            last_upd     = self._last_update
            stable       = now < self._stable_until

        # Auto-hide on gaze loss — _visible stays True so dot reappears automatically when gaze returns
        if visible and (now - last_upd) > 0.2:
            self._root.withdraw()
            self._root.after(self.PULSE_MS, self._update_loop)
            return

        if not visible:
            self._root.withdraw()
            self._root.after(self.PULSE_MS, self._update_loop)
            return

        # Posiciona janela centralizada no ponto de gaze
        half = self.SIZE // 2
        self._root.geometry(f"{self.SIZE}x{self.SIZE}+{ax - half}+{ay - half}")
        self._canvas.delete("all")

        cx, cy = self.SIZE // 2, self.SIZE // 2

        if stable:
            # Anel sólido durante os 500ms pós-saccade
            r = self.RING // 2
            self._canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="white", width=2
            )
            self._canvas.create_oval(
                cx - self.DOT//2, cy - self.DOT//2,
                cx + self.DOT//2, cy + self.DOT//2,
                fill="white", outline="#333333", width=1
            )
        else:
            # Animação pulsante: anel cresce e fica mais fino
            self._pulse_phase_tk = (self._pulse_phase_tk + self.PULSE_MS / 1000.0) % 1.0
            t = self._pulse_phase_tk
            r = int(self.DOT // 2 + (self.RING // 2 - self.DOT // 2) * t)
            width = max(1, int(2 * (1.0 - t)))
            self._canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="white", width=width
            )
            self._canvas.create_oval(
                cx - self.DOT//2, cy - self.DOT//2,
                cx + self.DOT//2, cy + self.DOT//2,
                fill="white", outline="#333333", width=1
            )

        self._root.deiconify()
        self._root.after(self.PULSE_MS, self._update_loop)

    def set_position(self, ax: int, ay: int):
        with self._lock:
            self._ax          = ax
            self._ay          = ay
            self._last_update = time.time()

    def set_stable(self, stable: bool):
        """
        stable=True: inicia período sólido de 500ms (timer interno).
        FocusController nunca chama set_stable(False) — o timer expira sozinho.
        """
        with self._lock:
            if stable:
                self._stable_until = time.time() + 0.5

    def show(self):
        with self._lock:
            self._visible = True

    def hide(self):
        with self._lock:
            self._visible = False

    def toggle(self):
        with self._lock:
            self._visible = not self._visible


class DesktopMap:
    """
    HUD no canto inferior esquerdo do monitor primário.
    Mostra miniatura do desktop virtual com ponto de gaze e linha de status.
    Roda em thread própria. Todas as chamadas públicas são thread-safe.

    Coordenadas: o desktop virtual pode ter valores negativos (monitores à esquerda
    do primário). _compute_scale normaliza via min_x/min_y para encaixar tudo no canvas.
    """
    W, H      = 260, 140   # tamanho fixo da janela
    MAP_W     = 240         # largura da área do mapa dentro do canvas
    MAP_H     = 100         # altura da área do mapa dentro do canvas
    # Margem interna do canvas (px) para o mapa não colar na borda
    MAP_PAD_X = 10
    MAP_PAD_Y = 5
    UPDATE_MS = 100         # ~10fps
    BG        = "#1a1a1a"
    MARGIN    = 10          # margem da borda do monitor primário

    def __init__(self, monitors):
        """
        monitors: lista de dicts com keys left/top/right/bottom/id
        (MonitorLayout.monitors)
        """
        self._lock      = threading.Lock()
        self._monitors  = monitors
        self._ax        = None   # None = gaze perdido
        self._ay        = None
        self._info_text = ""
        self._visible   = False
        self._root      = None
        self._primary   = self._find_primary()
        self._scale, self._off_x, self._off_y = self._compute_scale()
        threading.Thread(target=self._run, daemon=True).start()

    def _find_primary(self):
        """Monitor primário = left==0 e top==0. Fallback: primeiro da lista."""
        for m in self._monitors:
            if m["left"] == 0 and m["top"] == 0:
                return m
        return self._monitors[0]

    def _compute_scale(self):
        """
        Calcula escala e offset para encaixar todos os monitores em MAP_W x MAP_H.
        Retorna (scale, off_x, off_y) onde off_x/off_y já incluem a normalização
        de coordenadas negativas E o centramento dentro de MAP_W x MAP_H.
        _to_map aplica adicionalmente MAP_PAD_X/MAP_PAD_Y para margem de canvas.
        """
        min_x   = min(m["left"]   for m in self._monitors)
        min_y   = min(m["top"]    for m in self._monitors)
        max_x   = max(m["right"]  for m in self._monitors)
        max_y   = max(m["bottom"] for m in self._monitors)
        total_w = max_x - min_x
        total_h = max_y - min_y
        sx = self.MAP_W / total_w if total_w > 0 else 1.0
        sy = self.MAP_H / total_h if total_h > 0 else 1.0
        scale = min(sx, sy)
        # Centraliza o mapa dentro de MAP_W x MAP_H
        off_x = (self.MAP_W - total_w * scale) / 2 - min_x * scale
        off_y = (self.MAP_H - total_h * scale) / 2 - min_y * scale
        return scale, off_x, off_y

    def _to_map(self, ax, ay):
        """
        Converte coordenada absoluta do desktop para coordenada no canvas do mapa.
        MAP_PAD_X/MAP_PAD_Y são adicionados aqui para margem interna do canvas.
        """
        return (
            int(ax * self._scale + self._off_x) + self.MAP_PAD_X,
            int(ay * self._scale + self._off_y) + self.MAP_PAD_Y,
        )

    def _monitor_to_map(self, m):
        x1, y1 = self._to_map(m["left"],  m["top"])
        x2, y2 = self._to_map(m["right"], m["bottom"])
        return x1, y1, x2, y2

    def _run(self):
        import tkinter as tk
        p     = self._primary
        win_x = p["left"]   + self.MARGIN
        win_y = p["bottom"] - self.H - self.MARGIN

        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.85)
        self._root.configure(bg=self.BG)
        self._root.geometry(f"{self.W}x{self.H}+{win_x}+{win_y}")

        self._canvas = tk.Canvas(
            self._root, bg=self.BG, highlightthickness=0,
            width=self.W, height=self.MAP_H + self.MAP_PAD_Y * 2
        )
        self._canvas.pack(side="top", fill="x")

        self._label = tk.Label(
            self._root, bg=self.BG, fg="#888888",
            font=("Consolas", 8), anchor="w", padx=6
        )
        self._label.pack(side="top", fill="x")

        self._root.withdraw()
        self._root.after(self.UPDATE_MS, self._update_loop)
        self._root.mainloop()

    def _update_loop(self):
        with self._lock:
            visible = self._visible
            ax      = self._ax
            ay      = self._ay
            info    = self._info_text

        if not visible:
            self._root.withdraw()
            self._root.after(self.UPDATE_MS, self._update_loop)
            return

        self._canvas.delete("all")

        # Desenha todos os monitores como retângulos
        for m in self._monitors:
            x1, y1, x2, y2 = self._monitor_to_map(m)
            self._canvas.create_rectangle(
                x1, y1, x2, y2,
                outline="#444444", fill="#222222", width=1
            )
            mid_x = (x1 + x2) // 2
            mid_y = (y1 + y2) // 2
            self._canvas.create_text(
                mid_x, mid_y,
                text=f"M{m['id']}", fill="#555555", font=("Consolas", 7)
            )

        # Desenha ponto de gaze (some quando ax/ay são None)
        if ax is not None and ay is not None:
            mx, my = self._to_map(ax, ay)
            r = 4
            self._canvas.create_oval(
                mx - r, my - r, mx + r, my + r,
                fill="white", outline="#333333", width=1
            )

        # Linha de status
        self._label.config(text=info if info else "sem sinal")
        self._root.deiconify()
        self._root.after(self.UPDATE_MS, self._update_loop)

    def set_gaze(self, ax, ay):
        """ax, ay: coordenadas absolutas no desktop, ou None se gaze perdido."""
        with self._lock:
            self._ax = ax
            self._ay = ay

    def set_info(self, zone, title, fps):
        """
        zone: dict com monitor_id e quadrant (ou None)
        title: dominant_window["title"]
        fps: float calculado em FocusController
        """
        if zone is None:
            text = f"sem zona | {title or ''} | {fps:.0f}fps"
        else:
            text = f"M{zone['monitor_id']}-Q{zone['quadrant']} | {title or ''} | {fps:.0f}fps"
        with self._lock:
            self._info_text = text

    def show(self):
        with self._lock:
            self._visible = True

    def hide(self):
        with self._lock:
            self._visible = False

    def toggle(self):
        with self._lock:
            self._visible = not self._visible


class MouseHider:
    """
    Esconde o cursor do mouse após MOUSE_HIDE_DELAY_S segundos sem movimento.
    Ao detectar movimento, teleporta o cursor para a posição atual do gaze
    antes de torná-lo visível novamente.

    Thread-safe: _lock protege todo o estado mutável.
    pynput.mouse.Listener roda em sua própria thread daemon.
    threading.Timer dispara _hide() em thread daemon separada.
    """

    def __init__(self, _listener_factory=None, _timer_factory=None):
        from config import MOUSE_HIDE_DELAY_S
        self._delay         = MOUSE_HIDE_DELAY_S
        self._lock          = threading.Lock()
        self._gaze_pos      = None   # None até set_gaze_pos ser chamado
        self._hidden        = False
        self._enabled       = False
        self._timer         = None
        self._listener      = None
        self._timer_factory = _timer_factory if _timer_factory is not None else threading.Timer

        factory = _listener_factory
        if factory is None:
            from pynput import mouse as _mouse
            factory = _mouse.Listener

        try:
            self._listener = factory(on_move=self._on_move)
            self._listener.start()
        except Exception as e:
            print(f"[MouseHider] Falha ao iniciar listener: {e} — desativado.")
            self._enabled = False
            self._listener = None
            return

        # Inicia habilitado
        self._enabled = True
        self._reset_timer()

    # ── state mutators (all acquire _lock) ───────────────────────────────────

    def _hide(self):
        """Esconde cursor. Só chama ShowCursor(False) uma vez (_hidden guard)."""
        import ctypes
        with self._lock:
            if self._hidden:
                return
            self._hidden = True
        ctypes.windll.user32.ShowCursor(False)

    def _show(self):
        """Mostra cursor. Só chama ShowCursor(True) uma vez (_hidden guard)."""
        import ctypes
        with self._lock:
            if not self._hidden:
                return
            self._hidden = False
        ctypes.windll.user32.ShowCursor(True)

    def _reset_timer(self):
        """Cancela timer existente e agenda novo _hide após _delay segundos."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if not self._enabled:
                return
            t = self._timer_factory(self._delay, self._hide)
            t.daemon = True
            t.start()
            self._timer = t

    def _cancel_timer(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ── pynput callback ───────────────────────────────────────────────────────

    def _on_move(self, x, y):
        """Chamado pelo pynput em qualquer movimento do mouse."""
        import ctypes
        with self._lock:
            enabled = self._enabled
            gaze    = self._gaze_pos
        if not enabled:
            return
        # Teleporta para o gaze ANTES de mostrar (evita flicker na posição antiga)
        if gaze is not None:
            ctypes.windll.user32.SetCursorPos(gaze[0], gaze[1])
        self._show()
        self._reset_timer()

    # ── public interface ──────────────────────────────────────────────────────

    def set_gaze_pos(self, ax: int, ay: int) -> None:
        """Atualiza posição do gaze. Chamado a cada frame quando gaze é válido."""
        with self._lock:
            self._gaze_pos = (ax, ay)

    def set_enabled(self, enabled: bool) -> None:
        """Habilita/desabilita. Idempotente. Desabilitar restaura cursor e cancela timer."""
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
        if not enabled:
            self._cancel_timer()
            self._show()
        else:
            self._reset_timer()

    def stop(self) -> None:
        """Restaura cursor e para listener. Chamado no encerramento do app."""
        self._cancel_timer()
        self._show()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass


class FocusController:
    def __init__(self, layout):
        """
        layout: MonitorLayout — usado para construir o DesktopMap.
        """
        _apply_foreground_lock_timeout()
        self.detector    = SaccadeDetector()
        self.overlay     = OverlayBorder()
        self.gaze_dot    = GazeDot()
        self.desktop_map = DesktopMap(layout.monitors)
        self._current_hwnd      = None
        self._fps               = 0.0
        self._last_frame_time   = None
        self._last_valid_pos    = None  # última (ax, ay) válida — usada quando gaze sai do range

    def update(self, zone, dominant_window, ax=None, ay=None):
        """
        Deve ser chamado a cada frame.
        zone, dominant_window: zona atual e janela dominante (None se gaze perdido).
        ax, ay: coordenadas absolutas do gaze no desktop (None se gaze perdido).
        """
        now = time.time()

        # FPS tracking via EMA
        if self._last_frame_time is not None:
            elapsed = now - self._last_frame_time
            if elapsed > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / elapsed)
        self._last_frame_time = now

        # Gaze fora do range ou rosto não detectado — congela overlay na última posição válida
        if zone is None or ax is None:
            if self._last_valid_pos is not None:
                lax, lay = self._last_valid_pos
                self.gaze_dot.set_position(lax, lay)
                self.desktop_map.set_gaze(lax, lay)
            else:
                self.desktop_map.set_gaze(None, None)
            return

        # Atualiza posição do ponto de gaze a cada frame e salva como última válida
        self._last_valid_pos = (ax, ay)
        self.gaze_dot.set_position(ax, ay)
        self.desktop_map.set_gaze(ax, ay)

        if dominant_window is None:
            return

        triggered = self.detector.update(zone)
        if triggered:
            hwnd = dominant_window["hwnd"]
            if hwnd != self._current_hwnd:
                self._current_hwnd = hwnd
                _force_foreground(hwnd)
                self.overlay.set_target(hwnd)
                print(f"[Glaze] Foco → {dominant_window['title']}")
            # Feedback visual: anel sólido por 500ms + atualiza status do mapa
            self.gaze_dot.set_stable(True)
            self.desktop_map.set_info(zone, dominant_window["title"], self._fps)

    def toggle_overlay(self):
        self.overlay.toggle()
        self.gaze_dot.toggle()
        self.desktop_map.toggle()
