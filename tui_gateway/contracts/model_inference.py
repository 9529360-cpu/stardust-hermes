"""Explicit, one-shot model inference check contracts."""

from typing import Literal

from pydantic import Field

from .base import Params, Result
from .registry import method


class ModelInferenceTestParams(Params):
    profile: str = Field(default="", max_length=128)
    session_id: str = Field(min_length=1, max_length=160)
    probe_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    timeout_s: float = Field(default=20, gt=0, le=30)


class ModelInferenceTestResult(Result):
    status: Literal["success", "failed", "unsupported", "canceled", "cancel_not_supported", "timed_out"]
    inference_ok: bool | None
    provider: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    source: str | None = Field(default=None, max_length=64)
    latency_ms: float | None = None
    error_kind: Literal["auth", "rate_limit", "timeout", "network", "server", "empty_response", "unknown"] | None = None
    error: str | None = Field(default=None, max_length=160)
    response_summary: str | None = Field(default=None, max_length=160)
    nonempty_response: bool | None = None
    cancel_supported: bool = False


class ModelInferenceTestCancelParams(Params):
    profile: str = Field(default="", max_length=128)
    session_id: str = Field(min_length=1, max_length=160)
    probe_id: str = Field(min_length=1, max_length=128)


class ModelInferenceTestCancelResult(Result):
    status: Literal["cancel_requested", "not_running", "cancel_not_supported", "forbidden"]
    message: str = Field(max_length=160)
    cancel_supported: bool


method(
    "setup.inference_test",
    params=ModelInferenceTestParams,
    result=ModelInferenceTestResult,
    doc="Explicit one-request inference test for the selected route; never called by readiness polling.",
)
method(
    "setup.inference_test_cancel",
    params=ModelInferenceTestCancelParams,
    result=ModelInferenceTestCancelResult,
    doc="Cancel one in-flight inference test owned by this exact RPC transport, profile, and session.",
)
