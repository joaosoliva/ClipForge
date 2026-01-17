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

def build_job_paths(root: str, job_id: str, use_stickman: bool) -> JobPaths:
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

    out = os.path.join("output", job_id, "clips")
    os.makedirs(out, exist_ok=True)

    return JobPaths(
        job_id=job_id,
        base=base,
        guide=guide,
        stickman_json=stickman_json if use_stickman else None,
        images_dir=images_dir,
        audio=audio,
        srt=srt,
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
    use_stickman: bool
) -> List[Dict[str, Any]]:
    timeline = []

    for item in guide:
        trigger = norm(item["trigger"])
        
        # Mode: "text-only", "image-only" (default), "image-with-text"
        mode = item.get("mode", "image-only").lower()
        mode = mode.replace("_", "-")
        
        # Validação de imagem baseada no mode
        images: List[str] = []
        if mode in ["image-only", "image-with-text"]:
            image_ids = item.get("image_ids")
            if image_ids is None:
                image_id = item.get("image_id")
                image_ids = [image_id] if image_id else []
            if not image_ids:
                print_safe(f"[WARN] Mode '{mode}' requer imagem, mas não há image_id(s)")
                continue
            for image_id in image_ids:
                image = find_image_by_id(images_dir, image_id)
                if not image:
                    print_safe(f"[WARN] Image ID '{image_id}' não encontrada")
                    continue
                images.append(image)
            if not images:
                print_safe(f"[WARN] Mode '{mode}' requer imagem, mas nenhuma foi encontrada")
                continue
        
        matched_sub = None
        for sub in subs:
            if trigger_in_text(trigger, sub.text):
                matched_sub = sub
                break

        if not matched_sub:
            print_safe(f"[WARN] Trigger '{trigger}' não encontrado no SRT efetivo")
            continue

        stickman_cfg = None
        if use_stickman:
            stickman_cfg = find_stickman_for_trigger(trigger, stickman_guide, subs)

        timeline.append({
            "trigger": trigger,
            "images": images,
            "start": matched_sub.start.ordinal / 1000.0,
            "text": item.get("text"),
            "mode": mode,
            "zoom_enabled": item.get("effects", {}).get("zoom", False),
            "slide_direction": item.get("effects", {}).get("slide"),
            "stickman_cfg": stickman_cfg,
            "layout": item.get("layout", "legacy_single"),
            "stickman_anim": item.get("stickman_anim"),
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

def process_job(paths: JobPaths, use_stickman: bool):
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
        use_stickman=use_stickman
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
                path=image_path,
                zoom_enabled=item["zoom_enabled"],
                slide_direction=item["slide_direction"],
            )
            for image_path in item["images"]
        ]

        stickman_layer = None
        layout_result, layout_warnings = resolve_layout(
            item["layout"], use_stickman=use_stickman, image_count=len(item["images"])
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
            images=images,
            stickman=stickman_layer,
            text=item["text"],
        )

        warnings = render_clip(clip_spec, out_clip)
        for warning in warnings:
            print_safe(f"[WARN] {warning}")
        rendered.append(out_clip)

    final_video = os.path.join("output", paths.job_id, f"video_final_{paths.job_id}.mp4")
    print_safe(f"[{total}/{total} | 100%] Concatenando clips + audio")
    concat_job_clips(paths, rendered, final_video)

    print_safe(f"OK: Job {paths.job_id} finalizado -> {final_video}")

# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="batches", help="Pasta raiz com pastas 01,02,03...")
    parser.add_argument("--job", help="Renderiza apenas um job (ex: 01). Se omitido, renderiza todos.")
    parser.add_argument("--no-stickman", action="store_true",
                        help="Renderiza sem stickman (somente imagens centralizadas).")
    args = parser.parse_args()

    use_stickman = (not args.no_stickman)

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

    os.makedirs("output", exist_ok=True)

    for job_id in jobs:
        paths = build_job_paths(args.root, job_id, use_stickman=use_stickman)
        process_job(paths, use_stickman=use_stickman)

if __name__ == "__main__":
    main()
