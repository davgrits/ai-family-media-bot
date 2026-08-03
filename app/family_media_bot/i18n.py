"""What the bot says in chat, in the three supported languages.

This module owns *chat* strings. What we say to the *model* lives in prompts.py.
The split matters: chat text is localised, while the illustration prompt stays
English regardless of story language, because that is the language the image
model is good at.

Every key exists for every language, enforced by a test — so a missing
translation is a test failure rather than a runtime KeyError in front of a child.
"""

from __future__ import annotations

from enum import Enum


class Lang(str, Enum):
    EN = "en"
    RU = "ru"
    HE = "he"


DEFAULT_LANG = Lang.RU

# Right-to-left languages. Telegram renders these correctly on its own, but a
# story containing Latin-script character names needs bidi isolation so the
# names do not visually reorder the sentence around them.
RTL_LANGUAGES = frozenset({Lang.HE})


def resolve(code: str | None) -> Lang:
    """Map a language code — ours, or Telegram's `language_code` — to a supported
    Lang, falling back to the default. `ru-RU` resolves to `ru`."""
    base = (code or "").lower().split("-")[0]
    try:
        return Lang(base)
    except ValueError:
        return DEFAULT_LANG


_STRINGS: dict[Lang, dict[str, str]] = {
    Lang.EN: {
        "greeting": (
            "Hello! I tell bedtime stories. 🌙\n"
            "Just write what tonight's story should be about — for example: "
            "“a dragon and a lighthouse”."
        ),
        "menu": (
            "Here's what I can do:\n"
            "🧚 /fairytale — a bedtime tale starring your family\n"
            "✍️ /custom — tell me what tonight's story is about (or just type it)\n"
            "🎲 /surprise — a random story, no questions asked\n"
            "👨‍👩‍👧 /family — who stars in the stories\n"
            "🌍 Start any message with lang:en, lang:ru or lang:he to pick a language"
        ),
        "ack": "✨ Writing your story and painting the picture — about a minute...",
        "error_generation": (
            "Sorry, the story didn't work out this time 😔 Please try again in a minute."
        ),
        "ask_custom": (
            "What should tonight's story be about? Just type it — e.g. “a dragon and a lighthouse”."
        ),
        "family_list_header": "Who stars in the stories:",
        "family_empty": (
            "The story cast is empty. Characters live in the project's registry and "
            "are added there, not from chat."
        ),
    },
    Lang.RU: {
        "greeting": (
            "Привет! Я рассказываю сказки на ночь. 🌙\n"
            "Просто напиши, о чём должна быть сказка — например: "
            "«история про дракона и маяк»."
        ),
        "menu": (
            "Вот что я умею:\n"
            "🧚 /fairytale — сказка, где герои — ваша семья\n"
            "✍️ /custom — расскажи, о чём будет сказка (или просто напиши тему)\n"
            "🎲 /surprise — случайная сказка без вопросов\n"
            "👨‍👩‍👧 /family — кто играет в сказках\n"
            "🌍 Начни сообщение с lang:en, lang:ru или lang:he, чтобы выбрать язык"
        ),
        "ack": "✨ Придумываю сказку и рисую картинку — это займёт около минуты...",
        "error_generation": (
            "Простите, сказка сейчас не получилась 😔 Попробуйте ещё раз через минутку."
        ),
        "ask_custom": (
            "О чём будет сегодняшняя сказка? Просто напиши — например: «про дракона и маяк»."
        ),
        "family_list_header": "Кто играет в сказках:",
        "family_empty": (
            "Список героев пуст. Герои живут в реестре проекта и добавляются там, а не из чата."
        ),
    },
    Lang.HE: {
        "greeting": (
            "שלום! אני מספר סיפורים לפני השינה. 🌙\n"
            "פשוט כתבו על מה יהיה הסיפור — למשל: «דרקון ומגדלור»."
        ),
        "menu": (
            "הנה מה שאני יודע לעשות:\n"
            "🧚 /fairytale — אגדה שבה המשפחה שלכם היא הגיבורה\n"
            "✍️ /custom — ספרו לי על מה יהיה הסיפור (או פשוט כתבו נושא)\n"
            "🎲 /surprise — סיפור אקראי בלי שאלות\n"
            "👨‍👩‍👧 /family — מי מככב בסיפורים\n"
            "🌍 התחילו הודעה ב-lang:en, lang:ru או lang:he כדי לבחור שפה"
        ),
        "ack": "✨ ממציא סיפור ומצייר איור — זה ייקח בערך דקה...",
        "error_generation": "סליחה, הסיפור לא הצליח הפעם 😔 נסו שוב בעוד דקה.",
        "ask_custom": "על מה יהיה הסיפור של הערב? פשוט כתבו — למשל: «דרקון ומגדלור».",
        "family_list_header": "מי מככב בסיפורים:",
        "family_empty": (
            "רשימת הגיבורים ריקה. הדמויות נמצאות במרשם של הפרויקט ומתווספות שם, לא מהצ׳אט."
        ),
    },
}

# Every language must define exactly this set. Asserted by test_i18n.
STRING_KEYS = frozenset(_STRINGS[Lang.EN])


def t(lang: Lang | str, key: str, **kwargs: str) -> str:
    """Translate `key` into `lang`, formatting any {placeholders}."""
    resolved = lang if isinstance(lang, Lang) else resolve(lang)
    text = _STRINGS[resolved][key]
    return text.format(**kwargs) if kwargs else text
