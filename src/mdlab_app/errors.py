"""Stable, structured failures for the local processing CLI."""

from datetime import datetime, timezone


class ProcessingError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), "details": self.details}


def utc_timestamp(value):
    """Validate the contract's canonical UTC format, suitable for SQL ordering."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ProcessingError("INVALID_ARGUMENT", "Expected UTC YYYY-MM-DDTHH:MM:SSZ", value) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ProcessingError("INVALID_ARGUMENT", "Timestamp must use canonical UTC format", value)
    return parsed
