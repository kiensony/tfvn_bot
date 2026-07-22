import os
from fnmatch import fnmatch


def cog_disabled(module: str) -> bool:
    """Match a module against comma-separated DISABLED_COGS patterns."""
    patterns = [
        value.strip()
        for value in os.getenv("DISABLED_COGS", "").split(",")
        if value.strip()
    ]
    return any(fnmatch(module, pattern) for pattern in patterns)
