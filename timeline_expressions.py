from __future__ import annotations

from typing import Iterable, Sequence

from clip_specs import KeyframeSpec
from ffmpeg_expressions import build_eased_value_expr


def _sorted_keyframes(keyframes: Iterable[KeyframeSpec]) -> list[KeyframeSpec]:
    return sorted(keyframes, key=lambda kf: kf.time)


def build_piecewise_expr(
    keyframes: Sequence[KeyframeSpec],
    value_key: str,
    fallback: str,
    time_var: str = "t",
) -> str:
    ordered = _sorted_keyframes(keyframes)
    if len(ordered) < 2:
        return fallback

    expr_parts: list[str] = []
    for idx in range(len(ordered) - 1):
        start = ordered[idx]
        end = ordered[idx + 1]
        start_val = start.value.get(value_key)
        end_val = end.value.get(value_key)
        if start_val is None or end_val is None:
            continue
        eased = build_eased_value_expr(
            start_value=start_val,
            end_value=end_val,
            start_time=start.time,
            end_time=end.time,
            easing=start.easing,
            time_var=time_var,
        )
        expr_parts.append(
            f"if(between({time_var},{eased.start},{eased.end}),{eased.expr}"
        )

    if not expr_parts:
        return fallback

    expr = ",".join(expr_parts)
    expr += f",{fallback}" + ")" * len(expr_parts)
    return expr
