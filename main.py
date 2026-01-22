import os
import re
import json
import argparse
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import pysrt
from unidecode import unidecode

from clip_specs import ClipSpec, ImageLayer, StickmanAnim, StickmanLayer
from config import (
    AUDIO_EXTENSIONS,
    END_PAD_SECONDS,
    FPS,
    OUT_H,
    OUT_W,
    SRT_EDIT_FILENAME,
    STICKMAN_DEFAULT,
    STICKMAN_DIR,
    VALID_EXTS,
)
from layouts import resolve_layout
from renderer_v2 import render_clip
from png_to_jpg import convert_pngs_in_batches

# =============================================================================
# Console-safe printing (Windows cp1252 friendly)
# =============================================================================

def print_safe(s: str):
    """
    Print that won't crash on Windows consoles with legacy encodings (e.g., cp1252).
    Always flushes, so GUI can read progress in real time.
    """
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "cp1252"
        safe = s.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe, flush=True)

# =============================================================================
# MODELOS
# =============================================================================

@dataclass
class JobPaths:
    job_id: str
    base: str
    guide: str
    stickman_json: Optional[str]
    images_dir: str
    audio: str
    srt: str
    output_root: str
    output_dir: str   # output/<job>/clips

# =============================================================================
# UTILS
# =============================================================================

def norm(text: str) -> str:
    return unidecode(text.lower()).strip()

def trigger_in_text(trigger: str, text: str) -> bool:
    t = norm(trigger)
    s = norm(text)
    if " " in t:
        return t in s
    return re.search(rf"\b{re.escape(t)}\b", s) is not None

def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())

def find_audio_file(folder: str) -> Optional[str]:
    # prioriza "audio.ext" se existir
    for ext in AUDIO_EXTENSIONS:
        p = os.path.join(folder, f"audio{ext}")
        if os.path.exists(p):
            return p
    # senão, primeiro arquivo de áudio encontrado
    for f in os.listdir(folder):
        if f.lower().endswith(tuple(AUDIO_EXTENSIONS)):
            return os.path.join(folder, f)
    return None

def find_srt_file(folder: str) -> Optional[str]:
    # prioriza audio.srt
    preferred = os.path.join(folder, "audio.srt")
    if os.path.exists(preferred):
        return preferred
    for f in os.listdir(folder):
        if f.lower().endswith(".srt"):
            return os.path.join(folder, f)
    return None

def find_image_by_id(images_dir: str, image_id: str) -> Optional[str]:
    for f in os.listdir(images_dir):
        if f.lower().endswith(VALID_EXTS) and re.match(rf"^{re.escape(image_id)}\D", f):
            return os.path.join(images_dir, f)
    return None

def find_stickman_by_name(name: str) -> Optional[str]:
    p = os.path.join(STICKMAN_DIR, f"{name}.png")
    if os.path.exists(p):
        return p
    fallback = os.path.join(STICKMAN_DIR, f"{STICKMAN_DEFAULT}.png")
    return fallback if os.path.exists(fallback) else None

# =============================================================================
# SRT EDIT (merge virtual subs)
# =============================================================================

class _VirtualTime:
    def __init__(self, seconds: float):
        self.ordinal = int(round(seconds * 1000.0))

class _VirtualSub:
    """
    Subtítulo "virtual" compatível com o que o código já espera:
    - .text
    - .start.ordinal (ms)
    - .end.ordinal (ms)
    - .index (int)
    """
    def __init__(self, index: int, start_s: float, end_s: float, text: str):
        self.index = index
        self.start = _VirtualTime(start_s)
        self.end = _VirtualTime(end_s)
        self.text = text

