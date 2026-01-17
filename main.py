import os
import re
import json
import argparse
import subprocess
import sys
import math
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any

import pysrt
from unidecode import unidecode

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
# CONFIG GLOBAL
# =============================================================================

AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".aac", ".flac"]
VALID_EXTS = (".jpg", ".jpeg", ".png", ".gif")

OUT_W = 1920
OUT_H = 1080
FPS = 25
BG_COLOR = "white"

SAFE_H = 864

ZOOM_START = 1.0
ZOOM_END = 1.06
SLIDE_DURATION = 0.8

END_PAD_SECONDS = 0.25  # estende o último clip (não mexe em loop)

TEXT_SIZE = 52
TEXT_COLOR = "black"

STICKMAN_DIR = "input/stickman"
STICKMAN_DEFAULT = "neutral"

STICKMAN_SCALE = 0.6
STICKMAN_SIZE = int(1024 * STICKMAN_SCALE)
STICKMAN_MARGIN_X = 25

STICKMAN_TEXT_SIZE = 36
STICKMAN_TEXT_COLOR = "black"
STICKMAN_TEXT_MARGIN = 7

DISABLE_ZOOM_ON_GIFS = True

RESERVED_LEFT = STICKMAN_MARGIN_X + STICKMAN_SIZE + 40  # respiro extra
RIGHT_MARGIN = 100

# Área padrão quando stickman está ligado
CONTENT_W = OUT_W - RESERVED_LEFT - RIGHT_MARGIN
CONTENT_H = SAFE_H

# Fonte usada no drawtext (Windows). Ajuste se necessário.
FONTFILE = "/Windows/Fonts/comic.ttf"

# Novo: arquivo intermediário (não destrói o SRT original)
SRT_EDIT_FILENAME = "srt_edit.json"

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

def is_gif(path: str) -> bool:
    return path.lower().endswith(".gif")

def escape_text_for_ffmpeg(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "\\%")
    )

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
        image = None
        if mode in ["image-only", "image-with-text"]:
            image = find_image_by_id(images_dir, item["image_id"])
            if not image:
                print_safe(f"[WARN] Mode '{mode}' requer imagem, mas ID '{item['image_id']}' não encontrada")
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
            "image": image,
            "start": matched_sub.start.ordinal / 1000.0,
            "text": item.get("text"),
            "mode": mode,
            "zoom_enabled": item.get("effects", {}).get("zoom", False),
            "slide_direction": item.get("effects", {}).get("slide"),
            "stickman_cfg": stickman_cfg
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

# =============================================================================
# VIDEO
# =============================================================================

def _compute_layout(use_stickman: bool) -> Tuple[int, int, str]:
    """
    Returns target_w, target_h, final_x expression (x position for image overlay).
    When stickman is OFF -> images centered and can use full width.
    """
    if use_stickman:
        target_w = CONTENT_W
        target_h = CONTENT_H
        final_x = f"{RESERVED_LEFT}+({CONTENT_W}-w)/2"
    else:
        target_w = OUT_W
        target_h = SAFE_H
        final_x = "(W-w)/2"
    return target_w, target_h, final_x

