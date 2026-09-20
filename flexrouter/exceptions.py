class RouterBusy(Exception):
    """All models in the requested tier are rate-limited and wait=False."""

class RouterError(Exception):
    """Unrecoverable provider error (auth failure, repeated 5xx)."""

class ConfigError(Exception):
    """Invalid or missing flexrouter.yaml."""

class ConfigFieldError(ConfigError):
    """The settings file parsed fine but is missing a field it needs."""

class ContextWindowWarning(UserWarning):
    """Some models in tier skipped due to context window size."""

class ServiceNotRunning(RouterError):
    """Nothing is listening where the flexrouter service should be.

    A subclass of RouterError so code that already catches RouterError keeps
    working. Deliberately not handled by starting the service: auto-start was
    rejected twice, and a silent local fallback would give every process its
    own settings and its own private idea of how much of each provider's
    allowance was left.
    """
