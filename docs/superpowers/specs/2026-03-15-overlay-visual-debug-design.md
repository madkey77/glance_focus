# Overlay Visual Debug — Design Spec

**Data:** 2026-03-15
**Projeto:** Glaze — Eye-Tracking Window Focus
**Escopo:** Modo overlay com visualização do ponto de gaze, bordas coloridas por janela, e mapa do desktop

---

## Contexto

O Glaze já possui um `OverlayBorder` em `focus_controller.py` — uma janela Tkinter transparente que desenha uma borda fixa (`#00FF88`) em torno da janela em foco pelo gaze. O `GazeTracker` produz `(x_norm, y_norm)` suavizado mas esse valor não é exibido visualmente. O `diag_camera.py` existe como script de debug separado com OpenCV.

O objetivo é enriquecer o modo overlay existente sem criar dependências externas novas (sem OpenCV no modo principal) e sem comprometer a performance do loop de 30fps.

---

## Decisões de Design

- **Abordagem:** HUD Tkinter integrado (sem janela OpenCV separada no modo principal)
- **Posição do HUD:** Canto inferior esquerdo do monitor primário
- **Elementos selecionados:** ponto de gaze pulsante, mapa do desktop, bordas coloridas por janela
- **Ativação:** Ctrl+Alt+B controla todos os componentes juntos (mesmo hotkey atual)

---

## Componentes

### 1. GazeDot — Ponto de Gaze Pulsante

Nova classe em `focus_controller.py`. Janela Tkinter própria, transparente, sempre no topo.

**Comportamento:**
- Exibe um anel animado na posição absoluta do gaze no monitor ativo
- Anel externo: ~24px de diâmetro; ponto central: 8px; cor branca com borda escura para contraste em qualquer fundo
- **Animação pulsante:** o anel expande e some em loop (~1s por ciclo) enquanto o gaze está em movimento/instável
- **Estabilização:** quando `FocusController` chama `set_stable(True)`, a animação para e o anel fica sólido por ~500ms. O timer de retomada é responsabilidade interna de `GazeDot`: após 500ms, ela mesma reseta `_stable = False` via `after()`. `FocusController` nunca chama `set_stable(False)`.
- **Auto-hide por timeout:** o loop `after()` interno verifica a cada iteração se `time.time() - _last_position_update > 0.2`. Se sim, esconde a janela. `set_position` atualiza `_last_position_update` a cada chamada. Isso garante que GazeDot some quando gaze é perdido, mesmo sem chamada explícita.

**Threading:** Toda chamada pública (`set_position`, `set_stable`, `show`, `hide`) apenas escreve em atributos protegidos por `threading.Lock`. O loop `after()` interno lê esses atributos e atualiza o canvas — nunca o contrário. Inclui `_visible` no lock para evitar race condition (o `OverlayBorder` atual não protege `_visible` — o refactor deve corrigir isso nos três componentes).

**Interface:**
```python
class GazeDot:
    def set_position(self, ax: int, ay: int): ...  # thread-safe via lock; atualiza _last_position_update
    def set_stable(self, stable: bool): ...         # thread-safe via lock; True = para animação por 500ms
    def show(self): ...                             # thread-safe via lock
    def hide(self): ...                             # thread-safe via lock
```

---

### 2. OverlayBorder — Bordas Coloridas por Janela

Refatoração da classe existente. Atualmente usa `#00FF88` fixo.

**Comportamento:**
- Paleta de 8 cores vivas distintas: `["#00FF88", "#00CFFF", "#FFD700", "#FF6B35", "#FF00FF", "#4FC3F7", "#FF4444", "#FFFFFF"]`
- Mapeamento `hwnd → cor` persistente durante a sessão (dict interno `_color_map`)
- Quando uma janela recebe foco pela primeira vez, recebe a próxima cor disponível da paleta (round-robin via índice `_color_idx`)
- Janelas já vistas reutilizam a cor atribuída anteriormente
- Interface pública: `set_target(hwnd)` — mesma de hoje, lógica de cor é interna
- Corrigir durante o refactor: `_visible` deve ser lido dentro do lock no `_update_loop`, igualando ao padrão dos novos componentes

---

### 3. DesktopMap — Mapa do Desktop

Nova classe em `focus_controller.py`. Janela Tkinter transparente, fixada no canto inferior esquerdo do monitor primário.

**Monitor primário:** identificado como o monitor com `left == 0 and top == 0` entre os retornados por `MonitorLayout.monitors`. Se nenhum satisfizer (configuração incomum), usa o primeiro da lista.

**Coordenadas do minimap:** O desktop virtual pode ter coordenadas negativas (monitores à esquerda do primário têm `left < 0`). O DesktopMap normaliza calculando `min_x = min(m["left"] for m in monitors)` e `min_y = min(m["top"] for m in monitors)` como origem, depois escala tudo para caber em 240×100px mantendo proporção.

**Posicionamento inicial:** usar geometria fixa `260x140` na chamada `geometry()` — o tamanho é determinístico conforme o layout abaixo, não requer `update_idletasks()`. Posição: `x = primary["left"] + 10`, `y = primary["bottom"] - 140 - 10`.

**Layout:**
```
┌──────────────────────────────┐
│  [miniatura do desktop]      │  ~240×100px
│   MON0      MON1             │  zonas como retângulos
│   □  □  ●   □  □            │  ● = ponto de gaze
├──────────────────────────────┤
│ gaze=(0.52,0.48) M0-Q1 29fps │  texto de status
└──────────────────────────────┘
        Tamanho total: 260×140px (fixo)
```

