import os
from PIL import Image

pasta_entrada = "pngs"
pasta_saida = "jpgs"

os.makedirs(pasta_saida, exist_ok=True)

for arquivo in os.listdir(pasta_entrada):
    if arquivo.lower().endswith(".png"):
        caminho_png = os.path.join(pasta_entrada, arquivo)
        caminho_jpg = os.path.join(
            pasta_saida, os.path.splitext(arquivo)[0] + ".jpg"
        )

        img = Image.open(caminho_png).convert("RGB")
        img.save(caminho_jpg, "JPEG", quality=95)