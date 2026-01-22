from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import (
    LEFT_MARGIN,
    OUT_W,
    OUT_H,
    SAFE_H,
    CONTENT_H,
    RESERVED_LEFT,
    RESERVED_RIGHT,
    RIGHT_MARGIN,
    STICKMAN_MARGIN_X,
    STICKMAN_SIZE,
)


@dataclass
class ImageSlot:
    target_w: int
    target_h: int
    x_expr: str
    y_expr: str


@dataclass
class LayoutResult:
    name: str
    image_slots: List[ImageSlot]
    stickman_pos: Optional[Tuple[str, str]]

def _normalize_stickman_side(stickman_side: str) -> str:
    side = (stickman_side or "left").strip().lower()
    return "right" if side == "right" else "left"


def _content_area(use_stickman: bool, stickman_side: str) -> tuple[int, int]:
    if not use_stickman:
        return 0, OUT_W
    side = _normalize_stickman_side(stickman_side)
    if side == "right":
        content_x = LEFT_MARGIN
        content_w = OUT_W - LEFT_MARGIN - RESERVED_RIGHT
    else:
        content_x = RESERVED_LEFT
        content_w = OUT_W - RESERVED_LEFT - RIGHT_MARGIN
    return content_x, content_w


def _stickman_position(use_stickman: bool, stickman_side: str) -> Optional[Tuple[str, str]]:
    if not use_stickman:
        return None
    side = _normalize_stickman_side(stickman_side)
    if side == "right":
        return (f"W-w-{STICKMAN_MARGIN_X}", "(H-h)/2")
    return (f"{STICKMAN_MARGIN_X}", "(H-h)/2")


def _legacy_single(use_stickman: bool, stickman_side: str) -> LayoutResult:
    content_x, content_w = _content_area(use_stickman, stickman_side)
    if use_stickman:
        target_w = content_w
        target_h = CONTENT_H
        final_x = f"{content_x}+({content_w}-w)/2"
    else:
        target_w = OUT_W
        target_h = SAFE_H
        final_x = "(W-w)/2"

    final_y = "(H-h)/2"
    slot = ImageSlot(target_w=target_w, target_h=target_h, x_expr=final_x, y_expr=final_y)

    stickman_pos = _stickman_position(use_stickman, stickman_side)

    return LayoutResult(name="legacy_single", image_slots=[slot], stickman_pos=stickman_pos)


def _image_center_only() -> LayoutResult:
    slot = ImageSlot(target_w=OUT_W, target_h=SAFE_H, x_expr="(W-w)/2", y_expr="(H-h)/2")
    return LayoutResult(name="image_center_only", image_slots=[slot], stickman_pos=None)


def _stickman_center_only(use_stickman: bool) -> LayoutResult:
    stickman_pos = None
    if use_stickman:
        stickman_pos = ("(W-w)/2", "(H-h)/2")
    return LayoutResult(name="stickman_center_only", image_slots=[], stickman_pos=stickman_pos)


def _two_images_center(use_stickman: bool, stickman_side: str) -> LayoutResult:
    gap = 40
    if use_stickman:
        offset_x, total_w = _content_area(use_stickman, stickman_side)
    else:
        total_w = OUT_W
        offset_x = 0

    slot_w = int((total_w - gap) / 2)
    slot_h = SAFE_H
    left_x = offset_x + int((total_w - (2 * slot_w + gap)) / 2)
    right_x = left_x + slot_w + gap
    y_expr = "(H-h)/2"

    slots = [
        ImageSlot(target_w=slot_w, target_h=slot_h, x_expr=str(left_x), y_expr=y_expr),
        ImageSlot(target_w=slot_w, target_h=slot_h, x_expr=str(right_x), y_expr=y_expr),
    ]
    stickman_pos = _stickman_position(use_stickman, stickman_side)

    return LayoutResult(name="two_images_center", image_slots=slots, stickman_pos=stickman_pos)


def _stickman_left_3img(use_stickman: bool, stickman_side: str) -> LayoutResult:
    content_x, content_w = _content_area(use_stickman, stickman_side)
    gap = 24
    slot_w = content_w
    slot_h = int((SAFE_H - 2 * gap) / 3)
    total_h = slot_h * 3 + gap * 2
    top_y = int((OUT_H - total_h) / 2)
    x_expr = f"{content_x}+({content_w}-{slot_w})/2"

    slots = [
        ImageSlot(target_w=slot_w, target_h=slot_h, x_expr=x_expr, y_expr=str(top_y)),
        ImageSlot(target_w=slot_w, target_h=slot_h, x_expr=x_expr, y_expr=str(top_y + slot_h + gap)),
        ImageSlot(target_w=slot_w, target_h=slot_h, x_expr=x_expr, y_expr=str(top_y + (slot_h + gap) * 2)),
    ]
    stickman_pos = _stickman_position(use_stickman, stickman_side)

    return LayoutResult(name="stickman_left_3img", image_slots=slots, stickman_pos=stickman_pos)


def resolve_layout(
    layout_name: str,
    use_stickman: bool,
    image_count: int,
    stickman_side: str = "left",
) -> Tuple[LayoutResult, List[str]]:
    warnings: List[str] = []
    normalized = (layout_name or "legacy_single").strip().lower()
    normalized_side = _normalize_stickman_side(stickman_side)

    if normalized == "image_center_only":
        layout = _image_center_only()
        if use_stickman:
            warnings.append("Layout image_center_only ignora stickman.")
        if use_stickman and normalized_side == "right":
            warnings.append("Stickman à direita ignorado (layout image_center_only).")
        if image_count < len(layout.image_slots):
            warnings.append(
                f"Imagens insuficientes para layout {layout.name}. "
                f"Esperado {len(layout.image_slots)}, recebido {image_count}."
            )
        return layout, warnings

    if normalized == "stickman_center_only":
        if not use_stickman:
            warnings.append("Layout stickman_center_only sem stickman. Usando legacy_single.")
            return _legacy_single(use_stickman=False, stickman_side=normalized_side), warnings
        if normalized_side == "right":
            warnings.append("Stickman à direita ignorado (layout stickman_center_only).")
        return _stickman_center_only(use_stickman=True), warnings

    if normalized == "two_images_center":
        layout = _two_images_center(use_stickman=use_stickman, stickman_side=normalized_side)
    elif normalized == "stickman_left_3img":
        if not use_stickman:
            warnings.append("Layout stickman_left_3img sem stickman. Usando legacy_single.")
            return _legacy_single(use_stickman=False, stickman_side=normalized_side), warnings
        layout = _stickman_left_3img(use_stickman=True, stickman_side=normalized_side)
    elif normalized == "legacy_single":
        layout = _legacy_single(use_stickman=use_stickman, stickman_side=normalized_side)
    else:
        warnings.append(f"Layout desconhecido '{layout_name}'. Usando legacy_single.")
        layout = _legacy_single(use_stickman=use_stickman, stickman_side=normalized_side)

    if image_count < len(layout.image_slots):
        warnings.append(
            f"Imagens insuficientes para layout {layout.name}. "
            f"Esperado {len(layout.image_slots)}, recebido {image_count}."
        )

    return layout, warnings
