"""
alerts/queue.py
CPOI Platform — Alert Send Queue and Circuit Breaker

Processes the unsent alert queue with circuit breaker protection against
SendGrid outages. Ensures no alert is ever silently dropped: if SendGrid
is unavailable, the alert remains in the queue (email_sent = FALSE) and
will be retried on the next call to process_send_queue().

Public interface:
  CircuitBreaker         -- in-memory state machine; instantiate once and
                            pass to process_send_queue() for continuity
                            across repeated queue runs
  process_send_queue(
      conn, recipient_email, circuit_breaker=None
  ) -> dict[str, int]

Circuit breaker behavior:
  State machine: closed -> open -> half_open -> closed
    closed:    Normal operation. Sends proceed.
    open:      SendGrid is considered unavailable. All sends are skipped.
               The queue retains all unsent alerts. No alert is dropped.
    half_open: Cooldown elapsed. One send attempt is made. Success ->
               closed. Failure -> open (resets cooldown).

  Thresholds:
    FAILURE_THRESHOLD  = 3   consecutive failures -> open
    COOLDOWN_SECONDS   = 300 seconds (5 minutes) before open -> half_open

  State transitions are logged to audit_log with event_type
  'circuit_breaker_state_change'.

Commit behavior:
  process_send_queue() commits after every successful send individually.
  This is the one function in the platform that owns its own commits,
  because each delivered email is an irreversible external action. A
  mid-queue crash must not undo commits for emails already delivered.
  The circuit breaker state change audit log entries are committed with
  the transaction they accompany (send success or failure record).

Draft staging:
  If an alert's alert_message column is NULL (i.e., the alert was just
  created by the detector and has not yet been through the narrator),
  process_send_queue() calls draft_alert_message() and writes the result
  to alerts.alert_message before attempting the send. Retries therefore
  always use the same drafted message rather than re-querying the model.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Final

from alerts.narrator import draft_alert_message
from alerts.sender import send_alert_email
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


log = _build_logger("cpoi.alerts.queue")

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

_STATE_CLOSED: Final[str] = "closed"
_STATE_OPEN: Final[str] = "open"
_STATE_HALF_OPEN: Final[str] = "half_open"


class CircuitBreaker:
    """
    In-memory circuit breaker protecting the SendGrid send path.

    Instantiate once per session and pass to process_send_queue() so
    failure counts accumulate across repeated queue runs. If a new
    instance is created on each call, the breaker resets and consecutive
    failure counts are lost.

    Thread safety: this class is not thread-safe. CPOI v1 is single-user
    and single-threaded; no locking is applied.

    Attributes:
        FAILURE_THRESHOLD: Consecutive failures required to trip to open.
        COOLDOWN_SECONDS:  Seconds in open state before half_open is tested.
    """

    FAILURE_THRESHOLD: Final[int] = 3
    COOLDOWN_SECONDS: Final[int] = 300

    def __init__(self) -> None:
        self._state: str = _STATE_CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float | None = None  # time.monotonic() when tripped

    @property
    def state(self) -> str:
        """
        Return the current circuit breaker state.

        Lazily transitions open -> half_open when the cooldown period has
        elapsed. Callers should always use this property rather than
        reading _state directly.

        Returns:
            str: One of 'closed', 'open', 'half_open'.
        """
        if self._state == _STATE_OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.COOLDOWN_SECONDS:
                self._state = _STATE_HALF_OPEN
                log.info(
                    "Circuit breaker: open -> half_open "
                    "(cooldown of %ds elapsed after %.0fs).",
                    self.COOLDOWN_SECONDS,
                    elapsed,
                )
        return self._state

    def is_open(self) -> bool:
        """
        Return True if the circuit is open (sends must not be attempted).

        Returns True for both 'open' and 'half_open' states. In half_open,
        only process_send_queue() decides whether to allow a probe attempt;
        callers that use this method for a simple gate will be blocked until
        a successful probe resets the breaker.

        Returns:
            bool: True when state is 'open'. False when 'closed' or
                  'half_open' (half_open allows one probe attempt).
        """
        return self.state == _STATE_OPEN

    def allow_probe(self) -> bool:
        """
        Return True if a single probe attempt is allowed (half_open state).

        Returns:
            bool: True when state is 'half_open'.
        """
        return self.state == _STATE_HALF_OPEN

    def record_success(self) -> str | None:
        """
        Record a successful send and reset the breaker.

        Transitions half_open -> closed and open/closed -> closed.

        Returns:
            str | None: 'closed' if the state changed to closed (caller
                        should log the transition). None if already closed.
        """
        prev = self._state
        self._consecutive_failures = 0
        self._state = _STATE_CLOSED
        self._opened_at = None
        if prev != _STATE_CLOSED:
            log.info(
                "Circuit breaker: %s -> closed (successful send).", prev
            )
            return _STATE_CLOSED
        return None

    def record_failure(self) -> str | None:
        """
        Record a failed send and increment the consecutive failure counter.

        Transitions closed/half_open -> open when FAILURE_THRESHOLD is
        reached. Resets the cooldown timer on each trip.

        Returns:
            str | None: 'open' if the state just transitioned to open
                        (caller should log the transition). None if the
                        threshold has not yet been reached.
        """
        self._consecutive_failures += 1
        log.warning(
            "Circuit breaker: send failure recorded "
            "(consecutive_failures=%d, threshold=%d).",
            self._consecutive_failures,
            self.FAILURE_THRESHOLD,
        )
        if self._consecutive_failures >= self.FAILURE_THRESHOLD:
            if self._state != _STATE_OPEN:
                prev = self._state
                self._state = _STATE_OPEN
                self._opened_at = time.monotonic()
                log.error(
                    "Circuit breaker: %s -> open "
                    "(threshold of %d consecutive failures reached).",
                    prev,
                    self.FAILURE_THRESHOLD,
                )
                return _STATE_OPEN
        return None

    @property
    def consecutive_failures(self) -> int:
        """Return the current consecutive failure count."""
        return self._consecutive_failures


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_state_transition(
    conn: Any,
    alert_id: str | None,
    previous_state: str,
    new_state: str,
    reason: str,
) -> None:
    """
    Write a circuit_breaker_state_change entry to audit_log.

    Args:
        conn:           Open database connection.
        alert_id:       Alert that triggered the transition, or None.
        previous_state: State before transition.
        new_state:      State after transition.
        reason:         Human-readable reason for the transition.
    """
    write_audit_log(
        conn=conn,
        event_type="circuit_breaker_state_change",
        entity_type="alert",
        entity_id=alert_id,
        description=(
            f"Circuit breaker state transition: {previous_state} -> {new_state}. "
            f"Reason: {reason}."
        ),
        performed_by="system",
        metadata={
            "previous_state": previous_state,
            "new_state":      new_state,
            "reason":         reason,
            "alert_id":       alert_id,
        },
    )


def _ensure_alert_message_drafted(
    conn: Any,
    alert_id: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
    client_id: str,
    client_name: str,
) -> str:
    """
    Return the drafted alert message for an alert, drafting it if necessary.

    If the alert's alert_message column is NULL (alert was just created by
    the detector and has not yet been through the narrator), calls
    draft_alert_message() and writes the result to alerts.alert_message.
    This ensures retries use the same drafted text rather than re-querying
    the local model on every retry.

    Does not commit.

    Args:
        conn:            Open database connection.
        alert_id:        UUID of the alert record.
        signal_name:     Signal name for the narrator prompt.
        signal_value:    Measured signal value.
        threshold_value: Watch-level breach threshold.
        severity:        One of 'critical', 'elevated', 'watch'.
        client_id:       Client UUID (used for narrator context).
        client_name:     Real client name (passed to narrator).

    Returns:
        str: The drafted alert message (model-generated or fallback).
    """
    row = conn.execute(
        "SELECT alert_message FROM alerts WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()

    existing_message = row[0] if row else None

    if existing_message:
        return existing_message

    # Draft a new message via the narrator.
    log.info(
        "alert_message is NULL for alert_id=%s; drafting now.", alert_id
    )
    context: dict = {
        "client_name": client_name,
        "signal_value": float(signal_value),
        "threshold_value": float(threshold_value),
        "severity": severity,
    }
    drafted = draft_alert_message(
        conn=conn,
        alert_id=alert_id,
        signal_name=signal_name,
        signal_value=float(signal_value),
        threshold_value=float(threshold_value),
        severity=severity,
        client_name=client_name,
        context=context,
    )

    # Persist the drafted message so retries use the same text.
    conn.execute(
        "UPDATE alerts SET alert_message = ? WHERE alert_id = ?",
        (drafted, alert_id),
    )

    log.info(
        "alert_message drafted and saved for alert_id=%s (%d chars).",
        alert_id,
        len(drafted),
    )
    return drafted


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def process_send_queue(
    conn: Any,
    recipient_email: str,
    circuit_breaker: CircuitBreaker | None = None,
) -> dict[str, int]:
    """
    Process all unsent alerts through the send queue with circuit breaker
    protection.

    Iterates alerts where email_sent = FALSE in triggered_at order. For
    each alert: checks the circuit breaker, ensures the message has been
    drafted, then attempts to send via send_alert_email(). On success,
    commits immediately. On failure, records the failure with the circuit
    breaker; if the breaker trips open, logs the state change to audit_log.

    Unlike all other functions in this platform, process_send_queue() owns
    its own commits. Each successful send is committed as a discrete unit
    because email delivery is irreversible. A crash after send but before
    commit must not result in a second delivery. The email_sent = TRUE
    update prevents duplicate sends even if the commit is delayed; the
    idempotency guard in send_alert_email() provides a second layer.

    Args:
        conn:             Open, authenticated database connection.
        recipient_email:  Destination email address for all alerts in this
                          queue run. In v1, this is the configured executive
                          sponsor email.
        circuit_breaker:  Optional CircuitBreaker instance. If None, a new
                          instance is created for this run. Pass an existing
                          instance to preserve failure counts across repeated
                          calls.

    Returns:
        dict[str, int]: Counts keyed by outcome.
          'sent':                 Alerts successfully delivered via SendGrid.
          'skipped_circuit_open': Alerts skipped because circuit was open.
          'failed':               Alerts that raised a RuntimeError from
                                  send_alert_email(). Remain email_sent=FALSE
                                  in the database for retry.
          'already_sent':         Alerts found with email_sent=TRUE (covered
                                  by idempotency; should be zero in normal
                                  operation since the query filters them out).
          'draft_errors':         Alerts where message drafting itself raised
                                  an unexpected exception. Remain in queue.
    """
    if circuit_breaker is None:
        circuit_breaker = CircuitBreaker()

    # ------------------------------------------------------------------
    # Fetch all unsent alerts ordered by triggered_at (oldest first).
    # ------------------------------------------------------------------
    rows = conn.execute(
        """
        SELECT
            a.alert_id,
            a.signal_name,
            a.signal_value,
            a.threshold_value,
            a.alert_severity,
            a.alert_message,
            a.client_id,
            a.submission_id
        FROM alerts a
        WHERE a.email_sent = FALSE
        ORDER BY a.triggered_at ASC
        """,
    ).fetchall()

    results: dict[str, int] = {
        "sent":                 0,
        "skipped_circuit_open": 0,
        "failed":               0,
        "already_sent":         0,
        "draft_errors":         0,
    }

    if not rows:
        log.info("Send queue is empty. Nothing to process.")
        return results

    log.info(
        "Processing send queue: %d unsent alert(s), circuit_state=%s.",
        len(rows),
        circuit_breaker.state,
    )

    total = len(rows)
    for row_index, (
        alert_id, signal_name, signal_value, threshold_value,
        severity, alert_message, client_id, submission_id,
    ) in enumerate(rows):

        # ------------------------------------------------------------------
        # Check circuit breaker. Skip if open (not half_open — that allows
        # a probe attempt, handled below by letting the send proceed).
        # ------------------------------------------------------------------
        if circuit_breaker.is_open():
            log.warning(
                "Circuit breaker is open. Skipping alert_id=%s.", alert_id
            )
            results["skipped_circuit_open"] += 1
            continue

        is_probe = circuit_breaker.allow_probe()
        if is_probe:
            log.info(
                "Circuit breaker in half_open: probe attempt for alert_id=%s.",
                alert_id,
            )

        # ------------------------------------------------------------------
        # Resolve client name.
        # ------------------------------------------------------------------
        client_row = conn.execute(
            "SELECT client_name FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        client_name: str = client_row[0] if client_row else "Unknown Client"

        # ------------------------------------------------------------------
        # Ensure the alert message has been drafted.
        # ------------------------------------------------------------------
        try:
            drafted_message = _ensure_alert_message_drafted(
                conn=conn,
                alert_id=alert_id,
                signal_name=signal_name,
                signal_value=float(signal_value),
                threshold_value=float(threshold_value),
                severity=severity,
                client_id=client_id,
                client_name=client_name,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Failed to draft message for alert_id=%s: %s. "
                "Alert remains in queue.",
                alert_id,
                exc,
                exc_info=True,
            )
            results["draft_errors"] += 1
            continue

        # ------------------------------------------------------------------
        # Attempt to send the email.
        # ------------------------------------------------------------------
        try:
            sent = send_alert_email(
                conn=conn,
                alert_id=alert_id,
                recipient_email=recipient_email,
                client_name=client_name,
                drafted_message=drafted_message,
            )
        except RuntimeError as exc:
            log.error(
                "Send failed for alert_id=%s: %s. Alert remains in queue.",
                alert_id,
                exc,
            )
            results["failed"] += 1

            # Record failure with the circuit breaker.
            prev_state = circuit_breaker.state
            new_state = circuit_breaker.record_failure()

            if new_state == _STATE_OPEN:
                _log_state_transition(
                    conn=conn,
                    alert_id=alert_id,
                    previous_state=prev_state,
                    new_state=_STATE_OPEN,
                    reason=(
                        f"{CircuitBreaker.FAILURE_THRESHOLD} consecutive SendGrid "
                        f"failures. Last error: {exc}"
                    ),
                )
                conn.commit()
                log.error(
                    "Circuit breaker tripped to OPEN after alert_id=%s failure. "
                    "Remaining queue entries will be skipped.",
                    alert_id,
                )

            # If the circuit just opened, abort remaining items.
            if circuit_breaker.is_open():
                remaining = total - row_index - 1
                if remaining > 0:
                    results["skipped_circuit_open"] += remaining
                break

            continue

        # ------------------------------------------------------------------
        # Handle return value from send_alert_email.
        # ------------------------------------------------------------------
        if sent:
            results["sent"] += 1

            # Record success. Log state transition if breaker resets.
            prev_state = circuit_breaker.state
            new_state = circuit_breaker.record_success()
            if new_state == _STATE_CLOSED and is_probe:
                _log_state_transition(
                    conn=conn,
                    alert_id=alert_id,
                    previous_state=prev_state,
                    new_state=_STATE_CLOSED,
                    reason="Probe send succeeded. Resuming normal operation.",
                )

            # Commit after every successful send (owns its own commit per
            # module docstring rationale).
            conn.commit()

            log.info(
                "Alert sent and committed: alert_id=%s (%d sent so far).",
                alert_id,
                results["sent"],
            )
        else:
            # send_alert_email returned False: alert was already sent.
            results["already_sent"] += 1
            log.info(
                "Alert already marked sent: alert_id=%s.", alert_id
            )

    log.info(
        "Send queue complete: sent=%d skipped_circuit_open=%d "
        "failed=%d already_sent=%d draft_errors=%d",
        results["sent"],
        results["skipped_circuit_open"],
        results["failed"],
        results["already_sent"],
        results["draft_errors"],
    )
    return results
