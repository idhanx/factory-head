"""The Copilot's own sentences, in the languages the floor actually reads.

Two things answer a question in this product: the local model, which is handed
a fact bundle and told which language to write in, and the deterministic
fallback in ``copilot.py``, which is assembled from the templates below. Only
the second one is guaranteed to exist — a plant with no GPU never reaches the
model, and every caller falls back — so the deterministic path has to be
translatable too. Without that, picking Hindi would change the buttons around
the answer and nothing in it.

Keyed by the English sentence, exactly like the frontend dictionaries in
``frontend/src/lib/i18n``. That is the same deliberate trade made there: a
reworded English source misses the dictionary and falls back to English, which
is visible and fixable, instead of a stale key that silently says something
else.

Never translated, here or anywhere: money, counts, PPM, clock times, machine
codes, part numbers, reason-code labels and defect labels. Those arrive from
the kernel already formatted (``app/kernel/money.py``) and are reconciled
against paper — a rupee figure that reads differently on two tablets is a
figure nobody trusts.
"""

from __future__ import annotations

#: The languages the Copilot will answer in. ``LANGUAGE_NAMES`` is also what
#: goes into the model's system prompt, so the names are the English ones a
#: model recognises rather than the native spelling.
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "ta": "Tamil", "hi": "Hindi"}

DEFAULT_LANGUAGE = "en"


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, LANGUAGE_NAMES[DEFAULT_LANGUAGE])


def is_supported(language: str | None) -> bool:
    return language in LANGUAGE_NAMES


