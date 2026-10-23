"""A display string with optional locale-independent presentation metadata.

String consumers retain their existing output; structured drivers can localize it.
"""


class ProviderRetryNotice(str):
    code = "provider_retry"

    def __new__(cls, text: str, *, seconds: int, attempt: int, max_attempts: int):
        value = super().__new__(cls, text)
        value.params = {"seconds": seconds, "attempt": attempt, "max_attempts": max_attempts}
        return value
