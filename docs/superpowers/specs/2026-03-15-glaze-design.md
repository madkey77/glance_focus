# Glaze — Eye-Tracking Window Focus

**Data:** 2026-03-15
**Status:** Aprovado

---

## Objetivo

Programa que usa uma webcam para detectar para qual janela do Windows o usuário está olhando e automaticamente traz essa janela para foco. O objetivo é facilitar a troca de contexto entre diferentes janelas sem uso do mouse ou teclado.

---

## Decisões de Design

| Tema | Decisão |
|------|---------|
| Modelo de ativação | Saccade detection — ativa quando o olhar "pousa" em nova região (~150ms de estabilidade) |
| Ação ao ativar | `SetForegroundWindow` apenas (sem mover mouse) |
| Overlay visual | Borda sutil na janela ativa — togglável por hotkey, para fins de debug/teste |
| Monitores | Suporte a todos os monitores, usando nomes e layout real do Windows |
| Calibração | 4 cantos + centro (5 pontos) por monitor, homografia 2D, persiste em JSON |
| Mapeamento de janelas | Divisão em 4 quadrantes por monitor; janela dominante = maior área de sobreposição |
| Filtro de janelas | Apenas visíveis, não minimizadas, tamanho mínimo 200x200px |
| Interface | Só hotkeys globais (sem tray, sem UI) |
| Plataforma de execução | Windows nativo (Python Win32) |
| Desenvolvimento | Arquivos em `E:\projetos\glaze\glaze-app\`, editados via WSL |

---

## Estrutura de Pastas

```
E:\projetos\glaze\
├── docs\                        ← planejamento, specs (este arquivo)
└── glaze-app\                   ← repositório do código
    ├── main.py
    ├── gaze_tracker.py
    ├── calibration.py
    ├── monitor_layout.py
    ├── quadrant_mapper.py
    ├── focus_controller.py
    ├── calibration.json         ← gerado após calibração
    └── requirements.txt
```

---

## Arquitetura Geral

Pipeline executado a ~30fps no loop principal:

```
webcam
  ↓
gaze_tracker.py      — MediaPipe FaceMesh + lógica do MonitorTracking.py
                        → (x, y) normalizado [0..1]
  ↓
calibration.py       — homografia: (x,y) normalizado → coordenada absoluta desktop (px)
  ↓
monitor_layout.py    — qual monitor? qual quadrante?
  ↓
quadrant_mapper.py   — qual janela é dominante nesse quadrante?
  ↓
focus_controller.py  — saccade detection → SetForegroundWindow + overlay visual
  ↓
