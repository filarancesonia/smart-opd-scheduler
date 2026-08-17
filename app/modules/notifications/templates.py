"""Room 6 message templates, in Hindi and English.

Written to be understood when read aloud by a voice call, not just when read on
a screen. Every message names the booking reference, the room and the time,
because that is what someone standing at a hospital gate actually needs.
"""

from __future__ import annotations

from app.modules.notifications.models import TemplateCode

#: {code: {language: text}} — formatted with str.format(**context).
TEMPLATES: dict[str, dict[str, str]] = {
    TemplateCode.BOOKING_CONFIRMED: {
        "hi": (
            "आपका अपॉइंटमेंट तय हो गया है।\n"
            "डॉक्टर: {doctor_name}\n"
            "तारीख: {date} समय: {time}\n"
            "कमरा: {room}\n"
            "बुकिंग नंबर: {reference}\n"
            "कृपया 10 मिनट पहले पहुँचें।"
        ),
        "en": (
            "Your appointment is confirmed.\n"
            "Doctor: {doctor_name}\n"
            "Date: {date} Time: {time}\n"
            "Room: {room}\n"
            "Booking number: {reference}\n"
            "Please arrive 10 minutes early."
        ),
    },
    TemplateCode.REMINDER_DAY_BEFORE: {
        "hi": (
            "याद दिलाने के लिए: कल {time} बजे {doctor_name} के साथ आपका "
            "अपॉइंटमेंट है, कमरा {room}। बुकिंग नंबर {reference}। "
            "यदि आप नहीं आ सकते तो कृपया रद्द कर दें ताकि किसी और को समय मिल सके।"
        ),
        "en": (
            "Reminder: your appointment with {doctor_name} is tomorrow at {time}, "
            "room {room}. Booking number {reference}. "
            "If you cannot come, please cancel so someone else can take the slot."
        ),
    },
    TemplateCode.TURN_SOON: {
        "hi": (
            "आपकी बारी लगभग {minutes} मिनट में है। "
            "कृपया कमरा {room} के पास पहुँच जाएँ। टोकन नंबर {token}।"
        ),
        "en": (
            "Your turn is in about {minutes} minutes. "
            "Please come near room {room}. Token number {token}."
        ),
    },
    TemplateCode.NOW_CALLING: {
        "hi": "टोकन {token} — कृपया अभी कमरा {room} में जाएँ।",
        "en": "Token {token} — please go to room {room} now.",
    },
    TemplateCode.APPOINTMENT_CANCELLED: {
        "hi": (
            "आपका {date} का अपॉइंटमेंट (बुकिंग नंबर {reference}) रद्द कर दिया गया है। "
            "कारण: {reason}। नया समय लेने के लिए कृपया दोबारा बुक करें।"
        ),
        "en": (
            "Your appointment on {date} (booking number {reference}) has been "
            "cancelled. Reason: {reason}. Please book again for a new time."
        ),
    },
    TemplateCode.APPOINTMENT_RESCHEDULED: {
        "hi": (
            "आपका अपॉइंटमेंट बदल दिया गया है।\n"
            "नई तारीख: {date} समय: {time}\n"
            "कमरा: {room}\n"
            "नया बुकिंग नंबर: {reference}"
        ),
        "en": (
            "Your appointment has been moved.\n"
            "New date: {date} Time: {time}\n"
            "Room: {room}\n"
            "New booking number: {reference}"
        ),
    },
    TemplateCode.DOCTOR_DELAYED: {
        "hi": (
            "सूचना: {doctor_name} को पहुँचने में देरी हो रही है। "
            "आपका अनुमानित समय अब लगभग {minutes} मिनट बाद है। "
            "आप चाहें तो बाद में आ सकते हैं — आपका टोकन {token} सुरक्षित है।"
        ),
        "en": (
            "Notice: {doctor_name} is running late. "
            "Your estimated time is now about {minutes} minutes away. "
            "You may come later if you wish — your token {token} is safe."
        ),
    },
    TemplateCode.DOCTOR_UNAVAILABLE: {
        "hi": (
            "खेद है, {date} को {doctor_name} उपलब्ध नहीं होंगे। "
            "आपका अपॉइंटमेंट (बुकिंग नंबर {reference}) रद्द कर दिया गया है। "
            "कृपया दूसरी तारीख के लिए बुक करें। आपको प्राथमिकता दी जाएगी।"
        ),
        "en": (
            "We are sorry — {doctor_name} will not be available on {date}. "
            "Your appointment (booking number {reference}) has been cancelled. "
            "Please book another date; you will be given priority."
        ),
    },
}

#: Voice calls cannot render newlines, so they get a flattened variant.
VOICE_SAFE_CODES = {
    TemplateCode.BOOKING_CONFIRMED,
    TemplateCode.APPOINTMENT_RESCHEDULED,
}


def render(code: str, language: str, context: dict) -> str:
    """Render a template, falling back to English then to the code itself."""
    variants = TEMPLATES.get(code)
    if not variants:
        return f"[{code}]"

    text = variants.get(language) or variants.get("en") or next(iter(variants.values()))
    try:
        return text.format(**context)
    except KeyError as exc:
        # A missing placeholder must not lose the message entirely.
        return text.replace("{" + str(exc.args[0]) + "}", "—").format(
            **{**{k: "—" for k in _placeholders(text)}, **context}
        )


def _placeholders(text: str) -> set[str]:
    import string

    return {
        field
        for _, field, _, _ in string.Formatter().parse(text)
        if field is not None
    }


def flatten_for_voice(text: str) -> str:
    """Collapse a multi-line message into something a TTS engine reads well."""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())
