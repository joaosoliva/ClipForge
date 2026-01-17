from typing import Optional, Tuple

from config import STICKMAN_SIZE
from clip_specs import StickmanAnim


def build_stickman_animation(
    anim: Optional[StickmanAnim],
    total_frames: int,
    final_x: str,
    final_y: str,
) -> Tuple[str, str, str]:
    if not anim:
        return final_x, final_y, "1.0"

    name = (anim.name or "").strip().lower()
    total_frames = max(1, total_frames)

    if name == "walk_to_final":
        walk_frames = max(1, int(total_frames * 0.6))
        start_x = f"({final_x})-200"
        x_expr = (
            f"if(lt(n,{walk_frames}),"
            f"({start_x})+(({final_x})-({start_x}))*n/{walk_frames},"
            f"({final_x}))"
        )
        y_expr = (
            f"if(lt(n,{walk_frames}),"
            f"({final_y})+6*sin(2*PI*n/10),"
            f"({final_y}))"
        )
        return x_expr, y_expr, "1.0"

    if name == "pop_to_final":
        pop_frames = max(1, int(total_frames * 0.3))
        scale_expr = f"if(lt(n,{pop_frames}),0.7+0.3*n/{pop_frames},1.0)"
        return final_x, final_y, scale_expr

    if name == "slide_to_final":
        direction = (anim.direction or "left").strip().lower()
        slide_frames = max(1, int(total_frames * 0.4))
        if direction == "right":
            start_x = f"W+{STICKMAN_SIZE}"
        else:
            start_x = f"-{STICKMAN_SIZE}"
        x_expr = (
            f"if(lt(n,{slide_frames}),"
            f"({start_x})+(({final_x})-({start_x}))*n/{slide_frames},"
            f"({final_x}))"
        )
        return x_expr, final_y, "1.0"

    return final_x, final_y, "1.0"
