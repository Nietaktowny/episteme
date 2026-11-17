"""Sending alerts to where somebody will see them.

A *sink* (:class:`~episteme.settings.SinkSettings`) is a command or a webhook. :func:`deliver` sends one alert to
one sink and raises if that did not work; deciding which alerts go to which sink, and remembering what was sent, is
the job of :meth:`episteme.Episteme.deliver_alerts` and the event log.

The alert is sent as JSON: everything in :class:`~episteme.models.health.Alert`, which says which note, which rule,
how severe and what to do. It names notes, so a webhook should be ``https`` and a command should be one you trust.
"""

import json
import subprocess
import urllib.error
import urllib.request

from episteme.models.health import Alert
from episteme.settings import SinkSettings


class DeliveryError(RuntimeError):
    """An alert could not be delivered."""


def payload(alert: Alert) -> str:
    """The text a sink receives: the alert as JSON.

    Examples:
        >>> from datetime import datetime
        >>> alert = Alert(violation_id="v", rule_id="R9", rule_name="unresolved_lane", severity="error", severity_rank=3,
        ...               entity_id="p.md", opened_at=datetime(2026, 1, 1), days_open=0, description="d", remediation="r")
        >>> json.loads(payload(alert))["rule_id"]
        'R9'
    """
    return alert.model_dump_json()


def deliver(sink: SinkSettings, alert: Alert) -> None:
    """Send one alert to one sink.

    Raises:
        DeliveryError: If the command fails or times out, or the webhook cannot be reached or answers with an error.
            The message never contains the url's query, headers or the alert itself.
    """
    body = payload(alert)
    if sink.kind == "command":
        try:
            done = subprocess.run(sink.command, input=body, text=True, capture_output=True, timeout=sink.timeout, check=False)  # noqa: S603 - no shell, the user's own command
        except (OSError, subprocess.SubprocessError) as error:
            raise DeliveryError(f"{type(error).__name__}: {error}") from None
        if done.returncode != 0:
            raise DeliveryError(f"the command exited with {done.returncode}: {done.stderr.strip()[:300]}")
        return
    request = urllib.request.Request(  # noqa: S310 - http(s) only, checked by the settings
        sink.url, data=body.encode(), method="POST", headers={"Content-Type": "application/json", **sink.headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=sink.timeout) as response:  # noqa: S310
            if response.status >= 300:
                raise DeliveryError(f"the webhook answered {response.status}")
    except urllib.error.HTTPError as error:
        raise DeliveryError(f"the webhook answered {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise DeliveryError(f"the webhook could not be reached: {type(error).__name__}") from None
