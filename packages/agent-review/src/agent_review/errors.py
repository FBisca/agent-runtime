"""The two ways an invocation stops, kept apart because they mean different things (D34, D35)."""


class ReviewError(Exception):
    """The invocation cannot proceed and nothing was screened; `code` is the exit status.

    2 is the only refusal code: a usage error or an input the run cannot read (an invalid question
    configuration, a malformed fixture, a revision that does not resolve). The message names what it
    refused and the way out.
    """

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


class ScreeningError(Exception):
    """The screening did not complete: the provider failed, timed out or answered nothing usable.

    It is never a verdict, never retried and never a fallback to another model (D40): the caller reports
    the execution as incomplete and records this reason. A raised message carries the reason verbatim.
    """
