import asyncio
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.operation import _graceful_shutdown as shutdown_module
from cogs.operation._graceful_shutdown import (
    SHUTDOWN_REJECTION_MESSAGE,
    GracefulShutdownManager,
    ShutdownInProgress,
    drain_and_close,
    install_shutdown_signal_handlers,
    send_shutdown_rejection,
)


def make_context(name: str = "ping") -> SimpleNamespace:
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name=name),
        invoked_with=name,
        send=AsyncMock(),
        channel=SimpleNamespace(id=501),
        author=SimpleNamespace(id=77),
    )


class TestGracefulShutdownManager(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_rejects_new_commands_and_drains_admitted_work(
        self,
    ) -> None:
        manager = GracefulShutdownManager()
        active = make_context("slow_command")
        rejected = make_context("ping")

        manager.admit(active)
        self.assertEqual(manager.active_count, 1)
        self.assertTrue(manager.begin_shutdown("SIGTERM"))
        self.assertFalse(manager.begin_shutdown("SIGTERM"))

        with self.assertRaises(ShutdownInProgress) as raised:
            manager.admit(rejected)
        self.assertEqual(raised.exception.user_message, SHUTDOWN_REJECTION_MESSAGE)

        waiter = asyncio.create_task(manager.wait_for_commands())
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        manager.finish(active)
        self.assertTrue(await waiter)
        self.assertEqual(manager.active_count, 0)
        manager.finish(active)
        self.assertEqual(manager.active_count, 0)

    async def test_second_signal_forces_wait_without_releasing_active_command(
        self,
    ) -> None:
        manager = GracefulShutdownManager()
        active = make_context("long_command")
        manager.admit(active)
        manager.begin_shutdown("SIGINT")

        waiter = asyncio.create_task(manager.wait_for_commands())
        await asyncio.sleep(0)
        manager.force_shutdown()

        self.assertFalse(await waiter)
        self.assertTrue(manager.force_requested)
        self.assertEqual(manager.active_count, 1)
        manager.finish(active)

    async def test_drain_and_close_waits_until_command_finishes(self) -> None:
        manager = GracefulShutdownManager()
        active = make_context("slow_command")
        manager.admit(active)
        manager.begin_shutdown("SIGTERM")
        bot = SimpleNamespace(close=AsyncMock())

        shutdown = asyncio.create_task(drain_and_close(bot, manager))
        await asyncio.sleep(0)
        bot.close.assert_not_awaited()

        manager.finish(active)

        self.assertTrue(await shutdown)
        bot.close.assert_awaited_once_with()

    async def test_force_request_closes_with_active_command(self) -> None:
        manager = GracefulShutdownManager()
        active = make_context("stuck_command")
        manager.admit(active)
        manager.begin_shutdown("SIGTERM")
        bot = SimpleNamespace(close=AsyncMock())

        shutdown = asyncio.create_task(drain_and_close(bot, manager))
        await asyncio.sleep(0)
        manager.force_shutdown()

        self.assertFalse(await shutdown)
        bot.close.assert_awaited_once_with()
        manager.finish(active)

    async def test_wait_before_shutdown_is_rejected(self) -> None:
        manager = GracefulShutdownManager()

        with self.assertRaises(RuntimeError):
            await manager.wait_for_commands()

    async def test_shutdown_rejection_is_private_from_mentions(self) -> None:
        ctx = make_context()

        await send_shutdown_rejection(ctx)

        ctx.send.assert_awaited_once()
        args = ctx.send.await_args.args
        kwargs = ctx.send.await_args.kwargs
        self.assertEqual(args, (SHUTDOWN_REJECTION_MESSAGE,))
        allowed_mentions = kwargs["allowed_mentions"]
        self.assertFalse(allowed_mentions.everyone)
        self.assertFalse(allowed_mentions.users)
        self.assertFalse(allowed_mentions.roles)
        self.assertFalse(allowed_mentions.replied_user)


class TestShutdownSignals(unittest.TestCase):
    def test_signal_handlers_schedule_requests_and_restore_previous_handlers(
        self,
    ) -> None:
        installed: dict[signal.Signals, object] = {}
        previous = {
            shutdown_signal: object()
            for shutdown_signal in (signal.SIGINT, signal.SIGTERM)
        }

        def fake_getsignal(shutdown_signal):
            return installed.get(shutdown_signal, previous[shutdown_signal])

        def fake_signal(shutdown_signal, handler):
            old = fake_getsignal(shutdown_signal)
            installed[shutdown_signal] = handler
            return old

        loop = SimpleNamespace(call_soon_threadsafe=MagicMock())
        callback = MagicMock()
        with (
            patch.object(
                shutdown_module.signal,
                "getsignal",
                side_effect=fake_getsignal,
            ),
            patch.object(
                shutdown_module.signal,
                "signal",
                side_effect=fake_signal,
            ),
        ):
            restore = install_shutdown_signal_handlers(loop, callback)
            handler = installed[signal.SIGTERM]
            handler(signal.SIGTERM, None)

            loop.call_soon_threadsafe.assert_called_once_with(
                callback,
                "SIGTERM",
            )

            restore()

        self.assertIs(installed[signal.SIGINT], previous[signal.SIGINT])
        self.assertIs(installed[signal.SIGTERM], previous[signal.SIGTERM])


if __name__ == "__main__":
    unittest.main()
