import importlib.util
from pathlib import Path

module_path = Path(__file__).with_name("bot.py")
spec = importlib.util.spec_from_file_location("bot_module", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repeated_words_are_collapsed_and_sentence_is_closed():
    text = "Серьёзно серьёзно это звучит как будто"
    result = module.sanitize_quote_text(text)
    assert result == "Серьёзно это звучит как будто."


def test_trailing_incomplete_fragment_is_removed():
    text = "Ты это написал так, будто у тебя есть план на жизнь и"
    result = module.sanitize_quote_text(text)
    assert result.endswith(".")
    assert not result.endswith(" и")


test_repeated_words_are_collapsed_and_sentence_is_closed()
test_trailing_incomplete_fragment_is_removed()
