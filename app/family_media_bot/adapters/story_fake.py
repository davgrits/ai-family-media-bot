"""FakeStoryProvider — returns a canned, age-appropriate bedtime story (in the
requested language) so the full flow runs locally with no model calls. Cost is
reported as $0."""

from __future__ import annotations

import asyncio

from ..i18n import Lang, resolve
from ..models import Mode
from ..ports.story import StoryProvider, StoryResult

_CANNED: dict[Lang, str] = {
    Lang.EN: (
        "Once upon a quiet evening, when the stars were just beginning to blink "
        "awake, a little rabbit named Pip tucked a daisy behind one ear and set off "
        "to say goodnight to the meadow. \"Goodnight, tall grass,\" Pip whispered, "
        "and the grass swayed softly back. \"Goodnight, sleepy stream,\" and the "
        "water gurgled a gentle reply. The moon rose round and kind, wrapping the "
        "hills in a silver blanket. Pip yawned, curled up in a nest of soft clover, "
        "and listened to the crickets sing a slow, cozy song. One by one, the meadow "
        "friends closed their eyes. And as the warm night held them close, Pip "
        "drifted off to sleep, dreaming of soft clouds and tomorrow's gentle sun. "
        "Sweet dreams, little one."
    ),
    Lang.RU: (
        "Однажды тихим вечером, когда звёзды только начинали просыпаться, "
        "маленький кролик Пип заложил ромашку за ухо и отправился пожелать "
        "лугу спокойной ночи. «Спокойной ночи, высокая трава», — прошептал Пип, "
        "и трава мягко качнулась в ответ. «Спокойной ночи, сонный ручей», — и "
        "вода тихонько забулькала. Круглая добрая луна укрыла холмы серебряным "
        "одеялом. Пип зевнул, свернулся в гнёздышке из мягкого клевера и слушал, "
        "как сверчки поют медленную уютную песню. Один за другим друзья с луга "
        "закрывали глаза. И тёплая ночь обняла их всех, а Пип уснул, и снились "
        "ему мягкие облака и завтрашнее ласковое солнце. Сладких снов, малыш."
    ),
    Lang.HE: (
        "פעם, בערב שקט, כשהכוכבים רק התחילו להתעורר, ארנבון קטן בשם פיפ שם "
        "פרח מרגנית מאחורי האוזן ויצא לומר לילה טוב לאחו. «לילה טוב, עשב "
        "גבוה», לחש פיפ, והעשב התנועע ברוך בתשובה. «לילה טוב, נחל ישנוני», "
        "והמים פכפכו תשובה עדינה. הירח עלה עגול וטוב־לב ועטף את הגבעות "
        "בשמיכת כסף. פיפ פיהק, התכרבל בקן של תלתן רך והקשיב לצרצרים שרים "
        "שיר איטי ונעים. אחד־אחד עצמו חברי האחו את עיניהם. והלילה החם חיבק "
        "את כולם, ופיפ נרדם וחלם על עננים רכים ועל שמש עדינה של מחר. "
        "חלומות מתוקים, קטנטן."
    ),
}


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
        )

    async def check_ready(self) -> bool:
        return True
