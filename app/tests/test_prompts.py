from family_media_bot.i18n import Lang
from family_media_bot.models import Character, Mode
from family_media_bot.prompts import (
    compose_prompt,
    derive_illustration_prompt,
    system_prompt,
)


def test_system_prompt_per_language_always_requests_illustration_line():
    for lang in Lang:
        prompt = system_prompt(lang.value)
        assert "ILLUSTRATION:" in prompt
    assert "англий" not in system_prompt("en")
    assert "русском" in system_prompt("ru")
    assert "בעברית" in system_prompt("he")


def test_fairytale_prompt_weaves_in_the_saved_cast():
    cast = [
        Character(name="Dana", description="a cheerful girl with curly hair"),
        Character(name="Papa", description="a tall gentle giant"),
    ]
    prompt = compose_prompt(Mode.FAIRYTALE, "in a submarine", cast)
    assert "Dana — a cheerful girl with curly hair" in prompt
    assert "Papa — a tall gentle giant" in prompt
    assert "in a submarine" in prompt


def test_fairytale_prompt_without_cast_keeps_default_roles():
    prompt = compose_prompt(Mode.FAIRYTALE)
    assert "kind queen" in prompt


def test_custom_and_random_prompts():
    assert "a dragon" in compose_prompt(Mode.CUSTOM, "a dragon")
    assert "Surprise scenario" in compose_prompt(Mode.RANDOM)


def test_derive_illustration_prompt_uses_first_sentence():
    prompt = derive_illustration_prompt("A tiny fox naps. More text here.")
    assert prompt.startswith("A tiny fox naps")
