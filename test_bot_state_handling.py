import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram_manager import bot


class FakeMessage:
    def __init__(self, uid: int, text: str):
        self.from_user = SimpleNamespace(id=uid)
        self.chat = SimpleNamespace(id=uid)
        self.bot = AsyncMock()
        self.text = text
        self.entities = []
        self.answers = []
        self.deleted = False

    async def delete(self):
        self.deleted = True

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.answers), text=text)


class BotStateHandlingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._state.clear()
        bot._last_bot_msg.clear()

    async def test_menu_button_interrupts_existing_non_login_state(self):
        uid = 1001
        message = FakeMessage(uid, "👥 Manage Group")
        bot._state[uid] = {"action": "broadcast_delay_group", "list": "old"}

        with patch.object(bot, "is_registered_admin", return_value=True), \
             patch.object(bot, "_dispatch_menu", new=AsyncMock()) as dispatch:
            await bot.handle_text(message)

        self.assertNotIn(uid, bot._state)
        dispatch.assert_awaited_once_with(message, uid, "lists")
        self.assertTrue(message.deleted)

    async def test_login_code_state_does_not_treat_menu_text_as_menu(self):
        uid = 1002
        message = FakeMessage(uid, "👥 Manage Group")
        client = AsyncMock()
        bot._state[uid] = {"action": "login_code", "client": client, "phone": "+1", "phone_code_hash": "hash"}

        with patch.object(bot, "is_registered_admin", return_value=True), \
             patch.object(bot, "_dispatch_menu", new=AsyncMock()) as dispatch, \
             patch.object(bot, "_finish_login", new=AsyncMock()):
            await bot.handle_text(message)

        dispatch.assert_not_awaited()
        client.sign_in.assert_awaited_once()

    async def test_stop_clears_broadcasting_state_and_acknowledges(self):
        uid = 1003
        message = FakeMessage(uid, "stop")
        bot._state[uid] = {"action": "broadcasting"}

        with patch.object(bot, "is_registered_admin", return_value=True):
            await bot.handle_text(message)

        self.assertNotIn(uid, bot._state)
        self.assertEqual(message.answers[0][0], "Stopping broadcast...")

    async def test_broadcast_summary_uses_log_chat_id_instead_of_user_when_configured(self):
        uid = 1004
        log_chat_id = "@logs"
        message = FakeMessage(uid, "start")
        account = SimpleNamespace(alias="acc1", session_string="session", device_preset="preset")
        target_list = SimpleNamespace(name="list1", targets=["@group"])
        client = AsyncMock()
        client.is_connected = Mock(return_value=True)
        bot._state[uid] = {
            "action": "broadcasting",
            "list": "list1",
            "saved_text": "hello",
            "group_delay": (0.0, 0.0),
            "round_delay": (0.0, 0.0),
        }

        async def stop_after_sleep(_seconds):
            bot._state.pop(uid, None)

        with patch.object(bot, "get_list", return_value=target_list), \
             patch.object(bot, "get_accounts", return_value=[account]), \
             patch.object(bot, "_client_from_session", return_value=client), \
             patch.object(bot, "_broadcast_entities_for_target", new=AsyncMock(return_value=["entity"])), \
             patch.object(bot, "_log_chat_id", return_value=log_chat_id), \
             patch.object(bot, "_watermark_for_user", return_value=""), \
             patch.object(bot.asyncio, "sleep", new=AsyncMock(side_effect=stop_after_sleep)):
            await bot._start_broadcast(message, uid)

        client.send_message.assert_any_await("entity", "hello", parse_mode="html")
        client.send_message.assert_any_await(log_chat_id, unittest.mock.ANY)
        message.bot.send_message.assert_awaited_once()
        self.assertEqual(message.bot.send_message.await_args.args[0], uid)
        self.assertNotIn("Round", message.bot.send_message.await_args.args[1])

    async def test_broadcast_summary_falls_back_to_user_when_log_chat_id_is_empty(self):
        uid = 1005
        message = FakeMessage(uid, "start")
        account = SimpleNamespace(alias="acc1", session_string="session", device_preset="preset")
        target_list = SimpleNamespace(name="list1", targets=["@group"])
        client = AsyncMock()
        client.is_connected = Mock(return_value=True)
        bot._state[uid] = {
            "action": "broadcasting",
            "list": "list1",
            "saved_text": "hello",
            "group_delay": (0.0, 0.0),
            "round_delay": (0.0, 0.0),
        }

        async def stop_after_sleep(_seconds):
            bot._state.pop(uid, None)

        with patch.object(bot, "get_list", return_value=target_list), \
             patch.object(bot, "get_accounts", return_value=[account]), \
             patch.object(bot, "_client_from_session", return_value=client), \
             patch.object(bot, "_broadcast_entities_for_target", new=AsyncMock(return_value=["entity"])), \
             patch.object(bot, "_log_chat_id", return_value=None), \
             patch.object(bot, "_watermark_for_user", return_value=""), \
             patch.object(bot.asyncio, "sleep", new=AsyncMock(side_effect=stop_after_sleep)):
            await bot._start_broadcast(message, uid)

        summary_calls = [call for call in message.bot.send_message.await_args_list if call.args[0] == uid and "Round" in call.args[1]]
        self.assertEqual(len(summary_calls), 1)


if __name__ == "__main__":
    unittest.main()
