"""Pure helpers for the marriage feature (no Discord / Mongo I/O)."""

from __future__ import annotations

from dataclasses import dataclass


XP_PER_INTERACTION = 5
XP_PER_LEVEL = 20


@dataclass(frozen=True)
class RankInfo:
    key: str
    display: str
    emoji: str
    color: int
    min_level: int


# Ordered lowest → highest. Rebalance only here.
MARRIAGE_RANKS: tuple[RankInfo, ...] = (
    RankInfo("bronze", "Đồng", "🥉", 0xCD7F32, 1),
    RankInfo("silver", "Bạc", "🥈", 0xC0C0C0, 5),
    RankInfo("gold", "Vàng", "🥇", 0xFFD700, 10),
    RankInfo("diamond", "Kim cương", "💎", 0xB9F2FF, 18),
    RankInfo("blue_sapphire", "Sapphire xanh", "🔵", 0x0F52BA, 28),
    RankInfo("amethyst", "Thạch anh tím", "💜", 0x9966CC, 40),
    RankInfo("ruby", "Hồng ngọc", "❤️", 0xE0115F, 55),
    RankInfo("emerald", "Lục bảo", "💚", 0x50C878, 75),
    RankInfo("obsidian", "Obsidian", "🖤", 0x1C1C1C, 100),
    RankInfo("eternal", "Vĩnh cửu", "♾️", 0xFF69B4, 150),
)


def normalize_pair(user_id_a: int, user_id_b: int) -> tuple[int, int]:
    """Return sorted pair ids so (a,b) and (b,a) map to the same document."""
    if user_id_a == user_id_b:
        raise ValueError("pair members must be different users")
    if user_id_a < user_id_b:
        return user_id_a, user_id_b
    return user_id_b, user_id_a


def is_pair(left: int, right: int, user_a: int, user_b: int) -> bool:
    try:
        return normalize_pair(left, right) == (user_a, user_b)
    except ValueError:
        return False


def level_from_xp(xp: int) -> int:
    if xp < 0:
        raise ValueError("xp must be non-negative")
    return (xp // XP_PER_LEVEL) + 1


def rank_from_level(level: int) -> RankInfo:
    if level < 1:
        raise ValueError("level must be >= 1")
    current = MARRIAGE_RANKS[0]
    for rank in MARRIAGE_RANKS:
        if level >= rank.min_level:
            current = rank
        else:
            break
    return current


def rank_from_xp(xp: int) -> RankInfo:
    return rank_from_level(level_from_xp(xp))


def xp_progress_in_level(xp: int) -> tuple[int, int]:
    """Return (xp into current level band, xp needed per level)."""
    if xp < 0:
        raise ValueError("xp must be non-negative")
    return xp % XP_PER_LEVEL, XP_PER_LEVEL


def next_rank(level: int) -> RankInfo | None:
    """Rank after the current one, or None if already max."""
    current = rank_from_level(level)
    for rank in MARRIAGE_RANKS:
        if rank.min_level > current.min_level:
            return rank
    return None


def progress_bar(ratio: float, segments: int = 10) -> str:
    """Build a text bar from a 0..1 ratio."""
    if segments < 1:
        raise ValueError("segments must be >= 1")
    clamped = max(0.0, min(1.0, ratio))
    filled = int(round(clamped * segments))
    filled = max(0, min(segments, filled))
    return "█" * filled + "░" * (segments - filled)


def level_progress_bar(xp: int, segments: int = 10) -> str:
    into, need = xp_progress_in_level(xp)
    return progress_bar(into / need if need else 1.0, segments=segments)


def rank_level_progress_bar(level: int, segments: int = 10) -> str:
    """Progress within current rank band toward the next rank's min_level."""
    current = rank_from_level(level)
    following = next_rank(level)
    if following is None:
        return progress_bar(1.0, segments=segments)
    span = following.min_level - current.min_level
    if span <= 0:
        return progress_bar(1.0, segments=segments)
    into = level - current.min_level
    return progress_bar(into / span, segments=segments)


def days_together(married_at, now) -> int:
    """Whole days between married_at and now (both timezone-aware or naive)."""
    delta = now - married_at
    return max(0, int(delta.total_seconds() // 86400))
