class JarvisError(Exception):
    """Base application error."""


class ConfirmationRequiredError(JarvisError):
    """Raised when an action requires user confirmation."""
