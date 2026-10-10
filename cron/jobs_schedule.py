"""Schedule grammar and timing: repeat counts, durations, natural "every ..." phrases, cron
expressions, one-shots, timezone-aware timestamps, catch-up grace, cadence, stale-cron
classification and next-run computation.

Split out of ``cron.jobs``, which re-exports every name here. Names this module
does not define are reached late-bound via ``_jobs`` (import-cycle breaking), so
monkeypatching ``cron.jobs.<name>`` keeps working.
"""
from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("cron.jobs")  # log-record parity with the origin module


# --- Schedule Parsing ---

def normalize_repeat_value(repeat: Any) -> Optional[int]:
    """Coerce a repeat value (int or user-facing string) into ``Optional[int]``:
    ``'forever'``-family -> None, ``'once'``-family -> 1, numeric -> int, 0/negative -> None,
    else ValueError.

    The tool schema exposes ``repeat`` as an integer, but agents and users legitimately pass the user-facing
    strings ``'forever'``/``'once'`` or numeric strings (``'3'``). Uncoerced strings previously died with
    ``'<=' not supported between instances of 'str' and 'int'`` at create
    (#66824/#64520/#7142/#71987/#95706) and were stored raw by update paths, breaking ``mark_job_run``
    later.
    """
    if repeat is None:
        return None
    if isinstance(repeat, str):
        repeat_str = repeat.strip().lower()
        if repeat_str in ("forever", "infinite", "inf", "none", ""):
            return None
        if repeat_str in ("once", "one", "1x"):
            return 1
        try:
            repeat = int(repeat_str)
        except ValueError:
            raise ValueError(
                f"Invalid repeat value {repeat!r}: use an integer, "
                f"'forever', or 'once'."
            )
    return None if repeat <= 0 else int(repeat)


_DURATION_MULTIPLIERS = {'m': 1, 'h': 60, 'd': 1440}


