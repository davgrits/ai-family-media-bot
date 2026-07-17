from family_media_bot.i18n import _STRINGS, DEFAULT_LANG, Lang, resolve, t


def test_every_key_exists_in_every_language():
    keysets = {lang: set(strings) for lang, strings in _STRINGS.items()}
    assert set(keysets) == set(Lang), "a language is missing entirely"
    reference = keysets[Lang.EN]
    for lang, keys in keysets.items():
        assert keys == reference, f"{lang}: keys differ from EN: {keys ^ reference}"


def test_resolve_maps_codes_and_falls_back():
    assert resolve("ru") is Lang.RU
    assert resolve("he-IL") is Lang.HE
    assert resolve("EN") is Lang.EN
    assert resolve("fr") is DEFAULT_LANG
    assert resolve("") is DEFAULT_LANG
    assert resolve(None) is DEFAULT_LANG


def test_t_formats_placeholders_in_all_languages():
    for lang in Lang:
        text = t(lang, "family_added_photo", name="Dana", description="a hero")
        assert "Dana" in text and "a hero" in text
