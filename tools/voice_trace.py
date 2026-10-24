"""Opt-in, content-free voice timing. No model tool registration.

Timestamps use monotonic clocks; compare only entries with the same clock.
Renderer observations are elapsed milliseconds on one renderer turn clock.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from pathlib import Path

STAGES = (
    "listen_start", "eos", "stt_request", "stt_result", "llm_first_token",
    "tts_request", "network_first_audio_byte", "audio_context_scheduled",
    "playback_start", "turn_end",
)
REASONS = frozenset({
    "not_observed", "not_reached", "cancelled", "failed", "unsupported",
    "no_speech", "no_background_task", "client_direct_uninstrumented",
})
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_written: dict[tuple[str, str], float] = {}
_write_lock = threading.Lock()
_log = logging.getLogger(__name__)


def trace_enabled(config: dict) -> bool:
    voice = config.get("voice", {})
    return isinstance(voice, dict) and voice.get("trace_enabled") is True


class VoiceTrace:
    """A turn writes exactly once at its terminal boundary, never on each stage."""

    @classmethod
    def begin(cls, config: dict, *, home: Path | None = None, turn_id: str | None = None):
        if not trace_enabled(config):
            return None
        if home is None:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
        return cls(Path(home), turn_id or uuid.uuid4().hex)

    def __init__(self, home: Path, turn_id: str):
        if not isinstance(turn_id, str) or not _ID.fullmatch(turn_id):
            raise ValueError("invalid turn id")
        self.home = home
        self.turn_id = turn_id
        self.task_id = None
        self.stages = {}
        self._lock = threading.RLock()
        self._finished = False

    def mark(self, stage: str, *, timestamp_ms: float | None = None, clock: str = "backend"):
        if stage not in STAGES or clock not in {"backend", "renderer"}:
            raise ValueError("invalid stage or clock")
        stamp = time.monotonic_ns() / 1_000_000 if timestamp_ms is None else timestamp_ms
        if isinstance(stamp, bool) or not isinstance(stamp, (float, int)) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError("invalid timestamp")
        with self._lock:
            if not self._finished:
                self.stages.setdefault(stage, {"timestamp_ms": stamp, "clock": clock, "reason": None})

    def missing(self, stage: str, reason: str):
        if stage not in STAGES or reason not in REASONS:
            raise ValueError("invalid stage or reason")
        with self._lock:
            if not self._finished:
                self.stages.setdefault(stage, {"timestamp_ms": None, "clock": None, "reason": reason})

    def link_task(self, task_id: str):
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid task id")
        with self._lock:
            if self.task_id is not None and self.task_id != task_id:
                raise ValueError("task id already linked")
            self.task_id = task_id

    def finish(self, reason: str = "not_reached") -> bool:
        if reason not in REASONS:
            raise ValueError("invalid terminal reason")
        with self._lock:
            if self._finished:
                return False
            self.mark("turn_end")
            for stage in STAGES:
                self.missing(stage, reason)
            row = {"schema_version": 1, "turn_id": self.turn_id, "task_id": self.task_id,
                   "task_id_reason": None if self.task_id else "not_observed",
                   "stages": self.stages}
            try:
                with _write_lock:
                    key = (str(self.home.resolve()), self.turn_id)
                    now = time.monotonic()
                    for old, stamp in list(_written.items()):
                        if now - stamp > 3600:
                            del _written[old]
                    if key in _written:
                        self._finished = True
                        return False
                    if len(_written) >= 4096:
                        del _written[next(iter(_written))]
                    logs = self.home / "logs"
                    logs.mkdir(parents=True, exist_ok=True)
                    with (logs / "voice-trace.jsonl").open("a", encoding="utf-8") as out:
                        out.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
                    _written[key] = now
            except OSError:
                # Instrumentation must not abort a voice turn. Do not log exception
                # text: paths and provider errors may contain private information.
                _log.warning("Voice trace could not be written")
                self._finished = True
                return False
            self._finished = True
            return True
