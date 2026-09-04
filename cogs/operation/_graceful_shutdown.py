from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from types import FrameType

import discord
from discord.ext import commands


logger = logging.getLogger(__name__)

SHUTDOWN_REJECTION_MESSAGE = (
    "Bot đang tắt an toàn và không nhận lệnh mới. "
    "Vui lòng thử lại sau khi bot online trở lại."
)


class ShutdownInProgress(commands.CheckFailure):
    """Raised when a command arrives after graceful draining begins."""

    def __init__(self) -> None:
        super().__init__(SHUTDOWN_REJECTION_MESSAGE)
        self.user_message = SHUTDOWN_REJECTION_MESSAGE


class GracefulShutdownManager:
    """Atomically gate new commands and track admitted command invocations."""

    def __init__(self) -> None:
        self._draining = False
        self._reason: str | None = None
        self._active: dict[int, str] = {}
        self._idle = asyncio.Event()
        self._idle.set()
        self._force = asyncio.Event()

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_commands(self) -> tuple[str, ...]:
        return tuple(self._active.values())

    @property
    def force_requested(self) -> bool:
        return self._force.is_set()

    def admit(self, ctx: commands.Context) -> None:
        """Admit one command or raise once shutdown has started.

        This method intentionally contains no await point. Signal callbacks and
        message tasks therefore cannot interleave between checking the drain
        flag and registering an admitted command on the event-loop thread.
        """
        if self._draining:
            raise ShutdownInProgress()

        existing = getattr(ctx, "_graceful_shutdown_token", None)
        if isinstance(existing, int) and existing in self._active:
            return

        token = id(ctx)
        command = getattr(ctx, "command", None)
        command_name = str(
            getattr(command, "qualified_name", None)
            or getattr(ctx, "invoked_with", None)
            or "unknown"
        )
        self._active[token] = command_name
        setattr(ctx, "_graceful_shutdown_token", token)
        self._idle.clear()

    def finish(self, ctx: commands.Context) -> None:
        """Release a previously admitted command; repeated calls are safe."""
        token = getattr(ctx, "_graceful_shutdown_token", None)
        if not isinstance(token, int):
            return
        self._active.pop(token, None)
        try:
            delattr(ctx, "_graceful_shutdown_token")
        except AttributeError:
            pass
        if not self._active:
            self._idle.set()

    def begin_shutdown(self, reason: str) -> bool:
        """Enter drain mode and return whether this was the first request."""
        if self._draining:
            return False
        self._draining = True
        self._reason = reason
        logger.info(
            "Graceful shutdown requested reason=%s active_commands=%s",
            reason,
            self.active_count,
        )
        return True

    def force_shutdown(self) -> None:
        """Stop waiting for active commands after a repeated shutdown signal."""
        if self._force.is_set():
            return
        self._force.set()
        logger.warning(
            "Forced shutdown requested active_commands=%s commands=%s",
            self.active_count,
            self.active_commands,
        )

    async def wait_for_commands(self) -> bool:
        """Wait for admitted commands or a force request.

        Returns ``True`` when every admitted command finished and ``False``
        when a second shutdown request forced the wait to end.
        """
        if not self._draining:
            raise RuntimeError("Shutdown has not been requested")
        if self._idle.is_set():
            return True
        if self._force.is_set():
            return False

        idle_task = asyncio.create_task(self._idle.wait())
        force_task = asyncio.create_task(self._force.wait())
        try:
            await asyncio.wait(
                {idle_task, force_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            return self._idle.is_set()
        finally:
            for task in (idle_task, force_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(idle_task, force_task, return_exceptions=True)


async def send_shutdown_rejection(ctx: commands.Context) -> None:
    """Tell one caller that the bot has stopped accepting commands."""
    try:
        await ctx.send(
            SHUTDOWN_REJECTION_MESSAGE,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.HTTPException):
        logger.warning(
            "Could not deliver shutdown rejection channel=%s actor=%s",
            getattr(ctx, "channel", None),
            getattr(ctx, "author", None),
        )


async def drain_and_close(
    bot: commands.Bot,
    manager: GracefulShutdownManager,
) -> bool:
    """Wait for active commands, then close the Discord client."""
    drained = await manager.wait_for_commands()
    if drained:
        logger.info("All active commands finished; closing Discord client")
    else:
        logger.warning(
            "Closing Discord client with %s command(s) still active",
            manager.active_count,
        )
    await bot.close()
    return drained


def install_shutdown_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[str], None],
) -> Callable[[], None]:
    """Install portable SIGINT/SIGTERM handlers and return a restore callback."""
    registrations: list[tuple[signal.Signals, object]] = []

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        del frame
        try:
            reason = signal.Signals(signum).name
        except ValueError:
            reason = f"signal-{signum}"
        try:
            loop.call_soon_threadsafe(callback, reason)
        except RuntimeError:
            pass

    for name in ("SIGINT", "SIGTERM"):
        shutdown_signal = getattr(signal, name, None)
        if shutdown_signal is None:
            continue
        previous = signal.getsignal(shutdown_signal)
        try:
            signal.signal(shutdown_signal, handle_signal)
        except (OSError, RuntimeError, ValueError):
            logger.exception("Could not install %s shutdown handler", name)
            continue
        registrations.append((shutdown_signal, previous))

    def restore() -> None:
        for shutdown_signal, previous in registrations:
            try:
                if signal.getsignal(shutdown_signal) is handle_signal:
                    signal.signal(shutdown_signal, previous)
            except (OSError, RuntimeError, ValueError):
                logger.exception(
                    "Could not restore %s signal handler",
                    shutdown_signal.name,
                )

    return restore
