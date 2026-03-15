# list_cameras.py — lista câmeras disponíveis no sistema
# Uso: python list_cameras.py
import cv2

print("Procurando câmeras disponíveis...\n")
found = []
for i in range(10):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        ret, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        name = "desconhecida"
        # Tenta ler nome via backend
        try:
            name = cap.getBackendName()
        except Exception:
            pass
        status = "OK (imagem)" if ret else "abre mas sem imagem"
        print(f"  Índice {i}: {w}x{h} — backend={name} — {status}")
        found.append(i)
        cap.release()

if not found:
    print("  Nenhuma câmera encontrada.")
else:
    print(f"\nTotal: {len(found)} câmera(s) encontrada(s).")
    print(f"Para usar uma câmera específica, edite CAMERA_INDEX em config.py")