def _load_srt_edits(edit_path: str) -> List[dict]:
    try:
        if os.path.exists(edit_path):
            with open(edit_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def apply_srt_edits(subs: pysrt.SubRipFile, edit_path: str) -> List[Any]:
    """
    Retorna uma lista de subs efetivos:
    - Se houver edição para sub.index -> usa segments (vira vários subs virtuais)
    - Senão -> usa sub original
    """
    edits = _load_srt_edits(edit_path)
    if not edits:
        return list(subs)

    edit_map = {}
    for e in edits:
        try:
            idx = int(e.get("index"))
            edit_map[idx] = e
        except Exception:
            continue

    effective: List[Any] = []
    for sub in subs:
        e = edit_map.get(int(sub.index))
        if not e:
            effective.append(sub)
            continue

        segments = e.get("segments") or []
        if not isinstance(segments, list) or not segments:
            # se edit veio inválido, cai no original
            effective.append(sub)
            continue

        # cria subs virtuais, mantendo o mesmo index (ok para nosso uso)
        for seg in segments:
            try:
                st = float(seg["start"])
                en = float(seg["end"])
                tx = str(seg["text"])
                effective.append(_VirtualSub(int(sub.index), st, en, tx))
            except Exception:
                # se algum segmento falhar, ignora ele
                continue

    # ordena por início
    effective.sort(key=lambda x: x.start.ordinal)
    return effective

# =============================================================================
# DISCOVERY
# =============================================================================

def discover_jobs(root: str) -> List[str]:
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Pasta raiz não encontrada: {root}")

    jobs = []
    for d in sorted(os.listdir(root)):
        full = os.path.join(root, d)
        if os.path.isdir(full) and d.isdigit():
            jobs.append(d)
    return jobs

def build_job_paths(root: str, job_id: str, use_stickman: bool, output_root: str) -> JobPaths:
    base = os.path.join(root, job_id)
    images_dir = os.path.join(base, "imagens")

    guide = os.path.join(base, "guia.json")
    stickman_json = os.path.join(base, "stickman.json")
    audio = find_audio_file(base)
    srt = find_srt_file(base)

    if not os.path.isdir(images_dir):
        raise FileNotFoundError(f"[{job_id}] imagens/ não encontrada em {images_dir}")
    if not os.path.exists(guide):
        raise FileNotFoundError(f"[{job_id}] guia.json não encontrado")
    if use_stickman and not os.path.exists(stickman_json):
        raise FileNotFoundError(f"[{job_id}] stickman.json não encontrado")
    if not audio:
        raise FileNotFoundError(f"[{job_id}] áudio não encontrado (audio.mp3/wav etc.)")
    if not srt:
        raise FileNotFoundError(f"[{job_id}] .srt não encontrado (ex: audio.srt)")

    out = os.path.join(output_root, job_id, "clips")
    os.makedirs(out, exist_ok=True)

    return JobPaths(
        job_id=job_id,
        base=base,
        guide=guide,
        stickman_json=stickman_json if use_stickman else None,
        images_dir=images_dir,
        audio=audio,
        srt=srt,
        output_root=output_root,
        output_dir=out
    )

# =============================================================================
# LOAD / TIMELINE
# =============================================================================

def load_inputs(paths: JobPaths, use_stickman: bool):
    # SRT original
    subs_original = pysrt.open(paths.srt, encoding="utf-8")

    # Se existir srt_edit.json, aplica as edições criando subs "efetivos"
    edit_path = os.path.join(paths.base, SRT_EDIT_FILENAME)
    subs_effective = apply_srt_edits(subs_original, edit_path)

    with open(paths.guide, "r", encoding="utf-8") as f:
        guide = json.load(f)

    stickman_guide = None
    if use_stickman:
        with open(paths.stickman_json, "r", encoding="utf-8") as f:
            stickman_guide = json.load(f)

    return subs_effective, guide, stickman_guide

def find_stickman_for_trigger(trigger: str, stickman_guide, subs) -> Dict[str, str]:
    default_path = find_stickman_by_name(STICKMAN_DEFAULT)
    if not default_path:
        return {"path": "", "speech": ""}

    # Agora "subs" pode ser lista de SubRipItem OU VirtualSub (ambos têm .text)
    for sub in subs:
        if trigger_in_text(trigger, sub.text):
            for item in stickman_guide:
                if norm(item.get("trigger", "")) in norm(sub.text):
                    expr = item.get("expression", STICKMAN_DEFAULT)
                    p = find_stickman_by_name(expr) or default_path
                    return {"path": p, "speech": item.get("speech", "")}

    return {"path": default_path, "speech": ""}

def build_timeline(
    subs,
    guide,
    stickman_guide,
    audio_path: str,
    images_dir: str,
    use_stickman: bool,
    disable_zoom: bool = False
) -> List[Dict[str, Any]]:
    timeline = []

    def _normalize_mode(mode_value: str) -> str:
        mode_value = (mode_value or "image-only").lower().replace("_", "-")
        return mode_value

    def _normalize_layout(layout_value: str) -> str:
        return (layout_value or "legacy_single").strip().lower()

    def _get_item_image_ids(item: Dict[str, Any]) -> List[str]:
        image_ids = item.get("image_ids")
        if isinstance(image_ids, list) and image_ids:
            return [str(i).strip() for i in image_ids if str(i).strip()]
        image_id = str(item.get("image_id", "")).strip()
        return [image_id] if image_id else []

    def _collect_item_images(item: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
        if mode not in ["image-only", "image-with-text"]:
            return []

        image_ids = _get_item_image_ids(item)
        if not image_ids:
            return []

        effects = item.get("effects", {}) if isinstance(item.get("effects"), dict) else {}
        zoom_enabled = False if disable_zoom else effects.get("zoom", False)
        slide_direction = effects.get("slide")

        images: List[Dict[str, Any]] = []
        for image_id in image_ids:
            image = find_image_by_id(images_dir, image_id)
            if not image:
                print_safe(f"[WARN] Image ID '{image_id}' não encontrada")
                continue
            images.append({
                "path": image,
                "zoom_enabled": zoom_enabled,
                "slide_direction": slide_direction,
            })

        return images

    def _build_parent_links():
        parent_links: Dict[int, List[int]] = {}
        i = 0
        while i < len(guide):
            item = guide[i]
            mode = _normalize_mode(item.get("mode", "image-only"))
            layout_norm = _normalize_layout(item.get("layout", "legacy_single"))
            if mode in ["image-only", "image-with-text"] and layout_norm in {
                "two_images_center",
                "stickman_left_3img",
            }:
                required = 2 if layout_norm == "two_images_center" else 3
                image_count = len(_get_item_image_ids(item))
                needed = max(required - image_count, 0)
                children: List[int] = []
                for offset in range(1, needed + 1):
                    child_index = i + offset
                    if child_index >= len(guide):
                        break
                    child_item = guide[child_index]
                    child_layout_norm = _normalize_layout(child_item.get("layout", "legacy_single"))
                    if child_layout_norm not in {"legacy_single", "image_center_only"}:
                        print_safe(
                            "[WARN] Layout complexo não permitido como filho "
                            f"em '{child_item.get('trigger', '')}'. "
                            "Use legacy_single ou image_center_only."
                        )
                        break
                    children.append(child_index)
                if children:
                    parent_links[i] = children
                i += 1 + len(children)
                continue
            i += 1
        return parent_links

    parent_links = _build_parent_links()
    child_layout_overrides: Dict[int, str] = {}
    child_effective_images: Dict[int, List[Dict[str, Any]]] = {}
    child_has_images: Dict[int, bool] = {}
    child_text_anchor_slot: Dict[int, Optional[int]] = {}
    for parent_index, children in parent_links.items():
        parent_item = guide[parent_index]
        parent_mode = _normalize_mode(parent_item.get("mode", "image-only"))
        parent_layout = parent_item.get("layout", "legacy_single")
        parent_base_images = _collect_item_images(parent_item, parent_mode)
        layout_norm = _normalize_layout(parent_layout)
        required = 2 if layout_norm == "two_images_center" else 3
        cumulative_children: List[Dict[str, Any]] = []
        for child_index in children:
            child_item = guide[child_index]
            child_mode = _normalize_mode(child_item.get("mode", "image-only"))
            child_images = _collect_item_images(child_item, child_mode)
            child_has_images[child_index] = bool(child_images)
            child_slot_start = len(parent_base_images) + len(cumulative_children)
            static_base = [
                {
                    **image,
                    "zoom_enabled": False,
                    "slide_direction": None,
                }
                for image in parent_base_images
            ]
            static_previous = [
                {
                    **image,
                    "zoom_enabled": False,
                    "slide_direction": None,
                }
                for image in cumulative_children
            ]
            combined = static_base + static_previous + child_images
            child_effective_images[child_index] = combined[:required]
            child_layout_overrides[child_index] = parent_layout
            if child_images:
                child_text_anchor_slot[child_index] = min(child_slot_start, required - 1)
            else:
                child_text_anchor_slot[child_index] = None
            cumulative_children.extend(child_images)

    for idx, item in enumerate(guide):
        trigger = norm(item["trigger"])

        mode = _normalize_mode(item.get("mode", "image-only"))
        layout_name = item.get("layout", "legacy_single")
        if idx in child_layout_overrides:
            layout_name = child_layout_overrides[idx]
        layout_norm = _normalize_layout(layout_name)

        matched_sub = None
        for sub in subs:
            if trigger_in_text(trigger, sub.text):
                matched_sub = sub
                break

        if not matched_sub:
            print_safe(f"[WARN] Trigger '{trigger}' não encontrado no SRT efetivo")
            continue

        images: List[Dict[str, Any]] = _collect_item_images(item, mode)
        if idx in child_effective_images:
            images = child_effective_images[idx]

        if mode in ["image-only", "image-with-text"] and not images:
            print_safe(f"[WARN] Mode '{mode}' requer imagem, mas não há image_id(s)")
            continue
        if idx in child_layout_overrides and mode in ["image-only", "image-with-text"]:
            if not child_has_images.get(idx, True):
                print_safe(
                    f"[WARN] Item filho '{item.get('trigger', '')}' "
                    "não possui imagem para compor layout múltiplo."
                )

        stickman_cfg = None
        if use_stickman:
            stickman_cfg = find_stickman_for_trigger(trigger, stickman_guide, subs)

        text_anchor = item.get("text_anchor")
        if mode == "image-with-text" and item.get("text") and not text_anchor:
            text_anchor = "bottom"

        timeline.append({
            "trigger": trigger,
            "images": images,
            "start": matched_sub.start.ordinal / 1000.0,
            "text": item.get("text"),
            "text_anchor": text_anchor,
            "text_margin": item.get("text_margin"),
            "text_anchor_slot": child_text_anchor_slot.get(idx),
            "mode": mode,
            "stickman_cfg": stickman_cfg,
            "layout": layout_name,
            "stickman_anim": item.get("stickman_anim"),
            "stickman_position": item.get("stickman_position"),
        })

    timeline.sort(key=lambda x: x["start"])
    audio_duration = get_audio_duration(audio_path)

    for i in range(len(timeline)):
        if i < len(timeline) - 1:
            duration = timeline[i + 1]["start"] - timeline[i]["start"]
        else:
            duration = (audio_duration - timeline[i]["start"]) + END_PAD_SECONDS
        timeline[i]["duration"] = max(duration, 0.5)

    return timeline

def concat_job_clips(paths: JobPaths, clip_paths: List[str], output_video_path: str):
    """
    Concatena os clips do job e adiciona o áudio do próprio job.
    Usa concat demuxer com cwd=paths.output_dir (onde ficam os clips e o concat.txt).
    """
    os.makedirs(os.path.dirname(output_video_path), exist_ok=True)

    concat_file = os.path.join(paths.output_dir, "concat.txt")

    with open(concat_file, "w", encoding="utf-8") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.basename(cp)}'\n")

    audio_abs = os.path.abspath(paths.audio)
    out_abs = os.path.abspath(output_video_path)

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "concat.txt",
        "-i", audio_abs,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        out_abs
    ], check=True, cwd=paths.output_dir)

