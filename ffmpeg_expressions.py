from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class EasedExpression:
    expr: str
    start: float
    end: float


def _clamp_expr(expr: str, min_val: float, max_val: float) -> str:
    return f"max({min_val},min({expr},{max_val}))"


def _normalize_time_expr(time_var: str, start: float, end: float) -> str:
    duration = max(0.0001, end - start)
    return _clamp_expr(f"({time_var}-{start})/{duration}", 0.0, 1.0)


EASING_EXPRESSIONS: Dict[str, str] = {
    "linear": "{t}",
    "ease_in": "({t})*({t})",
    "ease_out": "1-(1-({t}))*(1-({t}))",
    "ease_in_out": "if(lt({t},0.5),2*({t})*({t}),1-((-2*{t}+2)*(-2*{t}+2))/2)",
    "cubic_in": "({t})*({t})*({t})",
    "cubic_out": "1-(1-({t}))*(1-({t}))*(1-({t}))",
    "cubic_in_out": "if(lt({t},0.5),4*({t})*({t})*({t}),1-((-2*{t}+2)*(-2*{t}+2)*(-2*{t}+2))/2)",
}


def build_eased_value_expr(
    start_value: float,
    end_value: float,
    start_time: float,
    end_time: float,
    easing: str = "linear",
    time_var: str = "t",
) -> EasedExpression:
    normalized = _normalize_time_expr(time_var, start_time, end_time)
    easing_expr = EASING_EXPRESSIONS.get(easing, EASING_EXPRESSIONS["linear"])
    eased = easing_expr.format(t=normalized)
    expr = f"{start_value}+({end_value}-{start_value})*({eased})"
    return EasedExpression(expr=expr, start=start_time, end=end_time)
