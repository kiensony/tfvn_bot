"""Shared Mongo settings an administrator can edit from the settings command."""

from dataclasses import dataclass
import math
from urllib.parse import urlsplit


NSFW_GIF_SETTINGS = (
    "BLOWJOB_GIFS", "HANDJOB_GIFS", "FOOTJOB_GIFS", "ASSJOB_GIFS",
    "THIGHJOB_GIFS", "SPANK_GIFS", "RIMJOB_GIFS", "FROTTING_GIFS",
    "FUCKING_GIFS", "CREAMPIE_GIFS", "THREESOME_GIFS", "ORGY_GIFS",
    "GANGBANG_GIFS", "FINGERING_GIFS", "RIDE_GIFS", "FACESIT_GIFS",
)
MOVE_ICON_SETTINGS = (
    "BRILLIANT_MOVE_ICON", "GREAT_MOVE_ICON", "GOOD_MOVE_ICON",
    "FORCED_MOVE_ICON", "MISS_MOVE_ICON", "BLUNDER_MOVE_ICON",
)
_MAX_IDS = 50
_MAX_URLS = 40
_MAX_TEXT = 64
_CHANNEL_HINT = "Nhập một ID kênh hoặc mention kênh."
_CHANNELS_HINT = "Nhập các ID kênh hoặc mention kênh, cách nhau bằng dấu cách hoặc dấu phẩy."
_ROLE_HINT = "Nhập một ID role hoặc mention role."
_ROLES_HINT = "Nhập các ID role hoặc mention role, cách nhau bằng dấu cách hoặc dấu phẩy."
_HOURS_HINT = "Nhập số giờ từ 0 đến 168."
_ICON_HINT = "Nhập một emoji hoặc chữ ngắn."
_URL_HINT = "Nhập các URL http hoặc https, cách nhau bằng dấu cách."


@dataclass(frozen=True)
class SharedSetting:
    name: str
    value_type: str
    kind: str
    reload_required: bool
    hint: str


def _shared(
    name: str, value_type: str, kind: str, reload_required: bool, hint: str,
) -> SharedSetting:
    return SharedSetting(name, value_type, kind, reload_required, hint)


SHARED_SETTINGS = {
    spec.name: spec for spec in (
        _shared("BIRTHDAY_CHANNEL", "STRING", "channel", True, _CHANNEL_HINT),
        _shared("HIGHLIGHT_CHANNEL", "STRING", "channel", False, _CHANNEL_HINT),
        _shared("AREA_51_CHANNEL_ID", "STRING", "channel", False, _CHANNEL_HINT),
        _shared(
            "BOOSTER_CUSTOM_VOICE_CATEGORY_ID", "STRING", "channel", False, _CHANNEL_HINT,
        ),
        _shared(
            "WORD_CONNECT_GAMES_CHANNELS", "ARRAY", "channels", True, _CHANNELS_HINT,
        ),
        _shared(
            "VIETNAMESE_KING_GAMES_CHANNELS", "ARRAY", "channels", True, _CHANNELS_HINT,
        ),
        _shared("FALLEN_FEMBOY_ROLE_ID", "STRING", "role", False, _ROLE_HINT),
        _shared("BOOSTER_CUSTOM_ROLE_ANCHOR_ID", "STRING", "role", False, _ROLE_HINT),
        _shared("KING_ROLE_ID", "STRING", "role", True, _ROLE_HINT),
        _shared("QUEEN_ROLE_ID", "STRING", "role", True, _ROLE_HINT),
        _shared("BETA_ROLE_IDS", "ARRAY", "roles", False, _ROLES_HINT),
        _shared("AREA_51_PRUNE_HOURS", "STRING", "hours", False, _HOURS_HINT),
        *(
            _shared(name, "STRING", "text", True, _ICON_HINT)
            for name in MOVE_ICON_SETTINGS
        ),
        *(
            _shared(name, "ARRAY", "urls", True, _URL_HINT)
            for name in NSFW_GIF_SETTINGS
        ),
    )
}


def shared_setting(name: str) -> SharedSetting | None:
    return SHARED_SETTINGS.get(name)


def normalize_shared_setting(name: str, value: object) -> str | list[str]:
    """Return the value stored in the shared global_vars cache."""
    spec = SHARED_SETTINGS.get(name)
    if spec is None:
        raise ValueError("Only supported shared settings can be saved.")
    if not isinstance(value, str):
        raise ValueError("Shared settings are entered as text.")
    text = value.strip()
    if not text:
        raise ValueError("Shared settings require a nonempty value.")
    if spec.kind == "channel":
        return _ids(text, "<#", one=True)[0]
    if spec.kind == "channels":
        return _ids(text, "<#", one=False)
    if spec.kind == "role":
        return _ids(text, "<@&", one=True)[0]
    if spec.kind == "roles":
        return _ids(text, "<@&", one=False)
    if spec.kind == "hours":
        return _hours(text)
    if spec.kind == "text":
        return _short_text(text)
    if spec.kind == "urls":
        return _urls(text)
    raise ValueError("Unsupported shared setting.")


def format_shared_setting(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def _ids(text: str, mention_prefix: str, *, one: bool) -> list[str]:
    tokens = [token for token in text.replace(",", " ").split() if token]
    if not tokens or (one and len(tokens) != 1) or len(tokens) > _MAX_IDS:
        raise ValueError("A valid Discord ID is required.")
    parsed = []
    seen = set()
    for token in tokens:
        body = token
        if body.startswith(mention_prefix) and body.endswith(">"):
            body = body[len(mention_prefix):-1]
        if not body.isdigit():
            raise ValueError("A valid Discord ID is required.")
        identifier = str(int(body))
        if identifier == "0":
            raise ValueError("A valid Discord ID is required.")
        if identifier in seen:
            continue
        seen.add(identifier)
        parsed.append(identifier)
    return parsed


def _hours(text: str) -> str:
    try:
        hours = float(text)
    except ValueError as error:
        raise ValueError("Hours must be a number from 0 to 168.") from error
    if isinstance(hours, bool) or not math.isfinite(hours) or hours < 0 or hours > 168:
        raise ValueError("Hours must be a number from 0 to 168.")
    if hours == int(hours):
        return str(int(hours))
    return format(hours, "g")


def _short_text(text: str) -> str:
    if "\n" in text or len(text) > _MAX_TEXT:
        raise ValueError("Text settings must be a single short value.")
    return text


def _urls(text: str) -> list[str]:
    tokens = [token for token in text.replace(",", " ").split() if token]
    if not tokens or len(tokens) > _MAX_URLS:
        raise ValueError("At least one HTTP URL is required.")
    urls = []
    for token in tokens:
        parsed = urlsplit(token)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GIF settings require HTTP(S) URLs.")
        urls.append(token)
    return urls
