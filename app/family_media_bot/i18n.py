"""Bot-facing UI strings in the three supported languages.

This module owns *what the bot says in chat*; prompt composition for the model
lives in prompts.py. Every key exists for every language (enforced by tests),
so a missing translation is a test failure, not a runtime KeyError.
"""

from __future__ import annotations

from enum import Enum


class Lang(str, Enum):
    EN = "en"
    RU = "ru"
    HE = "he"


DEFAULT_LANG = Lang.EN

# Inline-keyboard labels + callback payloads for the /start language picker.
LANGUAGE_BUTTONS = [
    ("English", "lang:en"),
    ("Русский", "lang:ru"),
    ("עברית", "lang:he"),
]

# Shown before a language is chosen, so it speaks all three at once.
CHOOSE_LANGUAGE = (
    "🌍 Please choose a language · Пожалуйста, выберите язык · בחרו שפה בבקשה"
)


def resolve(code: str | None) -> Lang:
    """Map a language code (ours or Telegram's `language_code`) to a supported
    Lang, falling back to English."""
    base = (code or "").lower().split("-")[0]
    try:
        return Lang(base)
    except ValueError:
        return DEFAULT_LANG


_STRINGS: dict[Lang, dict[str, str]] = {
    Lang.EN: {
        "language_set": "Great — we'll talk in English! 🌙",
        "menu": (
            "Here's what I can do:\n"
            "🧚 /fairytale — a bedtime tale starring your family\n"
            "✍️ /custom — tell me what tonight's story is about (or just type it)\n"
            "🎲 /surprise — a random story, no questions asked\n"
            "👨‍👩‍👧 /family — your story cast (send a photo with a name caption to add someone)\n"
            "🌍 /language — switch language"
        ),
        "ack": "✨ Writing your story and painting the picture — about a minute...",
        "error_generation": (
            "Sorry, the story didn't work out this time 😔 Please try again in a minute."
        ),
        "ask_custom": (
            "What should tonight's story be about? Just type it — "
            "e.g. “a dragon and a lighthouse”."
        ),
        "fairytale_need_family": (
            "I don't know your family yet! Send me a photo of each family member "
            "with their name as the caption — I'll turn them into story characters "
            "(the photo itself is never saved). You can also add someone by text:\n"
            "/family add Name: a short description"
        ),
        "family_empty": (
            "Your story cast is empty so far. Send a photo with a name caption, or:\n"
            "/family add Name: a short description"
        ),
        "family_list_header": "Your story cast:",
        "family_added_photo": (
            "✨ {name} joined the story cast:\n{description}\n"
            "(The photo was not saved — only this description.)"
        ),
        "family_added_text": "✨ {name} joined the story cast.",
        "family_add_usage": "To add by text: /family add Name: a short description",
        "family_cleared": "The story cast is now empty.",
        "family_removed": "{name} left the story cast.",
        "family_not_found": "I couldn't find “{name}” in the cast. /family shows the list.",
        "photo_needs_caption": (
            "Lovely photo! Please send it again with the person's name as the "
            "caption so I know who this is."
        ),
        "photo_processing": "🔍 Meeting your family member — one moment...",
        "photo_failed": "I couldn't turn that photo into a character this time 😔 Please try again.",
    },
    Lang.RU: {
        "language_set": "Отлично — говорим по-русски! 🌙",
        "menu": (
            "Вот что я умею:\n"
            "🧚 /fairytale — сказка, где герои — ваша семья\n"
            "✍️ /custom — расскажи, о чём будет сказка (или просто напиши тему)\n"
            "🎲 /surprise — случайная сказка без вопросов\n"
            "👨‍👩‍👧 /family — герои ваших сказок (пришлите фото с именем в подписи)\n"
            "🌍 /language — сменить язык"
        ),
        "ack": "✨ Придумываю сказку и рисую картинку — это займёт около минуты...",
        "error_generation": (
            "Простите, сказка сейчас не получилась 😔 Попробуйте ещё раз через минутку."
        ),
        "ask_custom": (
            "О чём будет сегодняшняя сказка? Просто напиши — например: "
            "«про дракона и маяк»."
        ),
        "fairytale_need_family": (
            "Я ещё не знаком с вашей семьёй! Пришлите фото каждого члена семьи "
            "с именем в подписи — я превращу их в героев сказок (само фото никуда "
            "не сохраняется). Можно добавить и текстом:\n"
            "/family add Имя: короткое описание"
        ),
        "family_empty": (
            "Пока в сказках нет героев. Пришлите фото с именем в подписи, или:\n"
            "/family add Имя: короткое описание"
        ),
        "family_list_header": "Герои ваших сказок:",
        "family_added_photo": (
            "✨ {name} теперь герой сказок:\n{description}\n"
            "(Фото не сохранено — только это описание.)"
        ),
        "family_added_text": "✨ {name} теперь герой сказок.",
        "family_add_usage": "Добавить текстом: /family add Имя: короткое описание",
        "family_cleared": "Список героев пуст.",
        "family_removed": "{name} больше не в списке героев.",
        "family_not_found": "Не нашёл «{name}» среди героев. Список: /family",
        "photo_needs_caption": (
            "Чудесное фото! Пришлите его ещё раз с именем в подписи, "
            "чтобы я знал, кто это."
        ),
        "photo_processing": "🔍 Знакомлюсь с членом семьи — секундочку...",
        "photo_failed": "Не получилось превратить фото в героя 😔 Попробуйте ещё раз.",
    },
    Lang.HE: {
        "language_set": "מעולה — נדבר בעברית! 🌙",
        "menu": (
            "הנה מה שאני יודע לעשות:\n"
            "🧚 /fairytale — אגדה שבה המשפחה שלכם היא הגיבורה\n"
            "✍️ /custom — ספרו לי על מה יהיה הסיפור (או פשוט כתבו נושא)\n"
            "🎲 /surprise — סיפור אקראי בלי שאלות\n"
            "👨‍👩‍👧 /family — גיבורי הסיפורים שלכם (שלחו תמונה עם שם בכיתוב)\n"
            "🌍 /language — החלפת שפה"
        ),
        "ack": "✨ ממציא סיפור ומצייר איור — זה ייקח בערך דקה...",
        "error_generation": "סליחה, הסיפור לא הצליח הפעם 😔 נסו שוב בעוד דקה.",
        "ask_custom": "על מה יהיה הסיפור של הערב? פשוט כתבו — למשל: «דרקון ומגדלור».",
        "fairytale_need_family": (
            "אני עוד לא מכיר את המשפחה שלכם! שלחו תמונה של כל בן משפחה עם השם "
            "בכיתוב — אהפוך אותם לדמויות בסיפור (התמונה עצמה לא נשמרת). "
            "אפשר גם להוסיף בטקסט:\n"
            "/family add שם: תיאור קצר"
        ),
        "family_empty": (
            "עדיין אין גיבורים בסיפורים. שלחו תמונה עם שם בכיתוב, או:\n"
            "/family add שם: תיאור קצר"
        ),
        "family_list_header": "גיבורי הסיפורים שלכם:",
        "family_added_photo": (
            "✨ {name} הצטרף/ה לגיבורי הסיפורים:\n{description}\n"
            "(התמונה לא נשמרה — רק התיאור הזה.)"
        ),
        "family_added_text": "✨ {name} הצטרף/ה לגיבורי הסיפורים.",
        "family_add_usage": "להוספה בטקסט: /family add שם: תיאור קצר",
        "family_cleared": "רשימת הגיבורים ריקה.",
        "family_removed": "{name} כבר לא ברשימת הגיבורים.",
        "family_not_found": "לא מצאתי את «{name}» ברשימה. הרשימה: /family",
        "photo_needs_caption": (
            "תמונה נהדרת! שלחו אותה שוב עם השם בכיתוב כדי שאדע מי זה."
        ),
        "photo_processing": "🔍 מכיר את בן המשפחה — רגע אחד...",
        "photo_failed": "לא הצלחתי להפוך את התמונה לדמות 😔 נסו שוב.",
    },
}


def t(lang: Lang | str, key: str, **kwargs: str) -> str:
    """Translate `key` into `lang`, formatting any {placeholders}."""
    resolved = resolve(lang.value if isinstance(lang, Lang) else lang)
    text = _STRINGS[resolved][key]
    return text.format(**kwargs) if kwargs else text
