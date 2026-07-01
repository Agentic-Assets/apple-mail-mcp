"""Pure-Python input normalizers, AppleScript OR-condition builders, and the email-list parser."""

from typing import Any

from apple_mail_mcp.core.escaping import escape_applescript


def normalize_search_terms(
    search_term: str | None = None,
    search_terms: list[str] | None = None,
) -> list[str]:
    """Return de-duplicated, non-empty search terms preserving order."""
    normalized = []

    if search_term and search_term.strip():
        normalized.append(search_term.strip())

    if search_terms:
        for term in search_terms:
            if term and term.strip():
                normalized.append(term.strip())

    unique_terms = []
    for term in normalized:
        if term not in unique_terms:
            unique_terms.append(term)

    return unique_terms


def contains_any_condition(field_name: str, values: list[str]) -> str:
    """Return AppleScript OR conditions for substring matches."""
    if not values:
        return "true"

    escaped_values = [escape_applescript(value) for value in values]
    parts = [f'{field_name} contains "{value}"' for value in escaped_values]
    return "(" + " or ".join(parts) + ")"


def normalize_message_ids(message_ids: list[Any] | None) -> list[str]:
    """Return de-duplicated numeric Mail ids as strings preserving order."""
    if not message_ids:
        return []

    normalized = []
    for value in message_ids:
        value_text = str(value).strip()
        if value_text and value_text.isdigit() and value_text not in normalized:
            normalized.append(value_text)

    return normalized


def equals_any_numeric_condition(field_name: str, values: list[str]) -> str:
    """Return AppleScript OR conditions for numeric equality matches."""
    if not values:
        return "false"

    parts = [f"{field_name} is {value}" for value in values]
    return "(" + " or ".join(parts) + ")"


def parse_email_list(output: str) -> list[dict[str, Any]]:
    """Parse the structured email output from AppleScript"""
    emails: list[dict[str, Any]] = []
    lines = output.split("\n")

    current_email: dict[str, Any] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("━") or line.startswith("📧") or line.startswith("⚠"):
            continue

        if line.startswith("✉") or line.startswith("✓"):
            # New email entry
            if current_email:
                emails.append(current_email)

            is_read = line.startswith("✓")
            subject = line[2:].strip()  # Remove indicator
            current_email = {"subject": subject, "is_read": is_read}
        elif line.startswith("From:"):
            current_email["sender"] = line[5:].strip()
        elif line.startswith("Date:"):
            current_email["date"] = line[5:].strip()
        elif line.startswith("Preview:"):
            current_email["preview"] = line[8:].strip()
        elif line.startswith("TOTAL EMAILS"):
            # End of email list
            if current_email:
                emails.append(current_email)
            break

    if current_email and current_email not in emails:
        emails.append(current_email)

    return emails
