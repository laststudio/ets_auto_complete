class DemoError(Exception):
    """Base error shown to CLI users without a traceback."""


class ConfigError(DemoError):
    """Required configuration is missing or invalid."""


class InterfaceError(DemoError):
    """The configured local interface returned an unsuccessful response."""


class AudioError(DemoError):
    """Audio synthesis or conversion failed."""


class IseError(DemoError):
    """ISE websocket evaluation failed."""


class EtsError(DemoError):
    """The real ETS platform returned an unsuccessful response."""
