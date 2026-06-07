"""
reports/validator.py
CPOI Platform — Report Template Validation

Two validation functions that enforce the Module 5 spec requirement:
  "All template variables populated before render"
  "No AI-generated text inserted without length validation (min 50, max 300
   words per section)"

validate_no_unfilled_placeholders(rendered: str) -> None
  Scans the Jinja2-rendered HTML string for any remaining {{ ... }} tokens
  or NARRATIVE_PLACEHOLDER__ sentinel strings. Raises ValueError immediately
  on the first unfilled token found, naming it explicitly. This is called
  after Jinja2 rendering but before any file is written.

validate_narrative_lengths(context: dict, narrative_keys: tuple) -> None
  Checks that every AI-generated narrative field in the context dict
  contains between MIN_WORDS and MAX_WORDS words (inclusive). Raises
  ValueError naming the field and its actual word count. Called before
  the Jinja2 render so malformed narratives never reach the template.

validate_context_required_keys(context: dict, required: tuple) -> None
  Checks that every key in the required tuple is present in the context
  dict and is not None. Raises KeyError naming the first missing key.
  Called at the start of the render pipeline before any work is done.
"""

import re
from typing import Any

# ---------------------------------------------------------------------------
# Word-count bounds (spec Module 5 validation criteria)
# ---------------------------------------------------------------------------

MIN_WORDS: int = 50
MAX_WORDS: int = 300

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches any remaining Jinja2 variable token after rendering.
# Jinja2 with undefined=StrictUndefined raises before this is needed,
# but a defence-in-depth scan catches edge cases (e.g. escaped braces
# that slipped through, or templates loaded outside the Jinja2 env).
_JINJA2_TOKEN_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Matches the NARRATIVE_PLACEHOLDER__ sentinel prefix anywhere in the
# rendered text. A match means the AI generation step was skipped for
# that section.
_NARRATIVE_PLACEHOLDER_RE = re.compile(r"NARRATIVE_PLACEHOLDER__\w+")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def validate_no_unfilled_placeholders(rendered: str) -> None:
    """
    Scan a Jinja2-rendered document string for unfilled variable tokens
    and un-replaced narrative placeholders.

    This function is the gate between rendering and file generation. It
    must be called on the rendered HTML string before any .docx or .pdf
    file is written. A document with unfilled placeholders must never
    reach the client.

    Args:
        rendered: The full HTML string produced by Jinja2 template
                  rendering. Must be the complete rendered output, not
                  a fragment.

    Raises:
        ValueError: if any {{ ... }} token or NARRATIVE_PLACEHOLDER__
                    sentinel remains in the rendered string. The error
                    message names the first offending token explicitly.
    """
    jinja_match = _JINJA2_TOKEN_RE.search(rendered)
    if jinja_match:
        raise ValueError(
            f"Unfilled Jinja2 variable token found in rendered document: "
            f"'{jinja_match.group()[:120]}'. "
            "All template variables must be populated before rendering. "
            "Check the context dict for missing keys."
        )

    placeholder_match = _NARRATIVE_PLACEHOLDER_RE.search(rendered)
    if placeholder_match:
        raise ValueError(
            f"Unfilled narrative placeholder found in rendered document: "
            f"'{placeholder_match.group()}'. "
            "AI narrative generation must complete for all sections before "
            "the document is rendered. Run the narrative generator first."
        )