**Comportamento:**
- Atualiza a ~10fps (a cada 100ms via `after()`)
- Recebe layout de monitores/zonas na construção (`MonitorLayout`)
- Recebe posição absoluta do gaze via `set_gaze(ax, ay)`; `None, None` indica gaze perdido — oculta o ponto no mapa
- Recebe info de status via `set_info(zone, title, fps)`:
  - `zone`: dict com `monitor_id` e `quadrant` — exibido como `"M{monitor_id}-Q{quadrant}"`
  - `title`: `dominant_window["title"]` (campo já existente nos dicts do `QuadrantMapper`)
  - `fps`: float calculado em `main.py` (ver seção abaixo)
- Fundo: preto semi-transparente (`#1a1a1a`, alpha via atributo `-alpha 0.85`)

**Threading:** mesmo padrão de `GazeDot` — métodos públicos escrevem em atributos com lock; `after()` lê e renderiza. `_visible` incluído no lock.

**Interface:**
```python
class DesktopMap:
    def __init__(self, layout: MonitorLayout): ...
    def set_gaze(self, ax: int | None, ay: int | None): ...  # None = gaze perdido
    def set_info(self, zone: dict | None, title: str, fps: float): ...
    def show(self): ...
    def hide(self): ...
```

---

### 4. FocusController — Integração

Mudanças em `FocusController`:

- Instancia `GazeDot`, `DesktopMap` no `__init__` (recebe `layout: MonitorLayout` como novo parâmetro)
- Assinatura: `update(zone, dominant_window, ax=None, ay=None)`
- **Atenção:** `ax=None` como default cria silenciosamente um estado "sem gaze" — as mudanças em `main.py` e `focus_controller.py` devem ser feitas juntas
- Quando `zone is None` ou `ax is None`: `desktop_map.set_gaze(None, None)` é chamado; `gaze_dot.set_position` não é chamado (GazeDot faz timeout interno)
- Quando `zone is not None` e `ax is not None`: chama `gaze_dot.set_position(ax, ay)` e `desktop_map.set_gaze(ax, ay)` a cada frame
- Quando saccade dispara (`triggered == True`): chama `gaze_dot.set_stable(True)` e `desktop_map.set_info(zone, dominant_window["title"], fps)` onde `fps` vem de `self._fps` (ver abaixo)
- `toggle_overlay()` liga/desliga `GazeDot`, `OverlayBorder` e `DesktopMap` juntos

**FPS tracking em FocusController:**
- Atributo `_fps: float = 0.0` e `_last_frame_time: float`
- `update()` calcula `elapsed = time.time() - _last_frame_time` e atualiza `_fps` com EMA (`alpha=0.1`): `_fps = 0.9 * _fps + 0.1 * (1/elapsed)`
- Não requer mudança em `main.py` para FPS

---

## Mudanças em main.py

**Mudança 1 — instanciação do FocusController:**
```python
# Antes:
controller = FocusController()

# Depois:
controller = FocusController(layout)
```

**Mudança 2 — chamada no loop principal:**
```python
# Antes:
zone = layout.get_zone(ax, ay)
if zone is not None:
    dominant = mapper.get_dominant(zone)
    controller.update(zone, dominant)

# Depois:
zone = layout.get_zone(ax, ay)
if zone is not None:
    dominant = mapper.get_dominant(zone)
    controller.update(zone, dominant, ax, ay)
else:
    controller.update(None, None)  # notifica gaze perdido
```

---

## Não está no escopo

- Miniatura da câmera no HUD (não selecionado pelo usuário)
- Info de texto como item separado (incorporado no DesktopMap)
- Janela OpenCV no modo principal
- Hotkey separada para o mapa/dot (tudo no Ctrl+Alt+B existente)

---

## Arquivos afetados

| Arquivo | Mudança |
|---|---|
| `glaze-app/focus_controller.py` | Refatorar `OverlayBorder` (cores + lock em `_visible`); adicionar `GazeDot`, `DesktopMap`; atualizar `FocusController` |
| `glaze-app/main.py` | Passar `layout` ao `FocusController`; passar `(ax, ay)` para `controller.update()`; notificar gaze perdido |

---

## Considerações de Threading

- Cada componente visual (`GazeDot`, `DesktopMap`, `OverlayBorder`) roda em thread própria com Tkinter (padrão já estabelecido no `OverlayBorder` existente)
- **Regra uniforme:** métodos públicos apenas escrevem em atributos protegidos por `threading.Lock` (incluindo `_visible`). O loop `after()` interno lê e renderiza. Nunca chamar Tkinter de outra thread diretamente.
- O loop principal em `main.py` continua a 30fps; GazeDot atualiza a 30fps via `after(33, ...)`, DesktopMap a 10fps via `after(100, ...)`

---

## Critérios de Aceitação

- [ ] Ponto de gaze aparece no monitor na posição correta e pulsa visivelmente
- [ ] Ponto para de pulsar por ~500ms após saccade confirmado, retomando automaticamente sem intervenção externa
- [ ] GazeDot se esconde quando gaze é perdido por >200ms
- [ ] Cada janela contornada tem cor diferente das demais; a cor é consistente entre frames
- [ ] Mapa do desktop aparece no canto inferior esquerdo com miniatura proporcional de todos os monitores
- [ ] Ponto no mapa corresponde à posição real do gaze; some quando gaze é perdido
- [ ] Coordenadas negativas de monitor são tratadas corretamente no minimap
- [ ] Status text no DesktopMap exibe zona, título da janela e FPS
- [ ] Ctrl+Alt+B liga/desliga todos os componentes juntos
- [ ] Nenhuma degradação perceptível no FPS do loop principal (target: 30fps)
