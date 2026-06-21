import asyncio
import unittest

from app.ws_manager import WSManager


class WSManagerSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def test_schedule_push_runs_coroutine_when_no_loop_is_registered(self):
        manager = WSManager()
        manager._loop = None
        calls = []

        async def mark():
            calls.append("ran")

        manager.schedule_push(mark())

        self.assertEqual(calls, ["ran"])

    async def test_reconstructing_singleton_does_not_reset_push_state(self):
        manager = WSManager()
        manager._per_user_pending = {123: 2}
        queue = manager._push_queue

        same_manager = WSManager()

        self.assertIs(same_manager._push_queue, queue)
        self.assertEqual(same_manager._per_user_pending, {123: 2})

    async def test_schedule_push_uses_current_running_loop_when_main_loop_missing(self):
        manager = WSManager()
        manager._loop = None
        event = asyncio.Event()

        async def mark():
            event.set()

        manager.schedule_push(mark())
        await asyncio.wait_for(event.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
