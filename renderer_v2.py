import math
import re
import subprocess
from typing import List

from clip_specs import BlurEntrySpec, ClipSpec, ImageLayer, StickmanLayer
from timeline_expressions import build_piecewise_expr
from config import (
    BG_COLOR,
    DISABLE_ZOOM_ON_GIFS,
    FONTFILE,
    FPS,
    SLIDE_DURATION,
    STICKMAN_MARGIN_X,
    STICKMAN_SIZE,
    STICKMAN_TEXT_COLOR,
    STICKMAN_TEXT_MARGIN,
    STICKMAN_TEXT_SIZE,
    TEXT_COLOR,
    TEXT_IMAGE_MARGIN,
    TEXT_SIZE,
    ZOOM_END,
    ZOOM_START,
)
from layouts import resolve_layout
from stickman_animations import build_stickman_animation


def _is_gif(path: str) -> bool:
    return path.lower().endswith(".gif")


def _escape_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )

def _quote_expr(expr: str) -> str:
    return f"'{expr}'"

def _replace_expr_vars(expr: str, width: int, height: int) -> str:
    expr = re.sub(r"\bw\b", str(width), expr)
    expr = re.sub(r"\bh\b", str(height), expr)
    return expr

def _scaled_image_size(path: str, target_w: int, target_h: int, zoom_enabled: bool) -> tuple[int, int]:
    if zoom_enabled:
        return target_w, target_h
    safe_path = path.replace("'", "\\'")
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"movie='{safe_path}',scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return target_w, target_h
    parts = result.stdout.strip().split("x")
    if len(parts) != 2:
        return target_w, target_h
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return target_w, target_h


def _build_entry_blur(
    blur: BlurEntrySpec,
    input_label: str,
    duration: float,
    fps: int,
) -> tuple[List[str], str]:
    filters: List[str] = []
    blur_duration = blur.duration if blur.duration is not None else SLIDE_DURATION
    if blur_duration <= 0:
        return filters, input_label
    blur_duration = min(blur_duration, duration)
    enable_expr = f"between(t,0,{blur_duration})"
    method = (blur.method or "tblend").strip().lower()
    output_label = "img_blur"
    if method == "boxblur":
        radius = blur.strength if blur.strength is not None else 4.0
        filters.append(
            f"{input_label}boxblur=luma_radius={radius}:luma_power=1:"
            f"enable='{enable_expr}'[{output_label}]"
        )
    elif method == "tmix":
        frames = 3
        if blur.strength is not None:
            frames = max(2, int(round(blur.strength)))
        filters.append(
            f"{input_label}tmix=frames={frames}:enable='{enable_expr}'[{output_label}]"
        )
    else:
        opacity = blur.strength if blur.strength is not None else 0.7
        opacity = max(0.05, min(opacity, 1.0))
        filters.append(
            f"{input_label}tblend=all_mode=average:all_opacity={opacity}:"
            f"enable='{enable_expr}'[{output_label}]"
        )
    return filters, f"[{output_label}]"


def _build_image_filter(
    image: ImageLayer,
    input_label: str,
    duration: float,
    total_frames: int,
    target_w: int,
    target_h: int,
    fps: int,
) -> tuple[List[str], str]:
    isgif = _is_gif(image.path)
    allow_zoom = image.zoom_enabled and not (isgif and DISABLE_ZOOM_ON_GIFS)
    filters: List[str] = []

    if allow_zoom:
        canvas_w = int(math.ceil(target_w * ZOOM_END))
        canvas_h = int(math.ceil(target_h * ZOOM_END))
        zoom_den = max(1, total_frames - 1)
        filters += [
            f"{input_label}setsar=1,format=rgba,"
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease[img0]",
            f"color=c={BG_COLOR}:s={canvas_w}x{canvas_h}:r={fps}:d={duration},format=rgba[can]",
            f"[can][img0]overlay=(W-w)/2:(H-h)/2[pre]",
            f"[pre]zoompan="
            f"z='{ZOOM_START}+({ZOOM_END}-{ZOOM_START})*on/{zoom_den}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:"
            f"s={target_w}x{target_h}:fps={fps}[img]",
        ]
    else:
        filters.append(
            f"{input_label}setsar=1,format=rgba,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"fps={fps}[img]"
        )

    output_label = "[img]"
    if image.slide_direction and image.blur_entry and image.blur_entry.enabled:
        blur_filters, output_label = _build_entry_blur(
            blur=image.blur_entry,
            input_label=output_label,
            duration=duration,
            fps=fps,
        )
        filters.extend(blur_filters)

    return filters, output_label


