# preview_camera.py — abre preview ao vivo de uma câmera para identificar qual é
# Uso: python preview_camera.py [indice]
# Exemplo: python preview_camera.py 2
# Pressione Q para fechar
import cv2
import sys

index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

print(f"Abrindo câmera índice {index}... (pressione Q para fechar)")
cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)

if not cap.isOpened():
    print(f"Erro: não foi possível abrir câmera {index}")
    sys.exit(1)

while True:
    ret, frame = cap.read()
    if not ret:
        print("Sem imagem.")
        break
    cv2.imshow(f"Camera {index} — pressione Q para fechar", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
