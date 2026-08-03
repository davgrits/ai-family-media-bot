"""FakeStoryProvider — a canned, age-appropriate bedtime story so the full flow
runs locally with no model calls. Cost is reported as $0.

It returns an `illustration_hint`, which matters more than it looks: the real
provider asks the model for that line, and the pipeline prefers it over any
fallback. A fake that omitted it left the primary path unexercised by `make run`
and by every fake-backed test — so the code that actually runs in production was
the code least covered locally.
"""

from __future__ import annotations

import asyncio

from ..i18n import Lang, resolve
from ..models import Mode
from ..ports.story import StoryProvider, StoryResult

_CANNED: dict[Lang, str] = {
    Lang.EN: (
        "Once upon a quiet evening, when the stars were just beginning to blink "
        "awake, a little rabbit named Pip tucked a daisy behind one ear and set off "
        'to say goodnight to the meadow. "Goodnight, tall grass," Pip whispered, '
        'and the grass swayed softly back. "Goodnight, sleepy stream," and the '
        "water gurgled a gentle reply. The moon rose round and kind, wrapping the "
        "hills in a silver blanket. Pip yawned, curled up in a nest of soft clover, "
        "and listened to the crickets sing a slow, cozy song. One by one, the meadow "
        "friends closed their eyes. And as the warm night held them close, Pip "
        "drifted off to sleep, dreaming of soft clouds and tomorrow's gentle sun. "
        "Sweet dreams, little one."
    ),
    Lang.RU: (
        "Однажды тихим вечером, когда звёзды только начали просыпаться, "
        "маленький зайчонок по имени Пип заложил ромашку за ухо и пошёл "
        "пожелать доброй ночи всему лугу. «Спокойной ночи, высокая трава», — "
        "прошептал Пип, и трава тихонько качнулась в ответ. «Спокойной ночи, "
        "сонный ручей», — и вода журчала ласково. Луна поднялась круглая и "
        "добрая и укрыла холмы серебряным одеялом. Пип зевнул, свернулся в "
        "гнёздышке из мягкого клевера и слушал, как сверчки поют медленную "
        "уютную песню. Один за другим друзья на лугу закрыли глаза. И пока "
        "тёплая ночь обнимала их, Пип уснул, и ему снились мягкие облака и "
        "ласковое солнце завтрашнего дня. Сладких снов, малыш."
    ),
    Lang.HE: (
        "בערב שקט אחד, כשהכוכבים רק התחילו להתעורר, ארנבון קטן בשם פיפ תחב "
        "חרצית מאחורי האוזן ויצא לאחל לילה טוב לכל האחו. «לילה טוב, עשב גבוה», "
        "לחש פיפ, והעשב התנועע בעדינות בחזרה. «לילה טוב, פלג מנומנם», והמים "
        "פכפכו תשובה רכה. הלבנה עלתה עגולה וטובה וכיסתה את הגבעות בשמיכת כסף. "
        "פיפ פיהק, התכרבל בקן של תלתן רך, והאזין לצרצרים שרים שיר איטי ונעים. "
        "אחד אחרי השני עצמו חברי האחו את עיניהם. וכשהלילה החם חיבק אותם, פיפ "
        "נרדם וחלם על עננים רכים ועל שמש עדינה של מחר. חלומות מתוקים, קטנטן."
    ),
}

# English regardless of story language, exactly as the real provider is asked to
# produce it — the image model is the audience here, not the family.
_CANNED_HINT = (
    "a small rabbit with a daisy behind one ear, curled in clover under a round "
    "moon on a quiet meadow"
)


class FakeStoryProvider(StoryProvider):
    def __init__(self, model_id: str = "fake-story-v1") -> None:
        self._model_id = model_id

    async def generate(self, mode: Mode, prompt: str, language: str = "ru") -> StoryResult:
        await asyncio.sleep(0)  # stay cooperative on the event loop
        text = _CANNED[resolve(language)]
        return StoryResult(
            text=text,
            model_id=self._model_id,
            cost_usd=0.0,
            tokens_in=len(prompt.split()),
            tokens_out=len(text.split()),
            illustration_hint=_CANNED_HINT,
        )

    async def check_ready(self) -> bool:
        return True
