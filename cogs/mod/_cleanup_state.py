"""Shared in-process locks for channel cleanup confirmation workflows."""


ACTIVE_CLEANUP_CHANNEL_IDS: set[int] = set()