class _Missing(dict):
    """Leaves an unknown ``{placeholder}`` as written instead of raising.

    A translation with a typo in a placeholder name must degrade to slightly
    odd text on one line, never to a 500 on a question an owner asked.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


#: How a sentence ends in each language. Hindi is written with a danda, and an
#: answer full of Devanagari stopped with Latin full stops reads as though it
#: were machine output — which is exactly what this product is trying not to be.
SENTENCE_END: dict[str, str] = {"en": ".", "ta": ".", "hi": "।"}


def join_sentences(language: str, parts: list[str]) -> str:
    """Join already-translated fragments into one paragraph.

    Fragments arrive with and without their own full stop depending on whether
    they came from a template or a limit, so each one is trimmed and then
    stopped consistently.
    """
    stop = SENTENCE_END.get(language, ".")
    cleaned = [part.strip().rstrip(" .।") for part in parts if part and part.strip()]
    return (stop + " ").join(cleaned) + stop if cleaned else ""


def say(language: str, source: str, **params: object) -> str:
    """The English ``source`` in ``language``, with ``{placeholders}`` filled."""
    template = DICTIONARIES.get(language, {}).get(source, source)
    if not params:
        return template
    try:
        return template.format_map(_Missing(params))
    except (IndexError, ValueError):
        # A stray brace in a translation is a typo, not a reason to fail.
        return template


# ---------------------------------------------------------------------------
# Fact labels
#
# The Copilot names a fact with the same words the screens use, so "recoverable
# loss" in an answer and "Recoverable loss" on the KPI tile are recognisably
# the same number. Keys with no entry fall back to the humanised key, which is
# readable English rather than debug output.
# ---------------------------------------------------------------------------

FACT_LABELS: dict[str, str] = {
    "about_station": "Station",
    "period": "Period",
    "recoverable_loss": "Recoverable loss",
    "previous_period_loss": "Previous period loss",
    "change": "Change",
    "today_recoverable_loss": "Today's recoverable loss",
    "today_downtime_minutes": "Today's downtime (minutes)",
    "yesterday_recoverable_loss": "Yesterday's recoverable loss",
    "yesterday_structural_loss": "Yesterday's structural loss",
    "yesterday_downtime_minutes": "Yesterday's downtime (minutes)",
    "shifts_of_history": "Shifts of history",
    "days_live": "Days live",
    "period_days": "Period (days)",
    "good_parts": "Good parts",
    "rejected_parts": "Rejected parts",
    "ppm": "PPM",
    # Company questions (§3 Layer 3 — "anyone in the plant can ask").
    "company_name": "Company",
    "plant_code": "Plant code",
    "location": "Location",
    "shift_pattern": "Shifts",
    "machines": "Machines",
    "sections": "Sections",
    "active_part_numbers": "Active part numbers",
    "part_families": "Part families",
    "customers": "Customers",
    "operators_on_roll": "Operators on roll",
    "people_with_a_login": "People with a login",
    "working_days_per_year": "Working days a year",
    "currency": "Currency",
    "running_since": "Running since",
}


def fact_label(language: str, key: str) -> str:
    return say(language, FACT_LABELS.get(key, key.replace("_", " ").capitalize()))


# ---------------------------------------------------------------------------
# Tamil (தமிழ்)
#
# Register: the plain Tamil a supervisor speaks on the floor, carrying the
# English loanwords the trade already uses (ஷிப்ட், PPM, Factory Memory).
# ---------------------------------------------------------------------------

TA: dict[str, str] = {
    # --- the refusal ---------------------------------------------------------
    "Sorry, I do not have an answer for this question. Please ask me a "
    "professional or core-based question.": (
        "மன்னிக்கவும், இந்தக் கேள்விக்கு என்னிடம் பதில் இல்லை. ஆலை அல்லது "
        "நிறுவனம் தொடர்பான கேள்வியைக் கேளுங்கள்."
    ),
    # --- answer sentences ----------------------------------------------------
    "No data recorded for that period": "அந்தக் காலத்திற்கு எந்தப் பதிவும் இல்லை",
    "{station} has {occurrences} {reason} events in the last 4 weeks costing "
    "{cost} — roughly one every {interval_days} days": (
        "{station} இல் கடந்த 4 வாரங்களில் {occurrences} {reason} நிகழ்வுகள், "
        "மொத்தம் {cost} — கிட்டத்தட்ட {interval_days} நாட்களுக்கு ஒன்று"
    ),
    "Top defects: {defects}": "முதன்மைக் குறைபாடுகள்: {defects}",
    "{count} action(s) overdue, biggest {cost}": (
        "{count} நடவடிக்கை(கள்) கெடு தாண்டியது, மிகப்பெரியது {cost}"
    ),
    "Biggest single item: {reason} on {station} ({cost})": (
        "மிகப்பெரிய ஒற்றை இழப்பு: {station} இல் {reason} ({cost})"
    ),
    "Last time ({when}): {fix}": "கடைசி முறை ({when}): {fix}",
    "Last time ({when}): {fix} — check {check_first} first": (
        "கடைசி முறை ({when}): {fix} — முதலில் {check_first} பாருங்கள்"
    ),
    # --- limits (what the data cannot answer) --------------------------------
    "Factory Memory has no recorded fix matching this.": (
        "இதற்குப் பொருந்தும் பழைய சரிசெய்தல் எதுவும் Factory Memory இல் இல்லை."
    ),
    "Only {shifts} shifts of history exist, so pattern questions cannot be "
    "answered yet. I have seen {events} events like this; I'll give you "
    "pattern analysis once I've seen more.": (
        "{shifts} ஷிப்ட்களின் பதிவு மட்டுமே உள்ளது, எனவே முறை (pattern) "
        "கேள்விகளுக்கு இப்போது பதில் சொல்ல முடியாது. இதுபோன்ற {events} "
        "நிகழ்வுகளை மட்டுமே பார்த்திருக்கிறேன்; மேலும் பதிவுகள் சேர்ந்ததும் "
        "முறை பகுப்பாய்வு தருகிறேன்."
    ),
    "{station} has no recurring pattern in the last 4 weeks — nothing repeated "
    "three or more times.": (
        "கடந்த 4 வாரங்களில் {station} இல் திரும்பத் திரும்ப வரும் முறை எதுவும் "
        "இல்லை — எதுவும் மூன்று முறைக்கு மேல் நிகழவில்லை."
    ),
    "That question was not understood, so this is today's position rather than "
    "an answer to it. Losses for a period, defects and quality, why a machine "
    "keeps stopping, what fixed something last time, and the company's own "
    "details can all be asked for by name.": (
        "இந்தக் கேள்வி புரியவில்லை, எனவே இது அதற்கான பதில் அல்ல — இன்றைய நிலை. "
        "ஒரு காலத்தின் இழப்புகள், குறைபாடுகள் மற்றும் தரம், ஓர் இயந்திரம் ஏன் "
        "திரும்பத் திரும்ப நிற்கிறது, கடந்த முறை எது சரிசெய்தது, நிறுவனத்தின் "
        "விவரங்கள் — இவற்றைப் பெயர் சொல்லிக் கேட்கலாம்."
    ),
    "Not enough history for pattern analysis (needs about 4 weeks).": (
        "முறை பகுப்பாய்வுக்குப் போதிய பதிவு இல்லை (சுமார் 4 வாரங்கள் தேவை)."
    ),
    # --- fact labels ---------------------------------------------------------
    "Station": "நிலையம்",
    "Period": "காலம்",
    "Recoverable loss": "மீட்கக்கூடிய இழப்பு",
    "Previous period loss": "முந்தைய காலத்தின் இழப்பு",
    "Change": "மாற்றம்",
    "Today's recoverable loss": "இன்றைய மீட்கக்கூடிய இழப்பு",
    "Today's downtime (minutes)": "இன்றைய நிற்கும் நேரம் (நிமிடங்கள்)",
    "Yesterday's recoverable loss": "நேற்றைய மீட்கக்கூடிய இழப்பு",
    "Yesterday's structural loss": "நேற்றைய கட்டமைப்பு இழப்பு",
    "Yesterday's downtime (minutes)": "நேற்றைய நிற்கும் நேரம் (நிமிடங்கள்)",
    "Shifts of history": "பதிவான ஷிப்ட்கள்",
    "Days live": "இயங்கிய நாட்கள்",
    "Period (days)": "காலம் (நாட்கள்)",
    "Good parts": "நல்ல பாகங்கள்",
    "Rejected parts": "நிராகரிக்கப்பட்ட பாகங்கள்",
    "Company": "நிறுவனம்",
    "Plant code": "ஆலைக் குறியீடு",
    "Location": "இடம்",
    "Shifts": "ஷிப்ட்கள்",
    "Machines": "இயந்திரங்கள்",
    "Sections": "பிரிவுகள்",
    "Active part numbers": "இயங்கும் பாக எண்கள்",
    "Part families": "பாகக் குடும்பங்கள்",
    "Customers": "வாடிக்கையாளர்கள்",
    "Operators on roll": "பணியில் உள்ள இயக்குபவர்கள்",
    "People with a login": "உள்நுழைவு உள்ளவர்கள்",
    "Working days a year": "ஆண்டுக்கு வேலை நாட்கள்",
    "Currency": "நாணயம்",
    "Running since": "இயங்கத் தொடங்கியது",
}


# ---------------------------------------------------------------------------
# Hindi (हिन्दी)
# ---------------------------------------------------------------------------

HI: dict[str, str] = {
    # --- the refusal ---------------------------------------------------------
    "Sorry, I do not have an answer for this question. Please ask me a "
    "professional or core-based question.": (
        "क्षमा करें, इस सवाल का जवाब मेरे पास नहीं है। कृपया प्लांट या कंपनी से "
        "जुड़ा सवाल पूछें।"
    ),
    # --- answer sentences ----------------------------------------------------
    "No data recorded for that period": "उस अवधि का कोई रिकॉर्ड दर्ज नहीं है",
    "{station} has {occurrences} {reason} events in the last 4 weeks costing "
    "{cost} — roughly one every {interval_days} days": (
        "{station} पर पिछले 4 हफ़्तों में {occurrences} {reason} घटनाएँ हुईं, "
        "कुल {cost} — यानी लगभग हर {interval_days} दिन में एक"
    ),
    "Top defects: {defects}": "मुख्य दोष: {defects}",
    "{count} action(s) overdue, biggest {cost}": (
        "{count} कार्रवाई समय पार, सबसे बड़ी {cost}"
    ),
    "Biggest single item: {reason} on {station} ({cost})": (
        "सबसे बड़ा अकेला नुकसान: {station} पर {reason} ({cost})"
    ),
    "Last time ({when}): {fix}": "पिछली बार ({when}): {fix}",
    "Last time ({when}): {fix} — check {check_first} first": (
        "पिछली बार ({when}): {fix} — पहले {check_first} जाँचें"
    ),
    # --- limits (what the data cannot answer) --------------------------------
    "Factory Memory has no recorded fix matching this.": (
        "इससे मिलता-जुलता कोई पुराना समाधान Factory Memory में दर्ज नहीं है।"
    ),
    "Only {shifts} shifts of history exist, so pattern questions cannot be "
    "answered yet. I have seen {events} events like this; I'll give you "
    "pattern analysis once I've seen more.": (
        "सिर्फ़ {shifts} शिफ़्टों का रिकॉर्ड है, इसलिए पैटर्न वाले सवालों का जवाब "
        "अभी नहीं दिया जा सकता। इस तरह की {events} घटनाएँ ही देखी हैं; और "
        "रिकॉर्ड जमा होते ही पैटर्न विश्लेषण दूँगा।"
    ),
    "{station} has no recurring pattern in the last 4 weeks — nothing repeated "
    "three or more times.": (
        "पिछले 4 हफ़्तों में {station} पर कोई दोहराव वाला पैटर्न नहीं है — कुछ भी "
        "तीन या उससे ज़्यादा बार नहीं हुआ।"
    ),
    "That question was not understood, so this is today's position rather than "
    "an answer to it. Losses for a period, defects and quality, why a machine "
    "keeps stopping, what fixed something last time, and the company's own "
    "details can all be asked for by name.": (
        "यह सवाल समझ नहीं आया, इसलिए यह उसका जवाब नहीं बल्कि आज की स्थिति है। "
        "किसी अवधि का नुकसान, दोष और गुणवत्ता, कोई मशीन बार-बार क्यों रुकती है, "
        "पिछली बार किस चीज़ ने ठीक किया, और कंपनी के अपने ब्यौरे — ये सब नाम "
        "लेकर पूछे जा सकते हैं।"
    ),
    "Not enough history for pattern analysis (needs about 4 weeks).": (
        "पैटर्न विश्लेषण के लिए पर्याप्त रिकॉर्ड नहीं है (लगभग 4 हफ़्ते चाहिए)।"
    ),
    # --- fact labels ---------------------------------------------------------
    "Station": "स्टेशन",
    "Period": "अवधि",
    "Recoverable loss": "वसूली योग्य नुकसान",
    "Previous period loss": "पिछली अवधि का नुकसान",
    "Change": "बदलाव",
    "Today's recoverable loss": "आज का वसूली योग्य नुकसान",
    "Today's downtime (minutes)": "आज का बंद रहने का समय (मिनट)",
    "Yesterday's recoverable loss": "कल का वसूली योग्य नुकसान",
    "Yesterday's structural loss": "कल का ढाँचागत नुकसान",
    "Yesterday's downtime (minutes)": "कल का बंद रहने का समय (मिनट)",
    "Shifts of history": "दर्ज शिफ़्टें",
    "Days live": "चालू दिन",
    "Period (days)": "अवधि (दिन)",
    "Good parts": "सही पुर्ज़े",
    "Rejected parts": "रिजेक्ट पुर्ज़े",
    "Company": "कंपनी",
    "Plant code": "प्लांट कोड",
    "Location": "जगह",
    "Shifts": "शिफ़्टें",
    "Machines": "मशीनें",
    "Sections": "सेक्शन",
    "Active part numbers": "चालू पार्ट नंबर",
    "Part families": "पार्ट फ़ैमिली",
    "Customers": "ग्राहक",
    "Operators on roll": "रोल पर ऑपरेटर",
    "People with a login": "लॉगिन वाले लोग",
    "Working days a year": "साल में काम के दिन",
    "Currency": "मुद्रा",
    "Running since": "कब से चल रहा है",
}


DICTIONARIES: dict[str, dict[str, str]] = {"ta": TA, "hi": HI}
