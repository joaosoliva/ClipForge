from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Sequence

from clip_specs import KeyframeSpec


@dataclass(frozen=True)
class InterpolatedFrame:
    time: float
    values: Dict[str, float]


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def _ease_linear(t: float) -> float:
    return t


def _ease_in_quad(t: float) -> float:
    return t * t


def _ease_out_quad(t: float) -> float:
    return 1 - (1 - t) * (1 - t)


def _ease_in_out_quad(t: float) -> float:
    if t < 0.5:
        return 2 * t * t
    return 1 - (-2 * t + 2) ** 2 / 2


def _ease_in_cubic(t: float) -> float:
    return t * t * t


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_in_out_cubic(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    return 1 - (-2 * t + 2) ** 3 / 2


EASING_MAP = {
    "linear": _ease_linear,
    "ease_in": _ease_in_quad,
    "ease_out": _ease_out_quad,
    "ease_in_out": _ease_in_out_quad,
    "cubic_in": _ease_in_cubic,
    "cubic_out": _ease_out_cubic,
    "cubic_in_out": _ease_in_out_cubic,
}


def _sorted_keyframes(keyframes: Sequence[KeyframeSpec]) -> list[KeyframeSpec]:
    return sorted(keyframes, key=lambda kf: kf.time)


def _interpolate_value(start: float, end: float, t: float, easing: str) -> float:
    ease_fn = EASING_MAP.get(easing, _ease_linear)
    factor = ease_fn(_clamp(t, 0.0, 1.0))
    return start + (end - start) * factor


def interpolate_keyframes(
    keyframes: Sequence[KeyframeSpec],
    t: float,
) -> InterpolatedFrame | None:
    if not keyframes:
        return None

    ordered = _sorted_keyframes(keyframes)
    if t <= ordered[0].time:
        return InterpolatedFrame(time=t, values=dict(ordered[0].value))
    if t >= ordered[-1].time:
        return InterpolatedFrame(time=t, values=dict(ordered[-1].value))

    for idx in range(len(ordered) - 1):
        current = ordered[idx]
        nxt = ordered[idx + 1]
        if current.time <= t <= nxt.time:
            span = max(0.0001, nxt.time - current.time)
            ratio = (t - current.time) / span
            values: Dict[str, float] = {}
            keys = set(current.value.keys()) | set(nxt.value.keys())
            for key in keys:
                start_val = current.value.get(key, nxt.value.get(key, 0.0))
                end_val = nxt.value.get(key, start_val)
                values[key] = _interpolate_value(start_val, end_val, ratio, current.easing)
            return InterpolatedFrame(time=t, values=values)
    return None


def validate_keyframes(keyframes: Iterable[KeyframeSpec]) -> list[str]:
    errors: list[str] = []
    last_time = None
    for kf in keyframes:
        if kf.time < 0:
            errors.append("Keyframe time must be non-negative.")
        if last_time is not None and kf.time < last_time:
            errors.append("Keyframes must be sorted by time.")
        if kf.easing not in EASING_MAP:
            errors.append(f"Unknown easing '{kf.easing}'.")
        last_time = kf.time
    return errors
