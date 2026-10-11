class DomainError(Exception):
    """Base class for expected business failures. Interfaces translate them into user-facing messages."""


class InvalidUpdate(DomainError):
    pass


class QueueUnavailable(DomainError):
    pass


class NotFound(DomainError):
    pass
