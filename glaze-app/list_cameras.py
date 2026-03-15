# list_cameras.py — lista câmeras disponíveis no sistema com nomes reais
# Uso: python list_cameras.py
import cv2

# Tenta listar nomes reais via DirectShow (Windows)
def get_camera_names_dshow():
    names = {}
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-PnpDevice -Class Camera | Select-Object -ExpandProperty FriendlyName"],
            capture_output=True, text=True, timeout=5
        )
        raw = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        for i, name in enumerate(raw):
            names[i] = name
    except Exception:
        pass
    return names

# Alternativa via wmic
def get_camera_names_wmic():
    names = {}
    try:
        import subprocess
        result = subprocess.run(
            ["wmic", "path", "Win32_PnPEntity", "where",
             "PNPClass='Camera' or PNPClass='Image'", "get", "Name"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.splitlines()
                 if l.strip() and l.strip() != "Name"]
        for i, name in enumerate(lines):
            names[i] = name
    except Exception:
        pass
    return names

print("Câmeras instaladas no sistema (via PnP):")
pnp_names = get_camera_names_dshow()
if not pnp_names:
    pnp_names = get_camera_names_wmic()

if pnp_names:
    for i, name in pnp_names.items():
        print(f"  [{i}] {name}")
else:
    print("  (não foi possível listar nomes via PnP)")

print("\nTestando abertura via OpenCV (DSHOW):\n")
found = []
for i in range(8):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        ret, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        status = "✓ imagem OK" if ret else "abre mas sem imagem"
        print(f"  CAMERA_INDEX={i} — {w}x{h} — {status}")
        found.append(i)
        cap.release()

print(f"\nTotal: {len(found)} câmera(s) acessível(is) pelo OpenCV.")
print("\nDica: câmera virtual NVIDIA (VCAM) geralmente aparece como índice 0 ou 1.")
print("      Webcam física tende a ser a que aparece sem logs VCAMDS acima.")
print("\nPara definir a câmera: edite CAMERA_INDEX em config.py")
print("Para verificar qual câmera está sendo usada visualmente:")
print("  python preview_camera.py  (cria um preview ao vivo)")
