import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import handlers
from helpers import _build_kira_reply, is_meme_template_text
from quote_utils import build_quote_system_prompt, parse_quote_command_args, sanitize_quote_text


class QuoteStyleTests(unittest.TestCase):
    def test_build_quote_system_prompt_contains_required_styles(self):
        prompt = build_quote_system_prompt("genz")
        lowered = prompt.lower()
        self.assertIn("зумерский", lowered)
        self.assertIn("токсичный", lowered)
        self.assertIn("зубастый", lowered)

    def test_sanitize_quote_text_removes_farm_related_terms(self):
        cleaned = sanitize_quote_text("Это фарма и grind, брат")
        self.assertNotIn("фарм", cleaned.lower())
        self.assertNotIn("grind", cleaned.lower())

    def test_parse_quote_command_args_ignores_numeric_count(self):
        self.assertEqual(parse_quote_command_args(["3", "roast"]), "roast")
        self.assertIsNone(parse_quote_command_args(["2"]))
        self.assertEqual(parse_quote_command_args(["genz"]), "genz")

    def test_is_meme_template_text_matches_67_and_robot(self):
        self.assertTrue(is_meme_template_text("про 67"))
        self.assertTrue(is_meme_template_text("робот сочинит симфонию"))
        self.assertFalse(is_meme_template_text("привет как дела"))

    def test_mortis_reply_distinguishes_joke_from_serious_negativity(self):
        joke_reply = handlers._build_mortis_chat_reply("ты же как 3.14рас, это шутка")
        serious_reply = handlers._build_mortis_chat_reply("ты реально меня задеваешь как 3.14рас")

        self.assertIn("шут", joke_reply.lower())
        self.assertIn("серьёз", serious_reply.lower())

    def test_kira_reply_handles_flirting_and_creativity_topics(self):
        flirt_reply = _build_kira_reply("кто-то подкатил к Кире в чате")
        art_reply = _build_kira_reply("фанарт и эдиты по Кире")

        self.assertIn("мортис", flirt_reply.lower())
        self.assertTrue(any(keyword in art_reply.lower() for keyword in ["рис", "арт", "эдит", "творч", "видео"]))

    def test_mortis_reply_handles_duel_and_esports_topics(self):
        lag_reply = handlers._build_mortis_chat_reply("я проиграл из-за пинга и лагов")
        duel_reply = handlers._build_mortis_chat_reply("пошли 1 на 1")
        esports_reply = handlers._build_mortis_chat_reply("сравни меня с другими стримерами в киберспорте")

        self.assertTrue(any(keyword in lag_reply.lower() for keyword in ["пинг", "лаг", "тимм", "катк"]))
        self.assertTrue(any(keyword in duel_reply.lower() for keyword in ["1 на 1", "дуэль", "pvp", "вызов"]))
        self.assertTrue(any(keyword in esports_reply.lower() for keyword in ["конкур", "стример", "кибер", "скилл", "вайб"]))

    def test_handler_prefers_nick_template_for_mortis_nickname_question(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=2, is_bot=False),
            text="Почему мортис придумал такой ник?",
            caption=None,
            photo=None,
            video=None,
            video_note=None,
            voice=None,
            audio=None,
            document=None,
            reply_to_message=None,
            reply=AsyncMock(),
        )

        async def run_test():
            with patch("handlers.save_message_to_history", Mock()):
                await handlers.handle_general_templates(message)

        asyncio.run(run_test())

        reply_text = message.reply.await_args_list[0].args[0]
        self.assertIn("Mortis", reply_text)
        self.assertNotIn("Если ты говоришь про Мортиса", reply_text)

    def test_handler_prefers_mortisplay_template_for_intro_questions(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=2, is_bot=False),
            text="кто такой mortisplay?",
            caption=None,
            photo=None,
            video=None,
            video_note=None,
            voice=None,
            audio=None,
            document=None,
            reply_to_message=None,
            reply=AsyncMock(),
        )

        async def run_test():
            with patch("handlers.save_message_to_history", Mock()):
                await handlers.handle_general_templates(message)

        asyncio.run(run_test())

        reply_text = message.reply.await_args_list[0].args[0]
        self.assertIn("Mortisplay", reply_text)
        self.assertNotIn("Если ты говоришь про Мортиса", reply_text)

    def test_handle_quote_command_uses_current_command_text(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=1),
            text="/q@agentplay_bot вот текст для цитаты",
            reply_to_message=None,
            message_id=7,
            reply=AsyncMock(),
        )
        collect_context_mock = Mock()
        generate_quote_reply_mock = AsyncMock(return_value="цитата")

        async def run_test():
            with (
                patch("handlers.increment_stat", Mock()),
                patch("handlers.bot.send_chat_action", new=AsyncMock()),
                patch("handlers.generate_quote_reply", generate_quote_reply_mock),
                patch("handlers.send_quote_with_feedback", new=AsyncMock()),
                patch("handlers.collect_reply_context", new=AsyncMock(return_value=["ignored"])) as collect_context_mock,
            ):
                await handlers.handle_quote_command(message)

        asyncio.run(run_test())

        self.assertFalse(collect_context_mock.called)
        self.assertEqual(generate_quote_reply_mock.await_args.args[0], "вот текст для цитаты")

    def test_handler_replies_with_ladno_to_cool_message(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=1, type="private"),
            from_user=SimpleNamespace(id=2, is_bot=False),
            text="прохладно 🤣",
            caption=None,
            photo=None,
            video=None,
            video_note=None,
            voice=None,
            audio=None,
            document=None,
            reply_to_message=None,
            reply=AsyncMock(),
        )

        async def run_test():
            with patch("handlers.random.random", return_value=0.0), patch("handlers.save_message_to_history", Mock()):
                await handlers.handle_general_templates(message)

        asyncio.run(run_test())

        self.assertEqual(message.reply.await_args_list[0].args[0], "ладно")

    def test_reply_to_agent_message_accepts_explicit_arguments(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=1), reply=AsyncMock())
        status = SimpleNamespace(edit_text=AsyncMock())
        message.reply.side_effect = [status, status]

        async def run_test():
            with (
                patch("handlers.bot.send_chat_action", new=AsyncMock()),
                patch("handlers.generate_ai_reply", new=AsyncMock(return_value="ok")),
                patch("handlers.animate_thinking_status", new=AsyncMock()),
            ):
                await handlers._reply_to_agent_message(message, clean_text="привет", context_text=None)

        asyncio.run(run_test())
        self.assertTrue(message.reply.called)


if __name__ == "__main__":
    unittest.main()
