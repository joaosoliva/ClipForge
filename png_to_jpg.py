import argparse
import os
import warnings
from typing import Optional, Iterable, Tuple

from PIL import Image, UnidentifiedImageError

DEFAULT_ROOT = "batches"


def _iter_batches(root: str, job: Optional[str] = None) -> Iterable[Tuple[str, str]]:
    if job:
        batch_dir = os.path.join(root, job)
        if os.path.isdir(batch_dir):
            yield job, batch_dir
        return

    for entry in sorted(os.listdir(root)):
        batch_dir = os.path.join(root, entry)
        if entry.isdigit() and os.path.isdir(batch_dir):
            yield entry, batch_dir


def convert_pngs_in_batches(root: str, job: Optional[str] = None) -> int:
    converted = 0

    for job_id, batch_dir in _iter_batches(root, job):
        images_dir = os.path.join(batch_dir, "imagens")
        if not os.path.isdir(images_dir):
            continue

        batch_converted = 0
        for filename in os.listdir(images_dir):
            if not filename.lower().endswith(".png"):
                continue

            png_path = os.path.join(images_dir, filename)
            jpg_path = os.path.join(images_dir, os.path.splitext(filename)[0] + ".jpg")

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    with Image.open(png_path) as img:
                        img.convert("RGB").save(jpg_path, "JPEG", quality=95)
            except (UnidentifiedImageError, OSError) as exc:
                print(f"[WARN] Batch {job_id}: falha ao converter {filename}: {exc}")
                continue

            os.remove(png_path)
            converted += 1
            batch_converted += 1

        if batch_converted:
            print(f"[INFO] Batch {job_id}: {batch_converted} PNG(s) convertidos para JPG.")

    return converted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Pasta raiz com pastas 01,02,03...")
    parser.add_argument("--job", help="Converte apenas um job (ex: 01). Se omitido, converte todos.")
    args = parser.parse_args()

    total = convert_pngs_in_batches(args.root, args.job)
    print(f"[OK] Conversão finalizada. Total PNG(s) convertidos: {total}.")


if __name__ == "__main__":
    main()
