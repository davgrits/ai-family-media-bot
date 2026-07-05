"""Ports — the interfaces that keep AWS out of the app logic.

Each concern (queue, story, image, storage) is an abstract base class with a dev
implementation and a prod implementation in `family_media_bot.adapters`.
"""
