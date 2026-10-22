"""Per-session receipts for ``prompt.submit``: a bounded, process-local record of accepted ``client_message_id``s."""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class SubmissionReceipts:
    """Remember accepted submissions for ``ttl`` seconds (at most ``capacity``) and serialize admission.

    ``submit`` runs ``accept`` at most once per id: a repeat of a successful submission gets the first result
    back, flagged ``already_received``, without calling ``accept`` again. Errors are not receipts, so a refused
    submission can retry. Receipts live with the live session: they survive client reconnects but not session
    eviction or a backend restart.
    """

    def __init__(self, capacity: int = 1024, ttl: float = 3600, clock: Callable[[], float] = time.monotonic):
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._receipts: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, message_id: str, accept: Callable[[], dict]) -> dict:
        """``accept()`` returns a JSON-RPC response; it is returned as-is the first time. A replay returns
        ``{"result": ...}`` only: the caller wraps it under the new request id."""
        with self._lock:
            now = self._clock()
            while self._receipts and next(iter(self._receipts.values()))[0] <= now - self._ttl:
                self._receipts.popitem(last=False)
            if message_id in self._receipts:
                _, result = self._receipts[message_id]
                return {"result": {**result, "already_received": True}}
            response = accept()
            if "result" in response:
                self._receipts[message_id] = (self._clock(), dict(response["result"]))
                while len(self._receipts) > self._capacity:
                    self._receipts.popitem(last=False)
            return response