def create_clip(
    image_path: Optional[str],
    duration: float,
    zoom_enabled: bool,
    slide_direction: Optional[str],
    text: Optional[str],
    stickman_cfg: Optional[Dict[str, str]],
    out: str,
    use_stickman: bool
):
    if use_stickman:
        if not stickman_cfg or not stickman_cfg.get("path"):
            raise RuntimeError(f"Stickman não encontrado para clip {out}")

    total_frames = max(1, int(math.ceil(duration * FPS)))

    inputs: List[str] = []

    # ---------- IMAGE / GIF ----------
    if image_path:
        if is_gif(image_path):
            inputs += ["-stream_loop", "-1", "-ignore_loop", "0", "-i", image_path]
        else:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", image_path]

    # ---------- BACKGROUND ----------
    inputs += ["-f", "lavfi", "-i", f"color=c={BG_COLOR}:s={OUT_W}x{OUT_H}:r={FPS}:d={duration}"]

    # ---------- STICKMAN ----------
    if use_stickman:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", stickman_cfg["path"]]

    img_i = 0 if image_path else None
    bg_i = 1 if image_path else 0
    stick_i = (bg_i + 1) if use_stickman else None

    filters: List[str] = [f"[{bg_i}:v]format=rgba[bg]"]

    # ---------- IMAGE FILTER ----------
    if image_path:
        target_w, target_h, final_x = _compute_layout(use_stickman)
        final_y = "(H-h)/2"

        isgif = is_gif(image_path)
        allow_zoom = zoom_enabled and not (isgif and DISABLE_ZOOM_ON_GIFS)

        if allow_zoom:
            canvas_w = int(math.ceil(target_w * ZOOM_END))
            canvas_h = int(math.ceil(target_h * ZOOM_END))

            filters += [
                f"[{img_i}:v]setsar=1,format=rgba,scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease[img0]",
                f"color=c={BG_COLOR}:s={canvas_w}x{canvas_h}:r={FPS}:d={duration},format=rgba[can]",
                f"[can][img0]overlay=(W-w)/2:(H-h)/2[pre]",
                f"[pre]zoompan="
                f"z='if(lte(on,1),{ZOOM_START},{ZOOM_START}+({ZOOM_END}-{ZOOM_START})*(on-1)/{total_frames})':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:"
                f"s={target_w}x{target_h}:fps={FPS}[img]"
            ]
        else:
            filters.append(
                f"[{img_i}:v]setsar=1,format=rgba,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"fps={FPS}[img]"
            )

        # Slide opcional (em ambos modos)
        if slide_direction:
            sf = max(1, int(SLIDE_DURATION * FPS))
            if slide_direction == "left":
                x_expr = f"if(lt(n,{sf}),W-(W-({final_x}))*n/{sf},({final_x}))"
                y_expr = final_y
            elif slide_direction == "right":
                x_expr = f"if(lt(n,{sf}),-w+({final_x}+w)*n/{sf},({final_x}))"
                y_expr = final_y
            elif slide_direction == "up":
                x_expr = final_x
                y_expr = f"if(lt(n,{sf}),H-(H-({final_y}))*n/{sf},({final_y}))"
            elif slide_direction == "down":
                x_expr = final_x
                y_expr = f"if(lt(n,{sf}),-h+({final_y}+h)*n/{sf},({final_y}))"
            else:
                x_expr = final_x
                y_expr = final_y
        else:
            x_expr = final_x
            y_expr = final_y

        filters.append(f"[bg][img]overlay=x='{x_expr}':y='{y_expr}':shortest=1[v]")
        cur = "[v]"
    else:
        cur = "[bg]"

    # ---------- STICKMAN OVERLAY ----------
    if use_stickman:
        filters.append(
            f"[{stick_i}:v]setsar=1,format=rgba,scale={STICKMAN_SIZE}:{STICKMAN_SIZE}[stick]"
        )
        filters.append(
            f"{cur}[stick]overlay=x={STICKMAN_MARGIN_X}:y='(H-h)/2':shortest=1[v2]"
        )
        cur = "[v2]"

    # ---------- MAIN TEXT ----------
    if text:
        t = escape_text_for_ffmpeg(text)
        filters.append(
            f"{cur}drawtext=fontfile={FONTFILE}:"
            f"text='{t}':fontsize={TEXT_SIZE}:fontcolor={TEXT_COLOR}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2[v3]"
        )
        cur = "[v3]"

    # ---------- STICKMAN SPEECH ----------
    if use_stickman and stickman_cfg and stickman_cfg.get("speech"):
        sp = escape_text_for_ffmpeg(stickman_cfg["speech"])
        filters.append(
            f"{cur}drawtext=fontfile={FONTFILE}:"
            f"text='{sp}':fontsize={STICKMAN_TEXT_SIZE}:fontcolor={STICKMAN_TEXT_COLOR}:"
            f"x={STICKMAN_MARGIN_X}+{STICKMAN_SIZE}/2-(text_w/2):"
            f"y='(H-{STICKMAN_SIZE})/2-text_h-{STICKMAN_TEXT_MARGIN}'[vfinal]"
        )
        cur = "[vfinal]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", cur,
        "-frames:v", str(total_frames),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        out
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "FFmpeg falhou.\n"
            f"Arquivo: {out}\n"
            f"Comando: {' '.join(cmd[:20])} ...\n"
            f"Stderr:\n{r.stderr}"
        )

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

        create_clip(
            image_path=item["image"],
            duration=item["duration"],
            zoom_enabled=item["zoom_enabled"],
            slide_direction=item["slide_direction"],
            text=item["text"],
            stickman_cfg=item["stickman_cfg"],
            out=out_clip,
            use_stickman=use_stickman
        )
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
