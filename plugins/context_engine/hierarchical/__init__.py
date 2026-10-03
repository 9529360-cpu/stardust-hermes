"""Incremental hierarchical context compression for long-lived Stardust sessions.

The engine deliberately reuses the built-in ContextCompressor's provider handling,
failure classification, redaction, message-boundary repair, and SessionDB integration.
Only the compaction policy changes:

- summarize only newly-consumed raw turns into immutable tier-1 blocks;
- carry older block text forward byte-for-byte instead of feeding it back into every
  subsequent summary request;
- when the live block set grows past a bounded ceiling, re-distill the oldest blocks
  into a higher-tier block;
- keep the existing in-place SessionDB archive as the sole authority for original
  pre-compaction turns.

This engine is opt-in through context.engine: hierarchical.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from agent.context_compressor import (
    COMPRESSED_SUMMARY_HAS_USER_TURN_KEY,
    ContextCompressor,
)
from agent.model_metadata import estimate_messages_tokens_rough

logger = logging.getLogger(__name__)

_CONTAINER_MAGIC = "[STARDUST CONTEXT BLOCKS v1]"
_BLOCK_OPEN_PREFIX = "<<<STARDUST_CONTEXT_BLOCK "
_BLOCK_END = "<<<END_STARDUST_CONTEXT_BLOCK>>>"
_BLOCK_HEADER_RE = re.compile(
    r"^<<<STARDUST_CONTEXT_BLOCK "
    r"id=(b_[0-9a-f]{16}) "
    r"tier=([1-3]) "
    r"user=([01]) "
    r"parents=([A-Za-z0-9_,.-]+)>>>$"
)
_NO_USER_SENTINEL_FRAGMENT = "no user-authored turns"


@dataclass(frozen=True)
class _ContextBlock:
    block_id: str
    tier: int
    body: str
    has_user_turn: bool
    parents: tuple[str, ...] = ()


class HierarchicalContextEngine(ContextCompressor):
    """ContextCompressor policy that preserves immutable incremental summary blocks."""

    _MIN_INCREMENT_TOKENS = 4_096
    _MAX_LIVE_BLOCKS = 8
    _PROMOTION_FANOUT = 4
    _MAX_PROMOTIONS_PER_PASS = 2
    _BLOCK_SUMMARY_RATIO = 0.08
    _BLOCK_SUMMARY_MIN_TOKENS = 900
    _BLOCK_SUMMARY_MAX_TOKENS = 2_600

    def __init__(self) -> None:
        # Keep the built-in safety/recovery machinery, but use a smaller recent-tail
        # ratio and fail closed if the primary incremental summary cannot be produced.
        # The host calls update_model() immediately after loading the engine.
        super().__init__(
            model="",
            threshold_percent=0.50,
            protect_first_n=3,
            protect_last_n=8,
            summary_target_ratio=0.12,
            quiet_mode=False,
            abort_on_summary_failure=True,
            tail_mode="legacy",
        )

    @property
    def name(self) -> str:
        return "hierarchical"

    def _compute_summary_budget(self, turns_to_summarize: List[Dict[str, Any]]) -> int:
        """Keep each immutable block compact enough that several can stay resident."""
        content_tokens = max(1, estimate_messages_tokens_rough(turns_to_summarize))
        budget = int(content_tokens * self._BLOCK_SUMMARY_RATIO)
        return max(
            self._BLOCK_SUMMARY_MIN_TOKENS,
            min(budget, self._BLOCK_SUMMARY_MAX_TOKENS),
        )

    @staticmethod
    def _json_default(value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return model_dump()
            except Exception:
                pass
        as_dict = getattr(value, "__dict__", None)
        if isinstance(as_dict, dict):
            return as_dict
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @classmethod
    def _message_fingerprint(cls, messages: Sequence[Dict[str, Any]]) -> str:
        """Stable content fingerprint; runtime-only metadata never changes block identity."""
        cleaned: list[dict[str, Any]] = []
        ignored = {"timestamp", "api_content", "display_metadata"}
        for message in messages:
            cleaned.append({
                key: value
                for key, value in message.items()
                if not str(key).startswith("_") and key not in ignored
            })
        payload = json.dumps(
            cleaned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=cls._json_default,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _block_id_for_messages(cls, messages: Sequence[Dict[str, Any]]) -> str:
        return "b_" + cls._message_fingerprint(messages)[:16]

    @staticmethod
    def _block_id_for_parents(parents: Sequence[str], tier: int) -> str:
        payload = f"tier={tier}|" + "|".join(parents)
        return "b_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _block_id_for_legacy_body(body: str) -> str:
        payload = "legacy|" + body
        return "b_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _escape_block_markers(body: str) -> str:
        # A summary is model output. Prevent an echoed protocol marker from becoming
        # structural state on the next resume/compaction.
        return (
            body.replace(_BLOCK_OPEN_PREFIX, "<<<STARDUST CONTEXT BLOCK ")
            .replace(_BLOCK_END, "<<<END STARDUST CONTEXT BLOCK>>>")
            .strip()
        )

    @classmethod
    def _render_container(cls, blocks: Sequence[_ContextBlock]) -> str:
        lines = [
            _CONTAINER_MAGIC,
            (
                "Immutable historical checkpoints, oldest to newest. "
                "Only higher-tier promotion may rewrite older block summaries. "
                "Original compacted turns remain in the session archive for exact recovery."
            ),
            "",
        ]
        for block in blocks:
            parents = ",".join(block.parents) if block.parents else "-"
            lines.append(
                f"{_BLOCK_OPEN_PREFIX}id={block.block_id} tier={block.tier} "
                f"user={1 if block.has_user_turn else 0} parents={parents}>>>"
            )
            lines.append(cls._escape_block_markers(block.body))
            lines.append(_BLOCK_END)
            lines.append("")
        lines.extend([
            "For exact omitted details, use session_search against this session instead of guessing.",
        ])
        return cls._with_summary_prefix("\n".join(lines).strip())

    @classmethod
    def _parse_container(cls, body: str) -> Optional[list[_ContextBlock]]:
        """Parse our durable text protocol; malformed containers fall back to legacy preservation."""
        lines = (body or "").strip().splitlines()
        if not lines or lines[0].strip() != _CONTAINER_MAGIC:
            return None

        blocks: list[_ContextBlock] = []
        index = 1
        while index < len(lines):
            header = _BLOCK_HEADER_RE.fullmatch(lines[index].strip())
            if not header:
                index += 1
                continue
            end = index + 1
            while end < len(lines) and lines[end].strip() != _BLOCK_END:
                end += 1
            if end >= len(lines):
                return None
            block_body = "\n".join(lines[index + 1:end]).strip()
            if not block_body:
                return None
            parents_raw = header.group(4)
            parents = () if parents_raw == "-" else tuple(
                item for item in parents_raw.split(",") if item
            )
            blocks.append(_ContextBlock(
                block_id=header.group(1),
                tier=int(header.group(2)),
                body=block_body,
                has_user_turn=header.group(3) == "1",
                parents=parents,
            ))
            index = end + 1
        return blocks

    @staticmethod
    def _legacy_has_user_turn(message: Dict[str, Any], body: str) -> bool:
        provenance = message.get(COMPRESSED_SUMMARY_HAS_USER_TURN_KEY)
        if isinstance(provenance, bool):
            return provenance
        return _NO_USER_SENTINEL_FRAGMENT not in body.lower()

    def _extract_existing_blocks(self, messages: List[Dict[str, Any]]) -> list[_ContextBlock]:
        """Recover block state from the live handoff message without a second store."""
        start = 1 if messages and messages[0].get("role") == "system" else 0
        hits = self._find_context_summaries(messages, start, len(messages))
        blocks: list[_ContextBlock] = []
        for index, body in hits:
            parsed = self._parse_container(body)
            if parsed is not None:
                blocks.extend(parsed)
                continue
            if body.strip():
                blocks.append(_ContextBlock(
                    block_id=self._block_id_for_legacy_body(body.strip()),
                    tier=1,
                    body=body.strip(),
                    has_user_turn=self._legacy_has_user_turn(messages[index], body),
                ))

        # Retry/resume paths can surface the same handoff more than once. First wins:
        # a block id denotes one historical coverage range inside this live projection.
        unique: list[_ContextBlock] = []
        seen: set[str] = set()
        for block in blocks:
            if block.block_id in seen:
                continue
            seen.add(block.block_id)
            unique.append(block)
        return unique

    def _summarize_increment(
        self,
        turns: List[Dict[str, Any]],
        *,
        focus_topic: Optional[str],
        memory_context: str,
        bypass_cooldown: bool,
    ) -> Optional[str]:
        """Summarize only new raw turns, never the resident historical block container."""
        previous_summary = self._previous_summary
        previous_provenance = self._summary_has_user_turn
        try:
            self._previous_summary = None
            self._summary_has_user_turn = self._transcript_has_real_user_turn(turns)
            return self._generate_summary(
                turns,
                focus_topic=focus_topic,
                memory_context=memory_context,
                bypass_cooldown=bypass_cooldown,
            )
        finally:
            # _generate_summary stores its result in _previous_summary for the built-in
            # iterative-update algorithm. Our authoritative live summary is the block
            # container assembled below, so restore the outer state here.
            self._previous_summary = previous_summary
            self._summary_has_user_turn = previous_provenance

    def _fallback_increment(
        self,
        telemetry: Dict[str, Any],
        turns: List[Dict[str, Any]],
        *,
        n_dropped: int,
        feasibility_skip: bool,
    ) -> str:
        previous_summary = self._previous_summary
        previous_provenance = self._summary_has_user_turn
        try:
            self._previous_summary = None
            self._summary_has_user_turn = self._transcript_has_real_user_turn(turns)
            return self._fallback_summary_for_window(
                telemetry,
                turns,
                n_dropped,
                feasibility_skip,
            )
        finally:
            self._previous_summary = previous_summary
            self._summary_has_user_turn = previous_provenance

    def _summarize_promotion(
        self,
        blocks: Sequence[_ContextBlock],
        *,
        focus_topic: Optional[str],
    ) -> Optional[str]:
        """Re-distill old summaries in isolated state so a failed promotion cannot poison the live engine."""
        synthetic_turns = [
            {
                "role": "assistant",
                "content": (
                    f"Archived context block {block.block_id} (tier {block.tier}):\n"
                    f"{block.body}"
                ),
            }
            for block in blocks
        ]
        helper = ContextCompressor(
            model=self.model,
            threshold_percent=self.threshold_percent,
            protect_first_n=0,
            protect_last_n=2,
            summary_target_ratio=0.10,
            quiet_mode=True,
            base_url=self.base_url,
            api_key=self.api_key,
            config_context_length=self.context_length or None,
            provider=self.provider,
            api_mode=self.api_mode,
            abort_on_summary_failure=True,
            max_tokens=self.max_tokens,
            model_thresholds=getattr(self, "model_thresholds", {}),
            tail_mode="legacy",
            custom_providers=getattr(self, "custom_providers", None),
        )
        helper._summary_has_user_turn = any(block.has_user_turn for block in blocks)
        summary = helper._generate_summary(
            synthetic_turns,
            focus_topic=focus_topic,
            memory_context="",
        )
        return self._strip_summary_prefix(summary) if summary else None

    def _promote_blocks(
        self,
        blocks: Sequence[_ContextBlock],
        *,
        focus_topic: Optional[str],
    ) -> tuple[list[_ContextBlock], int]:
        """Bound resident summaries with at most two old-block promotion calls per pass."""
        current = list(blocks)
        promotions = 0
        while (
            len(current) > self._MAX_LIVE_BLOCKS
            and promotions < self._MAX_PROMOTIONS_PER_PASS
            and len(current) >= self._PROMOTION_FANOUT
        ):
            group = current[: self._PROMOTION_FANOUT]
            promoted_body = self._summarize_promotion(group, focus_topic=focus_topic)
            if not promoted_body:
                logger.warning(
                    "Hierarchical context promotion failed; keeping %d source blocks unchanged",
                    len(group),
                )
                break
            next_tier = min(max(block.tier for block in group) + 1, 3)
            parent_ids = tuple(block.block_id for block in group)
            promoted = _ContextBlock(
                block_id=self._block_id_for_parents(parent_ids, next_tier),
                tier=next_tier,
                body=promoted_body,
                has_user_turn=any(block.has_user_turn for block in group),
                parents=parent_ids,
            )
            current = [promoted, *current[self._PROMOTION_FANOUT:]]
            promotions += 1
        return current, promotions

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
        bypass_cooldown: bool = False,
    ) -> List[Dict[str, Any]]:
        """Compact only the newly-consumed region, then carry immutable blocks forward."""
        telemetry = self._begin_compress_attempt(current_tokens, force)
        # Keep the exact input projection so a terminal summary failure is a true
        # no-op. The built-in compressor intentionally lets its cheap pre-prune
        # survive an abort; this engine's stronger contract is different because
        # immutable block creation must be all-or-nothing.
        original_messages = messages
        n_messages = len(messages)
        minimum = self._protect_head_size(messages) + 4
        if n_messages <= minimum:
            self._structural_no_op_result(
                telemetry,
                "insufficient_messages",
                f"only {n_messages} messages (need > {minimum})",
            )
            return messages

        display_tokens = (
            current_tokens
            if current_tokens
            else self.last_prompt_tokens or estimate_messages_tokens_rough(messages)
        )

        messages, pruned_count = self._prune_old_tool_results(
            messages,
            protect_tail_count=self.protect_last_n,
            protect_tail_tokens=self.tail_token_budget,
        )
        if pruned_count and not self.quiet_mode:
            logger.info(
                "Hierarchical pre-compression: pruned %d old tool result(s)",
                pruned_count,
            )
        messages = self._drop_blank_echoes(messages)
        n_messages = len(messages)

        existing_blocks = self._extract_existing_blocks(messages)
        compress_start, compress_end = self._compress_window(messages)
        if compress_start >= compress_end:
            self._record_compression_regions(
                head_messages=messages[:compress_start],
                middle_messages=[],
                tail_messages=messages[compress_end:],
            )
            self._structural_no_op_result(
                telemetry,
                "no_compressible_window",
                (
                    f"compress_start ({compress_start}) >= compress_end ({compress_end}) "
                    "- transcript fits within tail budget"
                ),
            )
            return messages

        turns_to_summarize = messages[compress_start:compress_end]
        scan = self._scan_window_handoffs(
            messages,
            compress_start,
            compress_end,
            turns_to_summarize,
        )
        turns_to_summarize = scan.turns_to_summarize
        self._record_compression_regions(
            head_messages=messages[:compress_start],
            middle_messages=turns_to_summarize,
            tail_messages=messages[compress_end:],
        )

        if not turns_to_summarize:
            self._structural_no_op_result(
                telemetry,
                "empty_post_handoff_window",
                f"window {compress_start}-{compress_end} holds only existing context blocks",
            )
            return messages

        increment_tokens = estimate_messages_tokens_rough(turns_to_summarize)
        if not force and increment_tokens < self._MIN_INCREMENT_TOKENS:
            self._structural_no_op_result(
                telemetry,
                "increment_too_small",
                (
                    f"only {increment_tokens} unsummarized tokens since the last block "
                    f"(need >= {self._MIN_INCREMENT_TOKENS})"
                ),
            )
            return messages

        if not self.quiet_mode:
            self._log_compression_start(
                display_tokens,
                compress_start,
                compress_end,
                len(turns_to_summarize),
                n_messages - scan.tail_start,
            )

        effective_focus = focus_topic or self._derive_auto_focus_topic(messages)
        feasibility_skip = (
            not force
            and self._feasibility_skip(
                telemetry,
                turns_to_summarize,
                compress_start,
                compress_end,
            )
        )

        summary: Optional[str] = None
        if not feasibility_skip:
            summary = self._summarize_increment(
                turns_to_summarize,
                focus_topic=effective_focus,
                memory_context=memory_context,
                bypass_cooldown=bypass_cooldown,
            )
            if not summary and self._abort_on_summary_failure(
                telemetry,
                compress_end - compress_start,
                scan.previous_summary_before,
            ):
                return original_messages

        if not summary:
            summary = self._fallback_increment(
                telemetry,
                turns_to_summarize,
                n_dropped=compress_end - compress_start,
                feasibility_skip=feasibility_skip,
            )

        increment_has_user = self._transcript_has_real_user_turn(turns_to_summarize)
        new_block = _ContextBlock(
            block_id=self._block_id_for_messages(turns_to_summarize),
            tier=1,
            body=self._strip_summary_prefix(summary),
            has_user_turn=increment_has_user,
        )

        blocks = list(existing_blocks)
        if all(block.block_id != new_block.block_id for block in blocks):
            blocks.append(new_block)

        blocks, promotions = self._promote_blocks(
            blocks,
            focus_topic=effective_focus,
        )
        telemetry["chunk_count"] = 1 + promotions

        container_summary = self._render_container(blocks)
        self._previous_summary = self._strip_summary_prefix(container_summary)
        compressed = self._assemble_compressed(
            messages,
            compress_start,
            compress_end,
            scan,
            container_summary,
        )
        return self._finalize_compressed(compressed, messages, n_messages)


def register(ctx: Any) -> None:
    """Explicit registration avoids the loader mistaking the imported base class for this engine."""
    ctx.register_context_engine(HierarchicalContextEngine())
