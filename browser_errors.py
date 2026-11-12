"""Browser errors shared by the application and the native Chrome client."""


class BrowserError(Exception):
    pass


class BrowserTimeout(BrowserError):
    pass