def _apply_slide(final_x: str, final_y: str, slide_direction: str, fps: int) -> List[str]:
    sf = max(1, int(SLIDE_DURATION * fps))
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
    return [x_expr, y_expr]

def _apply_slide_text(final_x: str, final_y: str, slide_direction: str, fps: int) -> List[str]:
    sf = max(1, int(SLIDE_DURATION * fps))
    if slide_direction == "left":
        x_expr = f"if(lt(n,{sf}),W-(W-({final_x}))*n/{sf},({final_x}))"
        y_expr = final_y
    elif slide_direction == "right":
        x_expr = f"if(lt(n,{sf}),-text_w+({final_x}+text_w)*n/{sf},({final_x}))"
        y_expr = final_y
    elif slide_direction == "up":
        x_expr = final_x
        y_expr = f"if(lt(n,{sf}),H-(H-({final_y}))*n/{sf},({final_y}))"
    elif slide_direction == "down":
        x_expr = final_x
        y_expr = f"if(lt(n,{sf}),-text_h+({final_y}+text_h)*n/{sf},({final_y}))"
    else:
        x_expr = final_x
        y_expr = final_y
    return [x_expr, y_expr]


def render_clip(spec: ClipSpec, out: str) -> List[str]:
    warnings: List[str] = []
    total_frames = max(1, int(math.ceil(spec.duration * spec.fps)))
    text_anchor = (spec.text_anchor or "").strip().lower()
    text_anchor_slot = spec.text_anchor_slot if spec.text_anchor_slot is not None else 0
    if spec.text_margin is None:
        text_margin = TEXT_IMAGE_MARGIN
    else:
        try:
            text_margin = int(spec.text_margin)
        except (TypeError, ValueError):
            text_margin = TEXT_IMAGE_MARGIN
    text_applied_to_anchor = False
    anchored_text_exprs = None

    layout, layout_warnings = resolve_layout(
        spec.layout,
        use_stickman=spec.stickman is not None,
        image_count=len(spec.images),
        stickman_side=spec.stickman_position,
    )
    warnings.extend(layout_warnings)

    inputs: List[str] = []

    for image in spec.images:
        if _is_gif(image.path):
            inputs += ["-stream_loop", "-1", "-ignore_loop", "0", "-i", image.path]
        else:
            inputs += ["-loop", "1", "-framerate", str(spec.fps), "-i", image.path]

    inputs += [
        "-f",
        "lavfi",
        "-i",
        f"color=c={BG_COLOR}:s={spec.width}x{spec.height}:r={spec.fps}:d={spec.duration}",
    ]

    if spec.stickman:
        inputs += ["-loop", "1", "-framerate", str(spec.fps), "-i", spec.stickman.path]

    bg_i = len(spec.images)
    stick_i = bg_i + 1 if spec.stickman else None

    filters: List[str] = [f"[{bg_i}:v]format=rgba[bg]"]
    cur = "[bg]"

    for idx, image in enumerate(spec.images):
        if idx >= len(layout.image_slots):
            warnings.append(
                f"Imagem extra ignorada no layout {layout.name}: {image.path}"
            )
            continue

        slot = layout.image_slots[idx]
        image_filters, image_label = _build_image_filter(
            image=image,
            input_label=f"[{idx}:v]",
            duration=spec.duration,
            total_frames=total_frames,
            target_w=slot.target_w,
            target_h=slot.target_h,
            fps=spec.fps,
        )
        filters += image_filters

        base_final_x = slot.x_expr
        base_final_y = slot.y_expr
        final_x = base_final_x
        final_y = base_final_y
        if image.keyframes:
            final_x = build_piecewise_expr(image.keyframes, "x", base_final_x)
            final_y = build_piecewise_expr(image.keyframes, "y", base_final_y)
        elif image.slide_direction:
            final_x, final_y = _apply_slide(final_x, final_y, image.slide_direction, spec.fps)

        if spec.text and idx == text_anchor_slot and text_anchor in {"top", "bottom"}:
            scaled_w, scaled_h = _scaled_image_size(
                image.path, slot.target_w, slot.target_h, image.zoom_enabled
            )
            base_final_x_expr = _replace_expr_vars(base_final_x, scaled_w, scaled_h)
            base_final_y_expr = _replace_expr_vars(base_final_y, scaled_w, scaled_h)
            text_x = f"{base_final_x_expr}+({scaled_w}-text_w)/2"
            if text_anchor == "top":
                text_y = f"{base_final_y_expr}-text_h-{text_margin}"
            else:
                text_y = f"{base_final_y_expr}+{scaled_h}+{text_margin}"
                text_y = f"min({text_y},H-text_h-{TEXT_IMAGE_MARGIN})"
            if image.slide_direction:
                text_x, text_y = _apply_slide_text(text_x, text_y, image.slide_direction, spec.fps)
            anchored_text_exprs = (text_x, text_y)

        filters.append(
            f"{cur}{image_label}overlay=x={_quote_expr(final_x)}:y={_quote_expr(final_y)}:shortest=1[v{idx}]"
        )
        cur = f"[v{idx}]"

    if spec.text and anchored_text_exprs is not None:
        text = _escape_text(spec.text)
        text_x, text_y = anchored_text_exprs
        filters.append(
            f"{cur}drawtext=fontfile={FONTFILE}:"
            f"text='{text}':fontsize={TEXT_SIZE}:fontcolor={TEXT_COLOR}:"
            f"x={_quote_expr(text_x)}:y={_quote_expr(text_y)}[vtext]"
        )
        cur = "[vtext]"
        text_applied_to_anchor = True

    if spec.stickman and layout.stickman_pos and stick_i is not None:
        stickman = spec.stickman
        stickman_x, stickman_y = layout.stickman_pos
        anim_x, anim_y, scale_expr = build_stickman_animation(
            stickman.anim, total_frames, stickman_x, stickman_y
        )
        filters.append(
            f"[{stick_i}:v]setsar=1,format=rgba,"
            f"scale='{STICKMAN_SIZE}*({scale_expr})':'{STICKMAN_SIZE}*({scale_expr})':eval=frame"
            f"[stick]"
        )
        filters.append(
            f"{cur}[stick]overlay=x={_quote_expr(anim_x)}:y={_quote_expr(anim_y)}:shortest=1[vstick]"
        )
        cur = "[vstick]"

    if spec.text and not text_applied_to_anchor:
        text = _escape_text(spec.text)
        filters.append(
            f"{cur}drawtext=fontfile={FONTFILE}:"
            f"text='{text}':fontsize={TEXT_SIZE}:fontcolor={TEXT_COLOR}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2[vtext]"
        )
        cur = "[vtext]"

    if spec.stickman and spec.stickman.speech and layout.stickman_pos:
        stickman_x, _stickman_y = layout.stickman_pos
        speech = _escape_text(spec.stickman.speech)
        filters.append(
            f"{cur}drawtext=fontfile={FONTFILE}:"
            f"text='{speech}':fontsize={STICKMAN_TEXT_SIZE}:fontcolor={STICKMAN_TEXT_COLOR}:"
            f"x={stickman_x}+{STICKMAN_SIZE}/2-(text_w/2):"
            f"y='(H-{STICKMAN_SIZE})/2-text_h-{STICKMAN_TEXT_MARGIN}'[vspeech]"
        )
        cur = "[vspeech]"

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        cur,
        "-frames:v",
        str(total_frames),
        "-t",
        str(spec.duration),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        out,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "FFmpeg falhou.\n"
            f"Arquivo: {out}\n"
            f"Comando: {' '.join(cmd[:20])} ...\n"
            f"Stderr:\n{r.stderr}"
        )

    return warnings