main.py              — hotkeys globais, fluxo de calibração, loop principal
```

---

## Módulos

### `gaze_tracker.py`
Encapsula a lógica do `MonitorTracking.py` como classe `GazeTracker`.
- Método principal: `get_gaze() → (x, y) | None`
- x, y normalizados [0..1] relativo à tela calibrada
- Roda MediaPipe FaceMesh em thread separada (não bloqueia o loop principal)
- Retorna `None` quando rosto não detectado
- Base: reutiliza lógica de orientação de cabeça + gaze ray do repositório de referência

### `calibration.py`
Conduz o fluxo de calibração interativo.
- Para cada monitor (em ordem, usando nomes reais do Windows): exibe janela fullscreen com ponto guia nos 4 cantos + centro
- Usuário olha para o ponto e pressiona `Space`
- Coleta 5 amostras por ponto, faz média
- Calcula homografia 2D (transformação perspectiva) mapeando gaze normalizado → pixels absolutos
- Persiste resultado em `calibration.json`
- No startup: carrega `calibration.json` automaticamente se existir

### `monitor_layout.py`
Detecta configuração real de monitores do Windows.
- Usa `win32api.EnumDisplayMonitors` para obter: nome, resolução, posição no desktop virtual
- Entende layout (lado a lado, um acima do outro)
- Divide cada monitor em 4 quadrantes
- Expõe: `get_quadrant(x_abs, y_abs) → (monitor_id, quadrant_id)`

### `quadrant_mapper.py`
Mapeia quadrante → janela dominante.
- Usa `win32gui.EnumWindows` para listar janelas visíveis
- Filtra: tamanho mínimo 200x200px, não minimizadas, com título
- Para cada quadrante: calcula qual janela tem maior área de sobreposição
- Atualiza a cada 500ms (não em todo frame)

### `focus_controller.py`
Controla o foco e o overlay visual.
- Mantém estado do quadrante atual
- Saccade detection: mudança de quadrante → aguarda 150ms de estabilidade → `SetForegroundWindow`
  (evita ativações acidentais ao atravessar quadrantes)
- Overlay: janela transparente sempre-no-topo com borda colorida na janela ativa
- Toggle do overlay via hotkey

### `main.py`
Orquestra tudo.
- Loop principal ~30fps
- Hotkeys globais via `keyboard` lib:
  - `Ctrl+Alt+G` — liga/desliga tracking
  - `Ctrl+Alt+B` — toggle overlay visual
  - `Ctrl+Alt+C` — inicia re-calibração
  - `Ctrl+Alt+Q` — encerra o programa

---

## Calibração — Fluxo Detalhado

1. `Ctrl+Alt+C` → detecta monitores via `win32api`, obtém nomes reais (ex: "DELL U2722D")
2. Terminal exibe: `"Calibrando: DELL U2722D (Monitor 1 de 2)"`
3. Abre janela fullscreen no monitor alvo — fundo escuro, círculo branco pulsante
4. Sequência: **centro primeiro** (inicializa coordenadas 3D do rosto), depois 4 cantos
5. Instrução na tela: `"Olhe para o ponto e pressione SPACE"`
6. Coleta 5 amostras por ponto, faz média
7. Repete para cada monitor
8. Salva `calibration.json`, retorna ao tracking normal

> O centro é calibrado primeiro porque o tracker de referência usa esse ponto para inicializar o sistema de coordenadas 3D do rosto.

---

## Saccade Detection e Mapeamento de Quadrantes

**Layout de quadrantes** (exemplo: 2 monitores lado a lado):

```
Monitor 1              Monitor 2
┌────────┬────────┐   ┌────────┬────────┐
│  Q1    │  Q2    │   │  Q5    │  Q6    │
│        │        │   │        │        │
├────────┼────────┤   ├────────┼────────┤
│  Q3    │  Q4    │   │  Q7    │  Q8    │
│        │        │   │        │        │
└────────┴────────┘   └────────┴────────┘
```

**Lógica de ativação (frame a frame):**

1. Gaze retorna coordenada absoluta → mapeada para `(monitor_id, quadrant_id)`
2. Se quadrante mudou em relação ao anterior → inicia timer de 150ms
3. Se após 150ms o gaze ainda está no mesmo quadrante → confirma saccade → `SetForegroundWindow`
4. Se o gaze saiu antes dos 150ms → cancela (foi só passagem)

**Janela dominante por quadrante:**
- Janela com maior área de interseção com o quadrante
- Atualizada a cada 500ms (não em todo frame)
- Em caso de empate: prioriza janela mais ao topo do Z-order

---

## Riscos Técnicos e Mitigações

| # | Risco | Mitigação |
|---|-------|-----------|
| 1 | Gaze oscilando entre zonas (erro residual ~50-100px) | Timer de 150ms configurável + filtro de média móvel no gaze antes do mapeamento de zonas |
| 2 | `SetForegroundWindow` bloqueado pelo Windows 10/11 | Usar `AttachThreadInput` + `BringWindowToTop` + `keybd_event` workaround (solução documentada) |
| 3 | Performance do MediaPipe | Resolução de captura configurável (padrão 480x360); MediaPipe Python no Windows roda apenas em CPU — com 5600X + 480x360 é suficiente para 30fps |

## Configuração e Parâmetros

Parâmetros configuráveis expostos no topo de `main.py` (ou futuro `config.py`):

```python
CAMERA_INDEX = 0
CAPTURE_WIDTH = 480
CAPTURE_HEIGHT = 360
SACCADE_STABLE_MS = 150      # tempo de estabilidade para confirmar saccade
GAZE_SMOOTH_FRAMES = 10      # janela do filtro de média móvel
ZONE_LAYOUT = "2x2"          # "2x2" | "4x1" | "1x4" | futuras configurações
QUADRANT_UPDATE_MS = 500     # frequência de atualização das janelas dominantes
MIN_WINDOW_SIZE = 200        # px — tamanho mínimo de janela considerada
```

---

## Referência Externa

- **Repositório base:** [JEOresearch/EyeTracker — Webcam3DTracker](https://github.com/JEOresearch/EyeTracker/tree/main/Webcam3DTracker)
- **Arquivo principal:** `MonitorTracking.py`
- **Tecnologias reutilizadas:** MediaPipe FaceMesh, orientação de cabeça 3D, gaze ray casting

---

## Dependências (estimadas)

```
opencv-python
numpy
mediapipe
scipy
pyautogui
keyboard
pywin32        ← win32api, win32gui
```

---

## Instruções para o Usuário (Windows)

A cada entrega de código, serão geradas instruções claras do que o usuário precisa fazer no Windows para rodar ou testar. O formato padrão será:

```
## O que fazer no Windows

1. Abra o PowerShell ou Prompt de Comando
2. Navegue até a pasta: cd E:\projetos\glaze\glaze-app
3. [comandos específicos da entrega]
```

Isso inclui:
- Instalação de dependências (`pip install ...`)
- Como rodar o programa (`python main.py`)
- Como testar uma funcionalidade específica
- O que esperar ver na tela / comportamento esperado
- Como reportar problemas (logs, mensagens de erro relevantes)

---

## Ideias Futuras (fora do escopo v1)

- Após 1s de dwell time na janela ativa: mover mouse para o centro dela
- Whitelist/blacklist de aplicativos
- Calibração adaptativa (ajuste fino sem recalibrar tudo)
- Aceleração GPU: trocar MediaPipe por InsightFace + ONNX Runtime CUDA (RTX 4060 Ti disponível) para ganho de performance e precisão
