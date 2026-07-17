"""Update router — the one conversation brain shared by the webhook and the
long-polling twin, so the two entry points can't drift apart.

Flow per update:
    button press  → language choice (saved to the chat profile)
    photo+caption → vision model → character card → saved to the profile
                    (the photo bytes stay in memory — never persisted)
    /commands     → menus, family cast management, or a generation job
    plain text    → a `custom` generation job (as before)

Everything the bot *says* comes from i18n.py in the chat's chosen language
(before a choice is made: Telegram's language hint, else English).
"""

from __future__ import annotations

import logging

from . import commands, metrics
from .i18n import CHOOSE_LANGUAGE, LANGUAGE_BUTTONS, Lang, resolve, t
from .models import Mode, new_job
from .ports.profiles import ProfileStore
from .ports.queue import QueuePort
from .ports.vision import VisionProvider
from .prompts import compose_prompt
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

# Telegram photos come in several sizes, smallest first; Claude's Converse API
# caps images at ~3.75 MB, so pick the largest size that stays safely under.
_MAX_PHOTO_BYTES = 3_500_000

_LANGUAGE_KEYBOARD = [[{"text": label, "callback_data": data} for label, data in LANGUAGE_BUTTONS]]


class Router:
    def __init__(
        self,
        telegram: TelegramClient,
        queue: QueuePort,
        profiles: ProfileStore,
        vision: VisionProvider,
    ) -> None:
        self._telegram = telegram
        self._queue = queue
        self._profiles = profiles
        self._vision = vision

    async def handle_update(self, update: dict) -> str:
        """Process one Telegram update. Returns a disposition for metrics:
        "accepted" (generation job enqueued), "handled" (conversational reply),
        or "ignored"."""
        callback = commands.extract_callback(update)
        if callback:
            return await self._handle_callback(update, *callback)

        photo = commands.extract_photo(update)
        if photo:
            return await self._handle_photo(update, *photo)

        chat_id, text = commands.extract_message(update)
        if chat_id is None or not (text or "").strip():
            return "ignored"
        return await self._handle_text(update, chat_id, text.strip())

    # --- helpers -------------------------------------------------------------

    async def _lang_for(self, chat_id: int, update: dict) -> Lang:
        """The chat's chosen language, else Telegram's hint, else English."""
        profile = await self._profiles.get(chat_id)
        if profile.language:
            return resolve(profile.language)
        return resolve(commands.extract_language_hint(update))

    async def _enqueue(self, chat_id: int, mode: Mode, args: str, lang: Lang) -> str:
        characters = None
        if mode is Mode.FAIRYTALE:
            characters = (await self._profiles.get(chat_id)).characters
        prompt = compose_prompt(mode, args, characters)
        job = new_job(chat_id, mode, prompt, language=lang.value)
        await self._queue.enqueue(job)
        metrics.JOBS_ENQUEUED.labels(mode=mode.value).inc()
        logger.info(
            "job enqueued",
            extra={
                "job_id": job.job_id,
                "mode": mode.value,
                "chat_id": chat_id,
                "language": lang.value,
            },
        )
        await self._telegram.send_text(chat_id, t(lang, "ack"))
        return "accepted"

    # --- update kinds ---------------------------------------------------------

    async def _handle_callback(
        self, update: dict, callback_id: str, chat_id: int, data: str
    ) -> str:
        try:
            await self._telegram.answer_callback_query(callback_id)
        except Exception:
            logger.exception("answerCallbackQuery failed", extra={"chat_id": chat_id})

        if data.startswith("lang:"):
            lang = resolve(data.removeprefix("lang:"))
            profile = await self._profiles.get(chat_id)
            profile.language = lang.value
            await self._profiles.put(chat_id, profile)
            logger.info(
                "language selected", extra={"chat_id": chat_id, "language": lang.value}
            )
            await self._telegram.send_text(
                chat_id, f"{t(lang, 'language_set')}\n\n{t(lang, 'menu')}"
            )
            return "handled"

        return "ignored"

    async def _handle_photo(
        self, update: dict, chat_id: int, caption: str, photo_sizes: list[dict]
    ) -> str:
        lang = await self._lang_for(chat_id, update)

        name, _, extra = caption.partition(":")
        name = name.strip()
        if not name:
            await self._telegram.send_text(chat_id, t(lang, "photo_needs_caption"))
            return "handled"

        await self._telegram.send_text(chat_id, t(lang, "photo_processing"))
        try:
            fitting = [
                p for p in photo_sizes if (p.get("file_size") or 0) <= _MAX_PHOTO_BYTES
            ]
            chosen = (fitting or photo_sizes)[-1]
            image_bytes = await self._telegram.download_file(chosen["file_id"])
            # Telegram compresses photo-type uploads to JPEG.
            result = await self._vision.describe_character(image_bytes, "jpeg", name)
        except Exception:
            logger.exception("photo → character failed", extra={"chat_id": chat_id})
            await self._telegram.send_text(chat_id, t(lang, "photo_failed"))
            return "handled"
        finally:
            image_bytes = b""  # photo bytes end their life here — never persisted

        description = result.description
        if extra.strip():
            description = f"{description} ({extra.strip()})"

        profile = await self._profiles.get(chat_id)
        profile.upsert_character(name, description)
        await self._profiles.put(chat_id, profile)

        metrics.CHARACTERS_ADDED.labels(source="photo").inc()
        metrics.JOB_COST_TOTAL.labels(mode="vision").inc(result.cost_usd)
        logger.info(
            "character added from photo",
            extra={
                "chat_id": chat_id,
                "character": name,
                "cast_size": len(profile.characters),
                "cost_usd": result.cost_usd,
                "model": result.model_id,
            },
        )
        await self._telegram.send_text(
            chat_id, t(lang, "family_added_photo", name=name, description=description)
        )
        return "handled"

    async def _handle_text(self, update: dict, chat_id: int, text: str) -> str:
        lang = await self._lang_for(chat_id, update)
        head, args = commands.split_command(text)

        if head in ("/start", "/language"):
            await self._telegram.send_text(
                chat_id, CHOOSE_LANGUAGE, inline_keyboard=_LANGUAGE_KEYBOARD
            )
            return "handled"

        if head == "/family":
            return await self._handle_family(chat_id, args, lang)

        parsed = commands.parse(update)
        if parsed is not None:
            if parsed.mode is Mode.FAIRYTALE:
                profile = await self._profiles.get(chat_id)
                if not profile.characters and not parsed.args:
                    await self._telegram.send_text(chat_id, t(lang, "fairytale_need_family"))
                    return "handled"
            if parsed.mode is Mode.CUSTOM and not parsed.args:
                await self._telegram.send_text(chat_id, t(lang, "ask_custom"))
                return "handled"
            return await self._enqueue(chat_id, parsed.mode, parsed.args, lang)

        if head:
            # Unknown command — show what the bot can do.
            await self._telegram.send_text(chat_id, t(lang, "menu"))
            return "handled"

        # Plain message → custom scene request (unchanged behavior).
        return await self._enqueue(chat_id, Mode.CUSTOM, text, lang)

    async def _handle_family(self, chat_id: int, args: str, lang: Lang) -> str:
        profile = await self._profiles.get(chat_id)
        sub, _, rest = args.partition(" ")
        sub = sub.lower()
        rest = rest.strip()

        if sub == "add":
            name, sep, description = rest.partition(":")
            name, description = name.strip(), description.strip()
            if not sep or not name or not description:
                await self._telegram.send_text(chat_id, t(lang, "family_add_usage"))
                return "handled"
            profile.upsert_character(name, description)
            await self._profiles.put(chat_id, profile)
            metrics.CHARACTERS_ADDED.labels(source="text").inc()
            await self._telegram.send_text(chat_id, t(lang, "family_added_text", name=name))
            return "handled"

        if sub == "clear":
            profile.characters = []
            await self._profiles.put(chat_id, profile)
            await self._telegram.send_text(chat_id, t(lang, "family_cleared"))
            return "handled"

        if sub == "remove":
            if profile.remove_character(rest):
                await self._profiles.put(chat_id, profile)
                await self._telegram.send_text(
                    chat_id, t(lang, "family_removed", name=rest)
                )
            else:
                await self._telegram.send_text(
                    chat_id, t(lang, "family_not_found", name=rest)
                )
            return "handled"

        # No subcommand — list the cast.
        if not profile.characters:
            await self._telegram.send_text(chat_id, t(lang, "family_empty"))
        else:
            lines = [t(lang, "family_list_header")] + [
                f"• {c.name} — {c.description}" for c in profile.characters
            ]
            await self._telegram.send_text(chat_id, "\n".join(lines))
        return "handled"
