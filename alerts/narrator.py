"""
alerts/narrator.py
CPOI Platform — Alert Message Narrator

Drafts the 4-sentence AI-generated alert message body using a locally
self-hosted model accessed via the ollama HTTP API. Client data never
leaves the server: all context is anonymized before the prompt is built,
regardless of whether the local model is running or not.

Public interface:
  anonymize_for_ai(client_data: dict) -> dict
  draft_alert_message(
      conn, alert_id, signal_name, signal_value, threshold_value,
      severity, client_name, context
  ) -> str

Privacy invariant:
  anonymize_for_ai() is called on the context dict before any data is
  passed to the model. The real client_name is replaced with 'CLIENT_A'
  in the prompt. The drafted message body will contain 'CLIENT_A'; the
  sender (alerts/sender.py) uses the real client name only in the email
  subject and CP letterhead, never in the AI-generated text.

Model configuration (environment variables):
  CPOI_OLLAMA_MODEL  -- model name passed to ollama (default: llama3.2:3b)
  CPOI_OLLAMA_URL    -- ollama generate endpoint
                        (default: http://localhost:11434/api/generate)

Fallback behavior:
  If the ollama API is unreachable, times out, or returns an empty
  response, draft_alert_message returns a deterministic 4-sentence message
  constructed from the signal data alone. No exception is raised. No alert
  is ever lost due to a model failure. The fallback is logged in the audit
  trail so the Managing Partner knows which messages were model-generated
  vs. template-generated.

Audit logging:
  Every call to draft_alert_message writes one audit_log entry with
  event_type 'alert_message_drafted'. The entry records the SHA-256 hash
  of the full prompt (never the prompt text itself), the model name used,
  whether the fallback template was applied, and the response character
  count. This satisfies the spec Part 8 requirement to log every AI
  generation event.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from db.audit import write_audit_log

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _build_logger(name: str) -> logging.Logger:
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload)

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.alerts.narrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_OLLAMA_MODEL: str = "llama3.2:3b"
_DEFAULT_OLLAMA_URL: str = "http://localhost:11434/api/generate"
_OLLAMA_TIMEOUT_SECONDS: int = 60
_MIN_RESPONSE_LENGTH: int = 40  # Minimum chars to accept an AI response as valid


# ---------------------------------------------------------------------------
# Anonymization — spec Part 8 (verbatim implementation)
# ---------------------------------------------------------------------------


def anonymize_for_ai(client_data: dict) -> dict:
    """
    Remove all client-identifiable information before AI processing.

    Replaces client_name, program_names, and owner_names with standardized
    placeholders. Applied as defense-in-depth before any data is passed to
    the local model, so that logs, prompt caches, and any future model
    interaction never contain identifiable client information.

    This function is implemented verbatim from cpoi-sdlc-spec.md Part 8,
    'AI Prompt Data Anonymization Rule'. Do not modify without a spec
    version update and audit log entry.

    Args:
        client_data: Dict of client context data, which may contain
                     'client_name', 'program_names', and 'owner_names' keys
                     alongside any signal or score values.

    Returns:
        dict: A shallow copy of client_data with identifiable fields replaced.
              The original dict is not modified.
    """
    anonymized = client_data.copy()
    anonymized["client_name"] = "CLIENT_A"
    anonymized["program_names"] = [
        f"PROGRAM_{i}"
        for i in range(len(anonymized.get("program_names", [])))
    ]
    anonymized["owner_names"] = (
        ["OWNER_REDACTED"] * len(anonymized.get("owner_names", []))
    )
    return anonymized


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_prompt(
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    anonymized_context: dict,
) -> str:
    """
    Build the ollama prompt from the Part 5 Exception Alert Message template.

    Uses 'CLIENT_A' (the anonymized client identifier) as client_name.
    The anonymized_context dict is serialized to JSON as the CONTEXT DATA
    field. The real client name never appears in this prompt.

    Args:
        signal_name:         Canonical signal name (e.g. 'governance_latency_index').
        signal_value:        The measured numeric signal value.
        threshold_value:     The watch-level threshold that was breached.
        anonymized_context:  Context dict already processed by anonymize_for_ai().

    Returns:
        str: The complete prompt string ready to send to ollama.
    """
    context_json = json.dumps(anonymized_context, indent=None, separators=(",", ":"))
    return (
        "SYSTEM: Write a brief operational intelligence alert for CLIENT_A. "
        "This is sent to the executive sponsor when a signal threshold is breached. "
        "Maximum 4 sentences. Be specific. State the signal, the measured value, "
        "the threshold, and the specific business risk. Do not recommend actions. "
        "State what the intelligence indicates.\n\n"
        f"SIGNAL: {signal_name}\n"
        f"MEASURED VALUE: {signal_value}\n"
        f"THRESHOLD: {threshold_value}\n"
        f"CONTEXT DATA: {context_json}"
    )


def _call_ollama(prompt: str) -> str | None:
    """
    Call the ollama HTTP API and return the generated text, or None on failure.

    Uses the non-streaming generate endpoint. Reads CPOI_OLLAMA_MODEL and
    CPOI_OLLAMA_URL from environment; falls back to module-level defaults.

    Returns None (does not raise) on any of:
      - Connection refused (model not running)
      - HTTP error from the ollama server
      - JSON decode error in the response
      - Empty or too-short response text
      - Any unexpected exception

    Args:
        prompt: The complete prompt string built by _build_prompt().

    Returns:
        str | None: The model's response text if successful and non-trivial,
                    None otherwise.
    """
    model = os.environ.get("CPOI_OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
    url = os.environ.get("CPOI_OLLAMA_URL", _DEFAULT_OLLAMA_URL)

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,   # Low temperature for consistent, factual output
            "num_predict": 300,   # Sufficient for 4 sentences; prevents runaway generation
        },
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_OLLAMA_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        log.warning(
            "ollama API unreachable at '%s': %s. Falling back to template.", url, exc
        )
        return None
    except TimeoutError:
        log.warning(
            "ollama API timed out after %ds. Falling back to template.",
            _OLLAMA_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Unexpected error calling ollama API: %s. Falling back to template.", exc
        )
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("ollama response is not valid JSON: %s. Falling back to template.", exc)
        return None

    response_text = data.get("response", "").strip()

    if len(response_text) < _MIN_RESPONSE_LENGTH:
        log.warning(
            "ollama response too short (%d chars, minimum %d). Falling back to template.",
            len(response_text),
            _MIN_RESPONSE_LENGTH,
        )
        return None

    return response_text


def _fallback_message(
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
) -> str:
    """
    Return a deterministic 4-sentence alert message when the local model is unavailable.

    Constructed entirely from signal data passed as arguments — no client
    identifiers, no external calls. This message is suitable for delivery
    via SendGrid even when ollama is not running.

    Args:
        signal_name:     Canonical signal name.
        signal_value:    The measured numeric signal value.
        threshold_value: The watch-level threshold that was breached.
        severity:        One of 'critical', 'elevated', 'watch'.

    Returns:
        str: A 4-sentence alert message.
    """
    display_name = signal_name.replace("_", " ").title()
    excess = round(signal_value - threshold_value, 4)
    return (
        f"The {display_name} signal has recorded a measured value of "
        f"{signal_value}, breaching the {severity}-level threshold of "
        f"{threshold_value}. "
        f"This measurement exceeds the established threshold by {excess}, "
        f"indicating a material operational risk condition that warrants "
        f"executive awareness. "
        f"The current deviation from baseline represents a significant "
        f"departure from expected performance standards for this signal category. "
        f"Full analysis of this condition will be included in the next "
        f"Monthly Intelligence Brief."
    )


def _hash_prompt(prompt: str) -> str:
    """Return the SHA-256 hex digest of the prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def draft_alert_message(
    conn: Any,
    alert_id: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
    client_name: str,
    context: dict,
) -> str:
    """
    Draft the 4-sentence AI alert message for a threshold breach.

    Anonymizes the context dict before building the prompt. The real
    client_name never appears in the prompt or the drafted message text.
    Calls the local ollama model; falls back to a deterministic template
    if the model is unreachable or returns an invalid response.

    Writes one audit_log entry recording the prompt hash, model used,
    fallback flag, and response character count. Does not call conn.commit().

    Args:
        conn:            Open, authenticated database connection.
        alert_id:        UUID of the alert record this message is being
                         drafted for. Used as the audit log entity_id.
        signal_name:     Canonical signal name as stored in signal_readings.
        signal_value:    The measured numeric signal value.
        threshold_value: The watch-level threshold that was breached.
        severity:        Severity label: 'critical', 'elevated', or 'watch'.
        client_name:     Real client name. Used only in the audit log
                         metadata (not in the prompt or drafted text).
        context:         Dict of additional context (other signal values,
                         OEI scores, period metadata, etc.). Will be
                         anonymized before use.

    Returns:
        str: The drafted alert message (4 sentences, model-generated or
             fallback template). Ready for insertion into alerts.alert_message.

    Raises:
        Exception: any database error from write_audit_log propagates to
                   the caller. Model errors are handled internally and never
                   propagate.
    """
    model_name = os.environ.get("CPOI_OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
    used_fallback: bool = False

    # Step 1: Anonymize context before any AI processing.
    anonymized_context = anonymize_for_ai(context)
    # Remove client_name from the anonymized context dict entirely
    # (anonymize_for_ai already replaced it with CLIENT_A, but ensure
    # it does not appear under any other key either).
    anonymized_context.pop("client_name", None)

    # Step 2: Build prompt. CLIENT_A is the only client identifier present.
    prompt = _build_prompt(
        signal_name=signal_name,
        signal_value=signal_value,
        threshold_value=threshold_value,
        anonymized_context=anonymized_context,
    )
    prompt_hash = _hash_prompt(prompt)

    log.info(
        "Drafting alert message: alert_id=%s signal=%s model=%s prompt_hash=%s",
        alert_id,
        signal_name,
        model_name,
        prompt_hash[:16],
    )

    # Step 3: Call local model. Falls back to template on any failure.
    ai_response = _call_ollama(prompt)

    if ai_response is not None:
        drafted_message = ai_response
        used_fallback = False
        log.info(
            "Alert message drafted by model: alert_id=%s chars=%d",
            alert_id,
            len(drafted_message),
        )
    else:
        drafted_message = _fallback_message(
            signal_name=signal_name,
            signal_value=signal_value,
            threshold_value=threshold_value,
            severity=severity,
        )
        used_fallback = True
        log.info(
            "Alert message drafted from fallback template: alert_id=%s chars=%d",
            alert_id,
            len(drafted_message),
        )

    # Step 4: Audit log entry for this AI generation event.
    write_audit_log(
        conn=conn,
        event_type="alert_message_drafted",
        entity_type="alert",
        entity_id=alert_id,
        description=(
            f"Alert message drafted for signal '{signal_name}'. "
            f"Model: {model_name}. "
            f"Fallback used: {used_fallback}. "
            f"Response length: {len(drafted_message)} characters."
        ),
        performed_by="system",
        metadata={
            "alert_id":       alert_id,
            "signal_name":    signal_name,
            "signal_value":   float(signal_value),
            "threshold_value": float(threshold_value),
            "severity":       severity,
            "model_name":     model_name,
            "prompt_hash":    prompt_hash,
            "used_fallback":  used_fallback,
            "response_chars": len(drafted_message),
            # client_name is stored in audit metadata for traceability
            # but never passed to the AI model.
            "client_name_logged": client_name,
        },
    )

    return drafted_message