def parse_duration(s: str) -> int:
    """Parse a duration into minutes: "30m" → 30, "2h" → 120, "1d" → 1440, bare "hour" → 60."""
    s = s.strip().lower()
    match = re.match(r'^(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not match:
        raise ValueError(
            f"Invalid duration: '{s}'. Use format like '30m', '2h', '1d', "
            "or a bare unit like 'hour' (defaults to 1).")
    value = int(match.group(1)) if match.group(1) else 1
    return value * _DURATION_MULTIPLIERS[match.group(2)[0]]


# Day-spec phrases for "every monday 9am" / "every day at 9am". Cron weekday numbering is
# 0=Sunday … 6=Saturday (croniter's default).
_WEEKDAY_TO_CRON_DOW = {
    "sunday": "0", "sun": "0",
    "monday": "1", "mon": "1",
    "tuesday": "2", "tue": "2", "tues": "2",
    "wednesday": "3", "wed": "3", "weds": "3",
    "thursday": "4", "thu": "4", "thur": "4", "thurs": "4",
    "friday": "5", "fri": "5",
    "saturday": "6", "sat": "6",
}

# Keyword day-specs that expand to a cron weekday field.
_DAYSPEC_TO_CRON_DOW = {
    "day": "*", "daily": "*", "everyday": "*",
    "weekday": "1-5", "weekdays": "1-5",
    "weekend": "0,6", "weekends": "0,6",
}


def _parse_clock_time(text: str) -> Optional[tuple]:
    """Parse ``9am``/``9:30am``/``14:00``/``7`` (bare 24h hour)/``noon``/``midnight`` into a
    24-hour ``(hour, minute)`` tuple, or None when unrecognized."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None
    if t in ("noon", "midday"):
        return (12, 0)
    if t == "midnight":
        return (0, 0)
    match = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', t)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return (hour, minute)


def _natural_every_to_cron(rest: str) -> Optional[str]:
    """Convert ``<when> [at] <time>`` ("monday 9am", "weekday at 9am", "monday, wednesday at 9am")
    to a 5-field cron expr, or None so ``parse_schedule`` can fall back to the interval path."""
    tokens = rest.lower().replace(",", " ").split()
    if not tokens:
        return None
    # Leading day tokens: a keyword spec ("weekdays") or a comma/"and"-separated weekday list.
    dow = _DAYSPEC_TO_CRON_DOW.get(tokens[0])
    idx = 1
    if dow is None:
        days = []
        idx = len(tokens)
        for i, tok in enumerate(tokens):
            if tok == "and":
                continue
            mapped = _WEEKDAY_TO_CRON_DOW.get(tok)
            if mapped is None:
                idx = i
                break
            if mapped not in days:
                days.append(mapped)
        if not days:
            return None
        dow = ",".join(days)
    time_tokens = tokens[idx:]
    if time_tokens and time_tokens[0] == "at":  # optional separator: "every day at 9am"
        time_tokens = time_tokens[1:]
    if not time_tokens:
        return None
    parsed = _parse_clock_time(" ".join(time_tokens))
    if parsed is None:
        return None
    hour, minute = parsed
    return f"{minute} {hour} * * {dow}"


def _cron_schedule(
    expr: str, display: str, missing_croniter: str, invalid_label: str
) -> Dict[str, Any]:
    """Validate a cron expression with croniter and build the stored schedule dict."""
    if not _jobs._ensure_croniter():
        raise ValueError(f"{missing_croniter} Install with: pip install croniter")
    try:
        _jobs.croniter(expr)
    except Exception as e:
        raise ValueError(f"Invalid {invalid_label} '{display}': {e}")
    return {"kind": "cron", "expr": expr, "display": display}


def _interval_schedule(minutes: int) -> Dict[str, Any]:
    return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}


def parse_schedule(schedule: str) -> Dict[str, Any]:
    """Parse a schedule string into ``{"kind": "once"|"interval"|"cron", ...}`` with ``run_at`` /
    ``minutes`` / ``expr``. "30m" and "every 30m" are recurring intervals; "every monday 9am" and
    "0 9 * * *" are cron; an ISO timestamp is once."""
    schedule = schedule.strip()
    original = schedule
    schedule_lower = schedule.lower()

    # Natural day/time phrase → cron ("every monday 9am", or sans prefix "weekdays at 9am");
    # any other "every X" → recurring interval.
    is_every = schedule_lower.startswith("every ")
    rest = schedule[6:].strip() if is_every else schedule_lower
    cron_expr = _natural_every_to_cron(rest)
    # Reuse the same helper — the phrase shape is identical without the "every " prefix. See #51975.
    if cron_expr is not None:
        example = "every monday 9am" if is_every else "weekdays at 9am"
        return _cron_schedule(
            cron_expr, original,
            f"Weekday/time schedules like '{example}' require the 'croniter' package.", "schedule")
    if is_every:
        return _interval_schedule(parse_duration(rest))

    # Cron expression (5-6 fields). Letters are allowed so named months/weekdays (JAN-DEC, MON-FRI)
    # reach croniter, which supports them.
    parts = schedule.split()
    if len(parts) >= 5 and all(re.match(r'^[A-Za-z\d\*\-,/]+$', p) for p in parts[:5]):
        return _cron_schedule(
            schedule, schedule, "Cron expressions require 'croniter' package.", "cron expression")

    # ISO timestamp (contains T or looks like date)
    if 'T' in schedule or re.match(r'^\d{4}-\d{2}-\d{2}', schedule):
        try:
            dt = datetime.fromisoformat(schedule.replace('Z', '+00:00'))
            # Naive timestamps become aware in the CONFIGURED Hermes timezone (not server-local):
            # the due-check compares against hermes_time.now().
            # Make naive timestamps timezone-aware at parse time so the stored value doesn't depend on the
            # system timezone matching at check time. UTC) while now() runs in Asia/Kolkata, the stored
            # instant would land hours off from the user's wall-clock intent — far enough that one-shots
            # never become due and recurring jobs fire at the wrong time. Using the configured zone makes
            # "20:07" mean 20:07 on the same clock the scheduler checks against (#51021).
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_jobs._hermes_now().tzinfo)
            return {
                "kind": "once",
                "run_at": dt.isoformat(),
                "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}"
            }
        except ValueError as e:
            raise ValueError(f"Invalid timestamp '{schedule}': {e}")

    # "in 30m"/"in 2h" is the explicit one-shot-by-duration form; a bare duration ("30m") is a
    # RECURRING interval per the documented tool contract.
    if schedule_lower.startswith("in "):
        duration_str = schedule[3:].strip()
        try:
            minutes = parse_duration(duration_str)
        except ValueError:
            raise ValueError(
                f"Invalid duration '{duration_str}' after 'in '. Use e.g. 'in 30m', 'in 2h'.")
        now = _jobs._hermes_now()
        # Durations measure elapsed time, not wall-clock hours across a DST transition.
        run_at = (now.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(now.tzinfo)
        return {"kind": "once", "run_at": run_at.isoformat(), "display": f"once in {duration_str}"}
    with contextlib.suppress(ValueError):
        return _interval_schedule(parse_duration(schedule))

    raise ValueError(
        f"Invalid schedule '{original}'. Use:\n"
        f"  - Interval: '30m', 'every 30m', 'every 2h' (recurring)\n"
        f"  - One-shot delay: 'in 30m', 'in 2h' (fires once)\n"
        f"  - Weekly/daily: 'every monday 9am', 'weekdays at 9am' (recurring)\n"
        f"  - Cron: '0 9 * * *' (cron expression)\n"
        f"  - Timestamp: '2026-02-03T14:00:00' (one-shot at time)"
    )


def _ensure_aware(dt: datetime) -> datetime:
    """Aware datetime in the configured Hermes timezone. Legacy naive values are read as
    *system-local* wall time (what created them) then converted, preserving ordering across
    timezone changes and avoiding false not-due results."""
    target_tz = _jobs._hermes_now().tzinfo
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.now().astimezone().tzinfo).astimezone(target_tz)
    return dt.astimezone(target_tz)


def _parse_aware(value: Any) -> Optional[datetime]:
    """``_ensure_aware(datetime.fromisoformat(value))``, or None when *value* is not a parseable ISO
    string."""
    try:
        return _ensure_aware(datetime.fromisoformat(value))
    except Exception:
        return None


def _timezone_offset_mismatch(stored: datetime, current: datetime) -> bool:
    """True when a stored aware timestamp uses a different UTC offset. Naive values return False:
    they are normalized by ``_ensure_aware`` and intentionally never take the offset-repair path."""
    if stored.tzinfo is None or current.tzinfo is None:
        return False
    return stored.utcoffset() != current.utcoffset()


def _stored_wall_clock_is_future(stored: datetime, current: datetime) -> bool:
    """True when the stored local wall-clock time has not arrived yet. Cron expresses wall-clock
    intent; after a timezone change an old offset can make a future run look due (21:00+10 →
    13:00+02). Comparing naive wall clocks separates that from a genuine miss."""
    return stored.replace(tzinfo=None) > current.replace(tzinfo=None)


def _recoverable_oneshot_run_at(
    schedule: Dict[str, Any], now: datetime, *, last_run_at: Optional[str] = None,
) -> Optional[str]:
    """One-shot run time if still eligible: a small grace window covers jobs created just after
    their minute; once run, a one-shot is never eligible again."""
    if not isinstance(schedule, dict) or schedule.get("kind") != "once" or last_run_at:
        return None
    run_at = schedule.get("run_at")
    run_at_dt = _parse_aware(run_at) if run_at else None
    if run_at_dt is not None and run_at_dt >= now - timedelta(seconds=_jobs.ONESHOT_GRACE_SECONDS):
        return run_at
    return None


_MIN_GRACE_SECONDS = 120
_MAX_GRACE_SECONDS = 7200


def _compute_grace_seconds(schedule: dict) -> int:
    """How late a job can be and still catch up rather than fast-forward: half the period, clamped
    to [120s, 2h], so daily jobs catch up but frequent jobs fast-forward quickly."""
    period_seconds = _schedule_cadence_seconds(schedule)
    if not period_seconds:
        return _MIN_GRACE_SECONDS
    return max(_MIN_GRACE_SECONDS, min(int(period_seconds) // 2, _MAX_GRACE_SECONDS))


# A recurring dispatch within this many seconds of schedule renders "on time": a busy once-a-minute
# ticker can slip a couple of minutes — normal cadence, not gateway downtime.
# See #99879.
_LATE_DISPATCH_TOLERANCE_SECONDS = 300


def _classify_dispatch_lateness(lateness_seconds: float, grace_seconds: int) -> str:
    """``on_time`` (within ticker slack), ``late`` (within the catch-up grace window), or
    ``catch_up`` (beyond grace; accumulated misses skipped, executed once now)."""
    if lateness_seconds > grace_seconds:
        return "catch_up"
    if lateness_seconds > _LATE_DISPATCH_TOLERANCE_SECONDS:
        return "late"
    return "on_time"


# Per-expr cache for _schedule_cadence_seconds' croniter measurements.
_cron_cadence_cache: Dict[str, Optional[float]] = {}


def _schedule_cadence_seconds(schedule: Dict[str, Any]) -> Optional[float]:
    """Approximate schedule period in seconds, or None (croniter missing / malformed expr). Cron
    results are cached per expr because this runs under ``_jobs_lock`` every tick; the gap can vary
    with base time for irregular exprs, acceptable for a staleness *threshold*."""
    if not isinstance(schedule, dict):
        return None
    kind = schedule.get("kind")
    if kind == "interval":
        minutes = schedule.get("minutes")
        try:
            return float(minutes) * 60.0 if minutes else None
        except (TypeError, ValueError):
            return None
    if kind != "cron" or not _jobs._ensure_croniter():
        return None
    expr = schedule.get("expr")
    if not expr:
        return None
    if expr in _cron_cadence_cache:
        return _cron_cadence_cache[expr]
    try:
        it = _jobs.croniter(expr, _jobs._hermes_now())
        first = it.get_next(datetime)
        gap = (it.get_next(datetime) - first).total_seconds()
        result = gap if gap > 0 else None
    except Exception:
        result = None
    # Hard bound so deleted/edited exprs can't grow the cache unboundedly in a long-lived gateway.
    if len(_cron_cadence_cache) >= 256:
        _cron_cadence_cache.clear()
    _cron_cadence_cache[expr] = result
    return result


def _cron_next_run_matches_expr(schedule: Dict[str, Any], next_run_dt: datetime) -> bool:
    """Whether ``next_run_dt`` is an occurrence of the schedule's current expr (detects a
    hand-edited ``schedule.expr`` whose stored ``next_run_at`` came from the old one).
    Best-effort: anything uncheckable (non-cron, no expr, no croniter, malformed) reports a
    match.

    A direct ``jobs.json`` edit can change ``schedule.expr`` while leaving the stored ``next_run_at``
    computed under the *old* expression (#93049). The stored instant is stale exactly when it is not an
    occurrence of the current expression. Validation is best-effort: anything that cannot be checked
    (non-cron kind, missing expr, croniter unavailable, malformed input) reports a match so the fire path
    keeps its existing semantics.
    """
    if schedule.get("kind") != "cron":
        return True
    expr = schedule.get("expr")
    if not expr or not _jobs._ensure_croniter() or _jobs.croniter is None:
        return True
    try:
        # Last occurrence at-or-before the instant: base one second past it so an exact hit is
        # included, then compare at second granularity (croniter is second-precision).
        prev = _jobs.croniter(str(expr), next_run_dt + timedelta(seconds=1)).get_prev(datetime)
        return abs((prev - next_run_dt).total_seconds()) < 1.0
    except Exception:
        return True


# Classifications for a due cron instant NOT on the current expr (see
# _classify_stale_cron_next_run).
STALE_CRON_MATCH = "match"
STALE_CRON_TIMEZONE_MIGRATION = "timezone_migration"
STALE_CRON_EXPR_EDIT = "expr_edit"


def _classify_stale_cron_next_run(
    schedule: Dict[str, Any], raw_next_run_dt: datetime, next_run_dt: datetime,
) -> str:
    """Explain WHY a stored ``next_run_at`` misses the current cron lattice; the causes need
    opposite actions. ``expr_edit``: a hand edit changed ``schedule.expr`` — re-anchor WITHOUT
    firing. ``timezone_migration``: only the offset representation changed (legacy UTC rows
    normalized into the profile tz); treating it as an edit would skip a due, never-fired
    occurrence. Discriminator: normalization moved the wall clock AND the stored instant's OWN
    wall clock is a legal occurrence (when offsets agree a genuine expr edit can never be misread
    as a migration).

    * ``expr_edit`` — a direct ``jobs.json`` edit changed ``schedule.expr`` while leaving ``next_run_at``
    computed under the old one (#93049). Upgrading from a UTC-scheduling build to one that honours the
    profile timezone leaves legacy rows like ``2026-09-02T04:00:00+00:00`` for ``0 4 * * *``; normalizing to
    Europe/Brussels turns that into ``06:00+02``, which the expression excludes. Treating it as a stale edit
    re-anchored to tomorrow and silently skipped a due occurrence that had never fired.
    """
    if _cron_next_run_matches_expr(schedule, next_run_dt):
        return STALE_CRON_MATCH
    wall_clock_shifted = raw_next_run_dt.replace(tzinfo=None) != next_run_dt.replace(tzinfo=None)
    if wall_clock_shifted and _cron_next_run_matches_expr(schedule, raw_next_run_dt):
        return STALE_CRON_TIMEZONE_MIGRATION
    return STALE_CRON_EXPR_EDIT


def compute_next_run(schedule: Dict[str, Any], last_run_at: Optional[str] = None) -> Optional[str]:
    """Compute the next run time for a schedule as an ISO string, or None if no more runs."""
    now = _jobs._hermes_now()
    if not isinstance(schedule, dict):
        return None
    kind = schedule.get("kind")
    if kind == "once":
        return _recoverable_oneshot_run_at(schedule, now, last_run_at=last_run_at)
    # Recurring kinds anchor on last_run_at so a restart doesn't re-anchor the schedule.
    base_time = (_parse_aware(last_run_at) if last_run_at else None) or now
    if kind == "interval":
        minutes = schedule.get("minutes")
        if minutes is None:
            return None
        # Add in UTC so an interval keeps its duration when the profile's UTC offset changes.
        next_run = base_time.astimezone(timezone.utc) + timedelta(minutes=minutes)
        return next_run.astimezone(base_time.tzinfo).isoformat()
    if kind == "cron":
        expr = schedule.get("expr")
        if not expr:
            return None
        if not _jobs._ensure_croniter():
            logger.warning(
                "Cannot compute next run for cron schedule %r: 'croniter' is "
                "not installed. croniter is a core dependency as of v0.9.x; "
                "reinstall hermes-agent or run 'pip install croniter' in your runtime env.",
                expr)
            return None
        # Anchor cron matching to the CONFIGURED IANA timezone's WALL CLOCK,
        # not to the UTC offset carried by ``base_time``. croniter ignores
        # the tzinfo on its start time and uses the start's UTC offset as its
        # working offset, so a ``last_run_at`` stored in UTC (+00:00) would
        # push the next fire to 09:00 UTC instead of 09:00 local, and DST
        # transition days (spring-forward / fall-back) would land one hour off
        # (08:00 or 10:00). Render the base as the configured zone's naive
        # wall clock for croniter, then re-attach the zone to the result, so
        # the wall-clock hour stays correct every calendar day, including DST
        # boundaries (morning-routine 09:00 America/Toronto).
        # Fall back to the base's own zone only when nothing is configured.
        zone = _jobs.get_timezone() or base_time.tzinfo
        base_wall = base_time.astimezone(zone).replace(tzinfo=None)
        it = _jobs.croniter(expr, base_wall)
        # Strictly-after guard for the DST fall-back hour (qwen-code#11723 class):
        # attaching the zone to a naive wall clock resolves the repeated autumn hour
        # to its EARLIER occurrence (fold=0), so a base inside the second occurrence
        # got a "next run" up to an hour in the PAST — the fire path would re-fire
        # immediately and re-anchor, looping. Try both folds of each candidate wall
        # clock and return the earliest instant strictly after the base; a repeated
        # hour has two instants, so two candidates always suffice.
        base_ts = base_time.timestamp()
        next_wall = it.get_next(datetime)
        for _ in range(2):
            for fold in (0, 1):
                candidate = next_wall.replace(tzinfo=zone, fold=fold)
                if candidate.timestamp() > base_ts:
                    return candidate.isoformat()
            next_wall = it.get_next(datetime)
        return next_wall.replace(tzinfo=zone).isoformat()
    return None


# Late-bound origin namespace: imported LAST so this module is fully populated
# before ``cron.jobs`` re-exports from it.
from cron import jobs as _jobs  # noqa: E402