def validate_narrative_lengths(
    context: dict[str, Any],
    narrative_keys: tuple[str, ...],
) -> None:
    """
    Validate that every AI narrative field meets the word-count bounds.

    Word count is calculated as the number of whitespace-delimited tokens
    in the narrative string. HTML tags are not stripped before counting
    because narratives are expected to be plain text at this stage; the
    template wraps them in <p> tags.

    Args:
        context:        The report context dict.
        narrative_keys: Tuple of key names in context that are AI-generated
                        narrative fields subject to length validation.
                        Use SNAPSHOT_NARRATIVE_KEYS or
                        MONTHLY_BRIEF_NARRATIVE_KEYS from reports/context.py.

    Raises:
        ValueError: if any narrative field is absent, is the placeholder
                    sentinel, or falls outside [MIN_WORDS, MAX_WORDS].
                    The error message names the field, the actual count,
                    and the required range.
    """
    from reports.context import NARRATIVE_PLACEHOLDER_PREFIX  # local import avoids circular

    for key in narrative_keys:
        if key not in context:
            raise ValueError(
                f"Narrative field '{key}' is absent from the report context. "
                "All narrative sections must be generated before validation."
            )

        value = context[key]

        if not isinstance(value, str):
            raise ValueError(
                f"Narrative field '{key}' must be a string, "
                f"got {type(value).__name__}."
            )

        if value.startswith(NARRATIVE_PLACEHOLDER_PREFIX):
            raise ValueError(
                f"Narrative field '{key}' still contains the unfilled "
                f"placeholder sentinel '{value[:80]}'. "
                "AI generation must run before validation."
            )

        word_count = len(value.split())
        if word_count < MIN_WORDS:
            raise ValueError(
                f"Narrative field '{key}' is too short: {word_count} words "
                f"(minimum {MIN_WORDS}). "
                "The AI generation step must produce at least "
                f"{MIN_WORDS} words per section."
            )

        if word_count > MAX_WORDS:
            raise ValueError(
                f"Narrative field '{key}' is too long: {word_count} words "
                f"(maximum {MAX_WORDS}). "
                "The AI generation step must not exceed "
                f"{MAX_WORDS} words per section."
            )


def validate_monthly_brief_deep_dives(
    narrative_deep_dives: dict[str, str],
) -> None:
    """
    Validate the per-dimension deep-dive narratives in a Monthly Brief context.

    Dimensions with material movement (non-empty string) must meet the same
    50-300 word bounds as other narrative sections. Dimensions with no
    material movement have an empty string value and are skipped.

    Args:
        narrative_deep_dives: Dict keyed by dimension_id ('1'-'5') mapping
                              to the AI-generated deep-dive text, or an
                              empty string for dimensions with no material
                              movement.

    Raises:
        ValueError: if any non-empty deep-dive value falls outside
                    [MIN_WORDS, MAX_WORDS] or still contains a placeholder.
    """
    from reports.context import NARRATIVE_PLACEHOLDER_PREFIX

    valid_ids = {"1", "2", "3", "4", "5"}
    for dim_id, text in narrative_deep_dives.items():
        if dim_id not in valid_ids:
            raise ValueError(
                f"narrative_dimension_deep_dives contains unrecognised "
                f"dimension_id '{dim_id}'. Valid IDs are '1' through '5'."
            )

        if not text:
            continue

        if text.startswith(NARRATIVE_PLACEHOLDER_PREFIX):
            raise ValueError(
                f"Dimension deep-dive for dimension '{dim_id}' still contains "
                f"the placeholder sentinel. Run AI generation first."
            )

        word_count = len(text.split())
        if word_count < MIN_WORDS:
            raise ValueError(
                f"Dimension deep-dive for dimension '{dim_id}' is too short: "
                f"{word_count} words (minimum {MIN_WORDS})."
            )

        if word_count > MAX_WORDS:
            raise ValueError(
                f"Dimension deep-dive for dimension '{dim_id}' is too long: "
                f"{word_count} words (maximum {MAX_WORDS})."
            )


def validate_context_required_keys(
    context: dict[str, Any],
    required: tuple[str, ...],
) -> None:
    """
    Assert that every required key is present and not None in the context.

    Called at the start of the render pipeline so the error message names
    the missing key before any partial work is done.

    Args:
        context:  The report context dict to validate.
        required: Tuple of key names that must be present and non-None.

    Raises:
        KeyError: if any required key is absent or mapped to None,
                  naming the first missing key.
    """
    for key in required:
        if key not in context or context[key] is None:
            raise KeyError(
                f"Required context key '{key}' is absent or None. "
                "Ensure the database query layer populates all required "
                "fields before calling the template renderer."
            )
