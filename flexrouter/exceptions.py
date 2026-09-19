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
