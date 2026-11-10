class CaptureError(Exception):
    """A command cannot proceed and nothing has been written; `code` is the exit status.

    1 is a runtime refusal (a missing session, an existing case or label), 2 is a usage error or an
    invalid input file.
    """

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code
