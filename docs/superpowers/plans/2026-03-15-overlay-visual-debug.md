# Overlay Visual Debug Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pulsing gaze-point dot, per-window colored borders, and a desktop minimap HUD to Glaze's overlay mode.

**Architecture:** Three Tkinter overlay components (`GazeDot`, refactored `OverlayBorder`, `DesktopMap`) each run in their own daemon thread following the existing `OverlayBorder` pattern. All public methods are thread-safe via `threading.Lock`; a polling `after()` loop handles rendering. `FocusController` orchestrates all three and receives `layout: MonitorLayout` at construction.

**Tech Stack:** Python 3, Tkinter (overlays), threading.Lock, win32gui, pynput, existing project structure in `glaze-app/`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `glaze-app/focus_controller.py` | Modify | Add `GazeDot`, `DesktopMap`; refactor `OverlayBorder`; update `FocusController` |
| `glaze-app/main.py` | Modify | Pass `layout` to `FocusController`; pass `ax, ay`; add `else` branch for lost gaze |

No new files. Both components and the controller live in `focus_controller.py` following the existing pattern.

> **Note on virtual environment:** All `python` commands below must be run with the project's venv active. From the repo root: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (WSL). Syntax checks use `python -m py_compile` to work in any environment (no display required).

---

## Chunk 1: GazeDot — Pulsing Gaze Point

### Task 1: GazeDot class with thread-safe state

**Files:**
- Modify: `glaze-app/focus_controller.py` (after `OverlayBorder` class, before `FocusController`)

- [ ] **Step 1: Open `glaze-app/focus_controller.py` and locate the end of `OverlayBorder` class. Insert `GazeDot` class after it.**

Add this class after `OverlayBorder`:

```python
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
        self._root         = None
        self._pulse_phase  = 0.0   # 0..1 para animação pulsante
        threading.Thread(target=self._run, daemon=True).start()

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
        import time
        now = time.time()
        with self._lock:
            visible      = self._visible
            ax           = self._ax
            ay           = self._ay
            last_upd     = self._last_update
            stable       = now < self._stable_until

        # Auto-hide se não recebe posição há >200ms (gaze perdido)
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
            # Tkinter Canvas não suporta alpha por shape — simula com tamanho crescente
            self._pulse_phase = (self._pulse_phase + self.PULSE_MS / 1000.0) % 1.0
            t = self._pulse_phase
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
        import time
        with self._lock:
            self._ax          = ax
            self._ay          = ay
            self._last_update = time.time()

    def set_stable(self, stable: bool):
        """
        stable=True: inicia período sólido de 500ms (timer interno).
        FocusController nunca chama set_stable(False) — o timer expira sozinho.
        """
        import time
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
```

- [ ] **Step 2: Verify the file is syntactically valid (no display required)**

```bash
cd glaze-app && python -m py_compile focus_controller.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add glaze-app/focus_controller.py
git commit -m "feat: add GazeDot — pulsing transparent gaze point overlay"
```

---

## Chunk 2: OverlayBorder — Per-Window Colors

### Task 2: Refactor OverlayBorder to use per-hwnd color palette

**Files:**
- Modify: `glaze-app/focus_controller.py` — `OverlayBorder` class

- [ ] **Step 1: Replace the entire `OverlayBorder` class with the refactored version**

```python
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
```

- [ ] **Step 2: Verify syntax**

```bash
cd glaze-app && python -m py_compile focus_controller.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add glaze-app/focus_controller.py
git commit -m "feat: per-window colored borders in OverlayBorder (palette of 8 colors)"
```

---

## Chunk 3: DesktopMap — HUD Minimap

### Task 3: DesktopMap class

**Files:**
- Modify: `glaze-app/focus_controller.py` (after `GazeDot`, before `FocusController`)

- [ ] **Step 1: Insert `DesktopMap` class after `GazeDot`**

```python
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
        Monitores e ponto de gaze chamam _to_map, garantindo offset consistente.
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
```

- [ ] **Step 2: Verify syntax**

```bash
cd glaze-app && python -m py_compile focus_controller.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add glaze-app/focus_controller.py
git commit -m "feat: add DesktopMap HUD — desktop minimap with gaze point and status"
```

---

## Chunk 4: FocusController Integration + main.py

### Task 4: Update FocusController to wire all three components

**Files:**
- Modify: `glaze-app/focus_controller.py` — `FocusController` class
- Modify: `glaze-app/main.py`

- [ ] **Step 1: Replace the entire `FocusController` class with the updated version**

```python
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

    def update(self, zone, dominant_window, ax=None, ay=None):
        """
        Deve ser chamado a cada frame.
        zone, dominant_window: zona atual e janela dominante (None se gaze perdido).
        ax, ay: coordenadas absolutas do gaze no desktop (None se gaze perdido).

        ATENÇÃO: ax=None é o default — se main.py não passar ax/ay, GazeDot e
        DesktopMap nunca recebem posição. As mudanças em focus_controller.py e
        main.py devem ser feitas em conjunto (ver Task 4, Steps 1-3).
        """
        import time
        now = time.time()

        # FPS tracking via EMA
        if self._last_frame_time is not None:
            elapsed = now - self._last_frame_time
            if elapsed > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / elapsed)
        self._last_frame_time = now

        # Gaze perdido — notifica DesktopMap; GazeDot faz auto-hide por timeout interno
        if zone is None or ax is None:
            self.desktop_map.set_gaze(None, None)
            return

        # Atualiza posição do ponto de gaze a cada frame
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
```