# =============================================================================
# PROCESS
# =============================================================================

def process_job(paths: JobPaths, use_stickman: bool, disable_zoom: bool, stickman_side: str):
    print_safe(f"\n>> Processando job {paths.job_id}")
    print_safe(f"   Root:  {paths.base}")
    print_safe(f"   Audio: {paths.audio}")
    print_safe(f"   SRT:   {paths.srt}")
    print_safe(f"   SRT_EDIT: {os.path.join(paths.base, SRT_EDIT_FILENAME)} (se existir)")
    print_safe(f"   Mode:  {'stickman' if use_stickman else 'somente-imagens'}")

    subs, guide, stickman_guide = load_inputs(paths, use_stickman=use_stickman)

    timeline = build_timeline(
        subs,
        guide,
        stickman_guide,
        paths.audio,
        paths.images_dir,
        use_stickman=use_stickman,
        disable_zoom=disable_zoom
    )

    if not timeline:
        print_safe("[WARN] Timeline vazia. Pulando job.")
        return

    rendered: List[str] = []
    total = len(timeline)

    for idx, item in enumerate(timeline, start=1):
        pct = int((idx / total) * 100)
        print_safe(f"[{idx}/{total} | {pct}%] Renderizando clip")

        out_clip = os.path.join(paths.output_dir, f"clip_{idx-1:03d}.mp4")

        images = [
            ImageLayer(
                path=image["path"],
                zoom_enabled=image.get("zoom_enabled", False),
                slide_direction=image.get("slide_direction"),
            )
            for image in item["images"]
        ]

        stickman_layer = None
        stickman_position = item.get("stickman_position") or stickman_side
        layout_result, layout_warnings = resolve_layout(
            item["layout"],
            use_stickman=use_stickman,
            image_count=len(item["images"]),
            stickman_side=stickman_position,
        )
        for warning in layout_warnings:
            print_safe(f"[WARN] {warning}")

        if use_stickman:
            if layout_result.stickman_pos is None:
                stickman_layer = None
            else:
                if not item["stickman_cfg"] or not item["stickman_cfg"].get("path"):
                    raise RuntimeError(f"Stickman não encontrado para clip {out_clip}")

                anim_cfg = item.get("stickman_anim")
                anim = None
                if isinstance(anim_cfg, dict) and anim_cfg.get("name"):
                    anim = StickmanAnim(
                        name=str(anim_cfg.get("name")),
                        direction=anim_cfg.get("direction"),
                    )

                stickman_layer = StickmanLayer(
                    path=item["stickman_cfg"]["path"],
                    speech=item["stickman_cfg"].get("speech", ""),
                    anim=anim,
                )

        clip_spec = ClipSpec(
            duration=item["duration"],
            fps=FPS,
            width=OUT_W,
            height=OUT_H,
            layout=item["layout"],
            stickman_position=stickman_position,
            images=images,
            stickman=stickman_layer,
            text=item["text"],
            text_anchor=item.get("text_anchor"),
            text_margin=item.get("text_margin"),
            text_anchor_slot=item.get("text_anchor_slot"),
        )

        warnings = render_clip(clip_spec, out_clip)
        for warning in warnings:
            print_safe(f"[WARN] {warning}")
        rendered.append(out_clip)

    final_video = os.path.join(paths.output_root, paths.job_id, f"video_final_{paths.job_id}.mp4")
    print_safe(f"[{total}/{total} | 100%] Concatenando clips + audio")
    concat_job_clips(paths, rendered, final_video)

    print_safe(f"OK: Job {paths.job_id} finalizado -> {final_video}")

# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="batches", help="Pasta raiz com pastas 01,02,03...")
    parser.add_argument("--output", default="output", help="Pasta base para os arquivos finais")
    parser.add_argument("--job", help="Renderiza apenas um job (ex: 01). Se omitido, renderiza todos.")
    parser.add_argument("--no-stickman", action="store_true",
                        help="Renderiza sem stickman (somente imagens centralizadas).")
    parser.add_argument(
        "--stickman-position",
        choices=["left", "right"],
        default="left",
        help="Posição do stickman quando ativo (left/right).",
    )
    parser.add_argument("--disable-zoom", action="store_true",
                        help="Desabilita o zoom de todas as triggers (útil para testes).")
    parser.add_argument("--convert-png-to-jpg", action="store_true",
                        help="Converte PNGs das pastas batches para JPG antes do render.")
    args = parser.parse_args()

    use_stickman = (not args.no_stickman)
    stickman_side = args.stickman_position

    try:
        jobs = discover_jobs(args.root)
    except Exception as e:
        print_safe(f"[ERRO] {e}")
        return

    if not jobs:
        print_safe("[ERRO] Nenhum job encontrado na pasta raiz.")
        return

    if args.job:
        if args.job not in jobs:
            print_safe(f"[ERRO] Job {args.job} não encontrado em {args.root}. Encontrados: {jobs}")
            return
        jobs = [args.job]

    os.makedirs(args.output, exist_ok=True)

    if args.convert_png_to_jpg:
        print_safe("[INFO] Convertendo PNGs para JPG antes do render...")
        for job_id in jobs:
            convert_pngs_in_batches(args.root, job_id)

    for job_id in jobs:
        paths = build_job_paths(args.root, job_id, use_stickman=use_stickman, output_root=args.output)
        process_job(
            paths,
            use_stickman=use_stickman,
            disable_zoom=args.disable_zoom,
            stickman_side=stickman_side,
        )

if __name__ == "__main__":
    main()
