class WorkerError(RuntimeError):
    """A concise, machine-readable worker failure."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")
