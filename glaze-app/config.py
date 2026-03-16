# glaze-app/config.py

CAMERA_INDEX = 0
CAPTURE_WIDTH = 480
CAPTURE_HEIGHT = 360

SACCADE_STABLE_MS = 200       # ms de estabilidade para confirmar saccade
GAZE_SMOOTH_FRAMES = 10       # janela do filtro de média móvel
ZONE_LAYOUT = "2x2"           # "2x2" | "4x1" | "1x4"
QUADRANT_UPDATE_MS = 500      # frequência de atualização das janelas dominantes
MIN_WINDOW_SIZE = 200         # px — tamanho mínimo de janela considerada

GAZE_PITCH_IRIS_WEIGHT = 5.0   # multiplicador da íris no eixo Y (era 3.0) — maior = mais íris, menos cabeça
GAZE_Y_RANGE_DEG       = 180   # range vertical em graus (era 130) — maior = mais centralizado

CALIBRATION_FILE = "calibration.json"
CALIBRATION_SAMPLES = 5       # amostras coletadas por ponto de calibração

SWEEP_SPEED       = 0.08   # normalized units/second — ball speed
SWEEP_ROWS        = 5      # number of horizontal rows
SWEEP_MIN_SAMPLES = 30     # minimum valid samples to attempt poly fit

MOUSE_HIDE_DELAY_S = 2.0   # seconds of inactivity before cursor hides
