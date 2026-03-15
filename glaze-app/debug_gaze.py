# glaze-app/debug_gaze.py
"""
Preview visual do gaze tracking para debug.
Mostra rosto com landmarks sobrepostos + indicadores de gaze em tempo real.

Uso: python debug_gaze.py
Teclas:
  Q    — sair
  S    — salvar screenshot do frame atual
"""

import sys
import math
import time

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

try:
    from config import CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT
except ImportError:
    CAMERA_INDEX = 0
    CAPTURE_WIDTH = 480
    CAPTURE_HEIGHT = 360

MODEL_PATH = "face_landmarker.task"

NOSE_INDICES = [4, 45, 275, 220, 440, 1, 5, 51, 281, 44, 274, 241,
                461, 125, 354, 218, 438, 195, 167, 393, 165, 391, 3, 248]
LEFT_IRIS        = [474, 475, 476, 477]
RIGHT_IRIS       = [469, 470, 471, 472]
LEFT_EYE_CORNERS = [33, 133]
RIGHT_EYE_CORNERS = [362, 263]


def _normalize(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _rot_x(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]], dtype=float)


def _rot_y(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]], dtype=float)


def _compute_head_rotation(landmarks, w, h, ref_container):
    pts = np.array([[landmarks[i].x * w,
                     landmarks[i].y * h,
                     landmarks[i].z * w]
                    for i in NOSE_INDICES], dtype=float)
    pts -= pts.mean(axis=0)
    cov = pts.T @ pts
    _, vecs = np.linalg.eigh(cov)
    vecs = vecs[:, ::-1]
    if ref_container[0] is None:
        ref_container[0] = vecs.copy()
    else:
        for i in range(3):
            if np.dot(vecs[:, i], ref_container[0][:, i]) < 0:
                vecs[:, i] *= -1
    return vecs


def _get_iris_center(landmarks, indices, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in indices])
    return pts.mean(axis=0)


def _get_eye_center(landmarks, corners, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in corners])
    return pts.mean(axis=0)


def _compute_gaze(landmarks, R, w, h):
    l_iris = _get_iris_center(landmarks, LEFT_IRIS,  w, h)
    r_iris = _get_iris_center(landmarks, RIGHT_IRIS, w, h)
    l_eye  = _get_eye_center(landmarks, LEFT_EYE_CORNERS,  w, h)
    r_eye  = _get_eye_center(landmarks, RIGHT_EYE_CORNERS, w, h)

    l_off = l_iris - l_eye
    r_off = r_iris - r_eye
    offset = (l_off + r_off) * 0.5

    eye_width = np.linalg.norm(
        np.array([landmarks[133].x - landmarks[33].x,
                  landmarks[133].y - landmarks[33].y]) * w
    )
    if eye_width > 1e-6:
        offset /= eye_width

    head_forward = R[:, 2]
    yaw_iris   = -offset[0] * 0.8
    pitch_iris =  offset[1] * 0.8
    gaze = _rot_y(yaw_iris) @ _rot_x(pitch_iris) @ head_forward
    return _normalize(gaze), head_forward, l_iris, r_iris, l_eye, r_eye


def draw_text(img, text, pos, scale=0.55, color=(255,255,255), thickness=1, bg=True):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x, y = pos
    if bg:
        cv2.rectangle(img, (x-2, y-th-3), (x+tw+2, y+3), (0,0,0), -1)
    cv2.putText(img, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def draw_iris(img, landmarks, indices, w, h, color):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in indices], dtype=int)
    center = pts.mean(axis=0).astype(int)
    # Estima raio da íris
    diffs = pts - center
    radius = int(np.linalg.norm(diffs, axis=1).mean()) + 2
    cv2.circle(img, tuple(center), radius, color, 2)
    cv2.circle(img, tuple(center), 2, color, -1)
    return center


def draw_gaze_arrow(img, eye_center_px, gaze_dir, length=60, color=(0,255,128)):
    """Desenha seta de gaze a partir do centro do olho."""
    # Projeta gaze_dir no plano 2D da imagem (ignora Z)
    dx = gaze_dir[0]
    dy = gaze_dir[1]
    norm = math.sqrt(dx*dx + dy*dy)
    if norm < 1e-6:
        return
    dx, dy = dx/norm, dy/norm
    x1, y1 = int(eye_center_px[0]), int(eye_center_px[1])
    x2 = int(x1 + dx * length)
    y2 = int(y1 + dy * length)
    cv2.arrowedLine(img, (x1,y1), (x2,y2), color, 2, tipLength=0.3)


def draw_head_axes(img, R, nose_px, w, h, scale=50):
    """Desenha eixos X/Y/Z da cabeça no nariz."""
    ox, oy = int(nose_px[0]), int(nose_px[1])
    # X = vermelho (esquerda-direita da cabeça)
    cv2.arrowedLine(img, (ox,oy),
                    (int(ox + R[0,0]*scale), int(oy + R[1,0]*scale)),
                    (0,0,255), 2, tipLength=0.3)
    # Y = verde (cima-baixo da cabeça)
    cv2.arrowedLine(img, (ox,oy),
                    (int(ox + R[0,1]*scale), int(oy + R[1,1]*scale)),
                    (0,255,0), 2, tipLength=0.3)
    # Z = azul (profundidade = forward)
    # -R[:,2] é o head_forward; mostramos ele
    cv2.arrowedLine(img, (ox,oy),
                    (int(ox - R[0,2]*scale), int(oy - R[1,2]*scale)),
                    (255,128,0), 2, tipLength=0.3)


