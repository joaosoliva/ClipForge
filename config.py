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
TEXT_IMAGE_MARGIN = 16

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
