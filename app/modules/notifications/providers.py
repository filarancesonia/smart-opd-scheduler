"""Room 6 delivery drivers.

The console driver is the default and is what runs in development, in tests and
in any demo. Real gateways (an SMS aggregator, WhatsApp Business, a TTS voice
provider) plug in behind the same two-method interface.

No driver here contacts an external service without credentials being
configured, so cloning this repository cannot cause a message to be sent to a
real phone.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger("notifications")


@dataclass
class DeliveryResult:
    ok: bool
    provider_message_id: str | None = None
    error: str = ""


class Provider:
    name = "base"

    def send(self, recipient: str, body: str) -> DeliveryResult:  # pragma: no cover
        raise NotImplementedError


class ConsoleProvider(Provider):
    """Writes the message to the log instead of sending it.

    Deliberately reports success: the delivery pipeline, retry logic and audit
    trail are all exercised without a single real message leaving the machine.
    """

    name = "console"

    def __init__(self, channel: str = "generic"):
        self.channel = channel

    def send(self, recipient: str, body: str) -> DeliveryResult:
        logger.info(
            "[%s -> %s]\n%s", self.channel.upper(), _mask_phone(recipient), body
        )
        return DeliveryResult(ok=True, provider_message_id=f"console-{uuid.uuid4().hex[:12]}")


class UnconfiguredProvider(Provider):
    """Stands in for a real gateway whose credentials are not set.

    Fails loudly and permanently rather than pretending to have delivered.
    """

    def __init__(self, name: str):
        self.name = name

    def send(self, recipient: str, body: str) -> DeliveryResult:
        return DeliveryResult(
            ok=False,
            error=(
                f"Provider '{self.name}' is selected but not configured. "
                "Set its credentials or switch the channel back to 'console'."
            ),
        )


def _mask_phone(value: str) -> str:
    """Never write a full phone number to a log file."""
    digits = "".join(c for c in value if c.isdigit())
    return f"******{digits[-4:]}" if len(digits) >= 4 else "******"


#: Channel -> configured provider name, read from settings.
def _provider_for(channel: str) -> Provider:
    configured = {
        "sms": settings.sms_provider,
        "whatsapp": settings.whatsapp_provider,
        "voice": settings.voice_provider,
        "push": "console",
    }.get(channel, "console")

    if configured == "console":
        return ConsoleProvider(channel)
    return UnconfiguredProvider(configured)


def get_provider(channel: str) -> Provider:
    return _provider_for(channel)