def draw_gaze_indicator(img, x_norm, y_norm, panel_x=10, panel_y=10, size=120):
    """Desenha um quadrado mostrando onde o gaze está normalizado."""
    x2, y2 = panel_x + size, panel_y + size
    cv2.rectangle(img, (panel_x, panel_y), (x2, y2), (60,60,60), -1)
    cv2.rectangle(img, (panel_x, panel_y), (x2, y2), (180,180,180), 1)

    # Linhas de grade
    mid_x = panel_x + size//2
    mid_y = panel_y + size//2
    cv2.line(img, (mid_x, panel_y), (mid_x, y2), (80,80,80), 1)
    cv2.line(img, (panel_x, mid_y), (x2, mid_y), (80,80,80), 1)

    # Ponto de gaze
    gx = int(panel_x + x_norm * size)
    gy = int(panel_y + y_norm * size)
    gx = max(panel_x+4, min(x2-4, gx))
    gy = max(panel_y+4, min(y2-4, gy))
    cv2.circle(img, (gx, gy), 6, (0,255,128), -1)
    cv2.circle(img, (gx, gy), 6, (255,255,255), 1)

    draw_text(img, "GAZE", (panel_x, panel_y + size + 14),
              scale=0.4, color=(180,180,180), bg=False)


def main():
    print(f"Abrindo câmera índice {CAMERA_INDEX}...")
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

    if not cap.isOpened():
        print("ERRO: não foi possível abrir câmera.")
        sys.exit(1)

    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,
    )
    detector = mp_vision.FaceLandmarker.create_from_options(options)
    print("Detector pronto. Pressione Q para sair, S para screenshot.\n")

    ref_nose = [None]
    start = time.time()
    screenshot_n = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        h, w = frame.shape[:2]
        display = frame.copy()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.time() - start) * 1000)

        result = detector.detect_for_video(mp_image, timestamp_ms)

        if not result.face_landmarks:
            draw_text(display, "Rosto nao detectado", (10, h-10),
                      color=(0,0,255), scale=0.6)
            cv2.imshow("Glaze Debug — Q=sair  S=screenshot", display)
            if cv2.waitKey(1) & 0xFF in (ord('q'), ord('Q')):
                break
            continue

        lm = result.face_landmarks[0]
        R = _compute_head_rotation(lm, w, h, ref_nose)
        gaze_dir, head_fwd, l_iris, r_iris, l_eye, r_eye = _compute_gaze(lm, R, w, h)

        # Ângulos e normas
        yaw_rad   = math.atan2(gaze_dir[0], -gaze_dir[2])
        pitch_rad = math.atan2(gaze_dir[1], -gaze_dir[2])
        yaw_deg   = math.degrees(yaw_rad)
        pitch_deg = math.degrees(pitch_rad)

        x_norm = max(0.0, min(1.0, 0.5 + yaw_rad   / math.radians(60)))
        y_norm = max(0.0, min(1.0, 0.5 + pitch_rad  / math.radians(40)))

        # ── Desenho ──────────────────────────────────────────────────────────

        # Íris
        l_center = draw_iris(display, lm, LEFT_IRIS,  w, h, (0,255,128))
        r_center = draw_iris(display, lm, RIGHT_IRIS, w, h, (0,255,128))

        # Centros dos olhos
        for c in [tuple(l_eye.astype(int)), tuple(r_eye.astype(int))]:
            cv2.circle(display, c, 3, (255,200,0), -1)

        # Seta de gaze em cada olho
        eye_mid = ((l_center + r_center) / 2).astype(int)
        draw_gaze_arrow(display, l_center, gaze_dir, length=55, color=(0,255,128))
        draw_gaze_arrow(display, r_center, gaze_dir, length=55, color=(0,255,128))

        # Nariz (landmark 4)
        nose_px = (lm[4].x * w, lm[4].y * h)
        cv2.circle(display, (int(nose_px[0]), int(nose_px[1])), 4, (0,128,255), -1)

        # Eixos da cabeça no nariz
        draw_head_axes(display, R, nose_px, w, h, scale=50)

        # Head-forward direction incluído em draw_head_axes como seta laranja

        # Indicador de gaze normalizado (canto superior direito)
        draw_gaze_indicator(display, x_norm, y_norm,
                            panel_x=w-135, panel_y=10, size=120)

        # HUD de texto
        lines = [
            f"yaw:   {yaw_deg:+7.1f} deg  x_norm: {x_norm:.3f}",
            f"pitch: {pitch_deg:+7.1f} deg  y_norm: {y_norm:.3f}",
            f"head_fwd: ({head_fwd[0]:+.2f}, {head_fwd[1]:+.2f}, {head_fwd[2]:+.2f})",
            f"R_Z col:  ({R[0,2]:+.2f}, {R[1,2]:+.2f}, {R[2,2]:+.2f})",
        ]
        for i, line in enumerate(lines):
            draw_text(display, line, (10, h - 10 - (len(lines)-1-i)*20),
                      scale=0.48, color=(220,220,220))

        # Legenda dos eixos
        draw_text(display, "X(cab)", (10, h-95), scale=0.38, color=(0,0,255), bg=False)
        draw_text(display, "Y(cab)", (10, h-80), scale=0.38, color=(0,200,0), bg=False)
        draw_text(display, "Z(-fwd)", (10, h-65), scale=0.38, color=(255,128,0), bg=False)

        cv2.imshow("Glaze Debug — Q=sair  S=screenshot", display)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q')):
            break
        if key in (ord('s'), ord('S')):
            fname = f"debug_screenshot_{screenshot_n}.png"
            cv2.imwrite(fname, display)
            print(f"Screenshot salvo: {fname}")
            screenshot_n += 1

    cap.release()
    detector.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
