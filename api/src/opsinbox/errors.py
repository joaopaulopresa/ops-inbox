"""The small exception hierarchy used across the application."""


class OpsInboxError(Exception):
    """Base class for every error raised on purpose by this application."""


class StorageError(OpsInboxError):
    """The object store refused or failed an operation."""


class ObjectNotFoundError(StorageError):
    """The requested object does not exist."""


class MailError(OpsInboxError):
    """The mail server could not be reached or refused an operation."""


class MessageParseError(OpsInboxError):
    """A raw message could not be parsed into something usable."""


class ReadingError(OpsInboxError):
    """An attachment could not be turned into text (corrupt or unsupported file)."""


class ModelError(OpsInboxError):
    """The model provider failed or returned something unusable."""


class RecordingMissingError(ModelError):
    """Replay mode was asked for a model response that was never recorded."""


class RuleError(OpsInboxError):
    """A business rule is malformed or cannot be evaluated."""


class NotFoundError(OpsInboxError):
    """A record that was asked for does not exist."""


class ConflictError(OpsInboxError):
    """The operation conflicts with the current state of a record."""