- [ ] **Step 2: Verify syntax of focus_controller.py**

```bash
cd glaze-app && python -m py_compile focus_controller.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Update `main.py` — duas mudanças coordenadas**

**Mudança 1** — linha ~75, substituir:
```python
    controller = FocusController()
```
por:
```python
    controller = FocusController(layout)
```

**Mudança 2** — no loop principal, substituir o bloco completo (incluindo o `for` interno):
```python
        if tracking_enabled[0] and calibration.is_calibrated():
            gaze = tracker.get_gaze()
            if gaze is not None:
                gx, gy = gaze
                for monitor in layout.monitors:
                    abs_pos = calibration.apply(monitor["id"], gx, gy)
                    if abs_pos is None:
                        continue
                    ax, ay = abs_pos
                    zone = layout.get_zone(ax, ay)
                    if zone is not None:
                        dominant = mapper.get_dominant(zone)
                        controller.update(zone, dominant)
                        break
```
por:
```python
        if tracking_enabled[0] and calibration.is_calibrated():
            gaze = tracker.get_gaze()
            if gaze is not None:
                gx, gy = gaze
                _found = False
                for monitor in layout.monitors:
                    abs_pos = calibration.apply(monitor["id"], gx, gy)
                    if abs_pos is None:
                        continue
                    ax, ay = abs_pos
                    zone = layout.get_zone(ax, ay)
                    if zone is not None:
                        dominant = mapper.get_dominant(zone)
                        controller.update(zone, dominant, ax, ay)
                        _found = True
                        break
                if not _found:
                    controller.update(None, None)
```

> **Nota:** usa `_found` flag em vez de `for...else` para maior clareza de intenção.

- [ ] **Step 4: Verify syntax of both files**

```bash
cd glaze-app && python -m py_compile focus_controller.py main.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 5: Commit (ambos os arquivos juntos)**

```bash
git add glaze-app/focus_controller.py glaze-app/main.py
git commit -m "feat: wire GazeDot + DesktopMap into FocusController; update main.py"
```

---

## Chunk 5: Manual Validation Checklist

### Task 5: Run the app and verify all overlay features

**Files:** nenhum — apenas validação manual (requer sessão GUI Windows)

- [ ] **Step 1: Run the app**

```bash
cd glaze-app && python main.py
```

Expected: app inicia sem exceção, imprime `[Glaze] Tracker iniciado.`

- [ ] **Step 2: Ativar overlay (Ctrl+Alt+B) e verificar ponto de gaze**

- [ ] Um ponto branco aparece aproximadamente onde você está olhando no monitor ativo
- [ ] O ponto pulsa continuamente (anel que cresce e encolhe) enquanto o olhar se move
- [ ] Após o gaze estabilizar numa zona (~200ms), o anel para de pulsar e fica sólido por ~500ms
- [ ] Depois dos 500ms, o anel retoma a pulsação automaticamente (sem nenhuma ação do usuário)

- [ ] **Step 3: Verificar auto-hide do ponto de gaze**

- [ ] Cubra ou desvie o rosto da câmera por mais de 200ms
- [ ] O ponto de gaze deve desaparecer da tela
- [ ] Ao retornar para a câmera, o ponto deve reaparecer na nova posição

- [ ] **Step 4: Verificar bordas coloridas**

- [ ] Olhe para 3 janelas diferentes: cada uma deve receber uma borda de cor diferente
- [ ] Volte para uma janela já visitada: a cor deve ser a mesma de antes
- [ ] As bordas devem desaparecer ao desativar overlay (Ctrl+Alt+B)

- [ ] **Step 5: Verificar DesktopMap**

- [ ] Painel escuro aparece no canto inferior esquerdo do monitor primário
- [ ] O painel mostra um ou mais retângulos representando os monitores conectados
- [ ] Um ponto branco se move no mapa conforme o gaze muda de posição
- [ ] A linha de status exibe: zona (ex: `M0-Q1`), título da janela, e FPS (ex: `29fps`)
- [ ] Ao perder o gaze (cobrir câmera), o ponto branco some do mapa

- [ ] **Step 6: Verificar coordenadas negativas (se aplicável)**

> Se você tiver um monitor posicionado à esquerda do monitor primário no Windows (coordenadas negativas), verifique se ele aparece corretamente no minimap do DesktopMap. Se não tiver esse setup, marque este item como N/A.

- [ ] Monitor com coordenadas negativas aparece no minimap sem distorção

- [ ] **Step 7: Verificar toggle completo (Ctrl+Alt+B)**

- [ ] Pressionar Ctrl+Alt+B uma vez: ponto de gaze, bordas e DesktopMap desaparecem juntos
- [ ] Pressionar Ctrl+Alt+B novamente: todos reaparecem juntos

- [ ] **Step 8: Verificar FPS do loop principal**

- [ ] Observe o FPS mostrado no DesktopMap durante uso normal
- [ ] Deve ficar próximo a 30fps (variações de ±5fps são aceitáveis)

- [ ] **Step 9: Commit final**

```bash
git add glaze-app/focus_controller.py glaze-app/main.py
git commit -m "chore: overlay visual debug complete — gaze dot, colors, desktop map"
```
