"""
Diagnóstico de câmera e MediaPipe — rode com:
    python diag_camera.py

Testa:
  1. Se a câmera abre e lê frames
  2. Se o MediaPipe detecta landmarks/face_matrix no frame ao vivo
  3. Exibe gaze normalizado em tempo real (q para sair)
"""
import cv2
import time
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

CAMERA_INDEX = 0
MODEL_PATH = "face_landmarker.task"

def main():
    print(f"[DIAG] Abrindo câmera index={CAMERA_INDEX}...")
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        print("[ERRO] Câmera não abriu. Tente mudar CAMERA_INDEX em config.py.")
        return

    ret, frame = cap.read()
    if not ret or frame is None:
        print("[ERRO] Câmera abriu mas não retornou frames.")
        cap.release()
        return

    h, w = frame.shape[:2]
    print(f"[OK] Câmera aberta — resolução real: {w}x{h}")

    print(f"[DIAG] Carregando modelo MediaPipe: {MODEL_PATH}")
    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.3,
        min_face_presence_confidence=0.3,
        min_tracking_confidence=0.3,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=True,
    )
    detector = mp_vision.FaceLandmarker.create_from_options(options)
    print("[OK] Modelo carregado. Pressione Q para sair.")
    print()

    t_start = time.time()
    frame_count = 0
    detected_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERRO] Falha ao ler frame.")
            break

        frame_count += 1
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.time() - t_start) * 1000)

        result = detector.detect_for_video(mp_image, timestamp_ms)

        has_face   = bool(result.face_landmarks)
        has_matrix = bool(result.facial_transformation_matrixes)

        if has_face and has_matrix:
            detected_count += 1
            lm = result.face_landmarks[0]
            mat = np.array(result.facial_transformation_matrixes[0].data).reshape(4, 4)
            R = mat[:3, :3]
            import math
            fwd = R[:, 2] / (np.linalg.norm(R[:, 2]) + 1e-9)
            yaw   = math.atan2(fwd[0], fwd[2])
            pitch = math.atan2(-fwd[1], fwd[2])
            x_norm = max(0.0, min(1.0, 0.5 + yaw   / math.radians(90)))
            y_norm = max(0.0, min(1.0, 0.5 + pitch  / math.radians(70)))

            status = f"DETECTADO | gaze=({x_norm:.3f}, {y_norm:.3f}) | yaw={math.degrees(yaw):.1f}° pitch={math.degrees(pitch):.1f}°"
            color = (0, 255, 0)
        else:
            status = f"SEM ROSTO (landmarks={has_face}, matrix={has_matrix})"
            color = (0, 0, 255)

        # Overlay no frame
        cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        pct = detected_count / frame_count * 100 if frame_count else 0
        cv2.putText(frame, f"frames={frame_count} detecoes={detected_count} ({pct:.0f}%)",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        cv2.imshow("Glaze Diag — Q para sair", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    detector.close()
    print(f"\n[DIAG] Resultado: {detected_count}/{frame_count} frames com rosto detectado ({detected_count/frame_count*100:.1f}%)" if frame_count else "")

if __name__ == "__main__":
    main()
