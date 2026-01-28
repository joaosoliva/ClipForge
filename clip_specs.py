from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class ImageLayer:
    path: str
    zoom_enabled: bool = False
    slide_direction: Optional[str] = None
    blur_entry: Optional["BlurEntrySpec"] = None
    keyframes: Sequence["KeyframeSpec"] = field(default_factory=list)


@dataclass
class StickmanAnim:
    name: str
    direction: Optional[str] = None


@dataclass
class StickmanLayer:
    path: str
    speech: str = ""
    anim: Optional[StickmanAnim] = None


@dataclass
class ClipSpec:
    duration: float
    fps: int
    width: int
    height: int
    layout: str
    stickman_position: str = "left"
    images: List[ImageLayer] = field(default_factory=list)
    stickman: Optional[StickmanLayer] = None
    text: Optional[str] = None
    text_anchor: Optional[str] = None
    text_margin: Optional[int] = None
    text_anchor_slot: Optional[int] = None
    timeline: Optional["TimelineSpec"] = None


@dataclass
class BlurEntrySpec:
    """Blur aplicado somente durante a entrada (slide-in)."""
    enabled: bool = False
    duration: Optional[float] = None
    strength: Optional[float] = None
    method: str = "tblend"


@dataclass
class KeyframeSpec:
    time: float
    value: Dict[str, float]
    easing: str = "linear"


@dataclass
class EffectWindowSpec:
    name: str
    start: float
    end: float
    params: Dict[str, float] = field(default_factory=dict)


@dataclass
class TrackSpec:
    track_id: str
    kind: str
    target_id: Optional[str] = None
    keyframes: Sequence[KeyframeSpec] = field(default_factory=list)
    effects: Sequence[EffectWindowSpec] = field(default_factory=list)


@dataclass
class TimelineSpec:
    duration: float
    fps: int
    tracks: Sequence[TrackSpec] = field(default_factory=list)
