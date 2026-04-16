"""
Génère les icônes PWA pour le dashboard Android.
Lance-le une seule fois :
    python generate_icons.py

Crée : static/icons/icon-192.png et static/icons/icon-512.png
Ne nécessite que Pillow (pip install pillow).
"""
import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installation de Pillow...")
    os.system(f"{sys.executable} -m pip install pillow -q")
    from PIL import Image, ImageDraw, ImageFont

ICONS_DIR = Path(__file__).parent / "static" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)


def create_icon(size: int) -> None:
    """Génère une icône carrée de `size`×`size` pixels."""
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fond dégradé simulé : rectangle arrondi vert foncé
    margin = size // 10
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=size // 6,
        fill=(22, 27, 34, 255),      # #161b22
        outline=(63, 185, 80, 255),  # #3fb950
        width=max(2, size // 60),
    )

    # Symbole graphique : courbe haussière simplifiée
    cx, cy  = size // 2, size // 2
    scale   = size / 192

    # Ligne de tendance haussière
    points = [
        (cx - int(70*scale), cy + int(30*scale)),
        (cx - int(40*scale), cy + int(10*scale)),
        (cx - int(10*scale), cy + int(20*scale)),
        (cx + int(20*scale), cy - int(15*scale)),
        (cx + int(50*scale), cy - int(40*scale)),
        (cx + int(70*scale), cy - int(55*scale)),
    ]
    draw.line(points, fill=(63, 185, 80, 255), width=max(3, int(6*scale)), joint="curve")

    # Flèche vers le haut à droite
    tip_x = cx + int(70*scale)
    tip_y = cy - int(55*scale)
    arr   = max(4, int(10*scale))
    draw.polygon([
        (tip_x, tip_y),
        (tip_x - arr, tip_y + arr),
        (tip_x + arr, tip_y + arr),
    ], fill=(63, 185, 80, 255))

    # Texte "BOT"
    try:
        font_size = max(18, size // 10)
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = "BOT"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw   = bbox[2] - bbox[0]
    th   = bbox[3] - bbox[1]
    draw.text(
        (cx - tw // 2, cy + int(35*scale)),
        text, font=font, fill=(88, 166, 255, 255)
    )

    path = ICONS_DIR / f"icon-{size}.png"
    img.save(path, "PNG")
    print(f"  Créé: {path}")


if __name__ == "__main__":
    print("Génération des icônes PWA...")
    create_icon(192)
    create_icon(512)
    print("Icônes générées.")
