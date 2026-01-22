from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ImageLayer:
    path: str
    zoom_enabled: bool = False
    slide_direction: Optional[str] = None


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
