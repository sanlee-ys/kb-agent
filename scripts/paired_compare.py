"""Paired comparison core: group keys, pairing, paired summaries, diagnostics.

VENDORED from defense-news-classifier `src/paired_compare.py` at merge commit
39edea4eeed9a27dee80b618d730bcc5c4a00003 (PR #149, merged 2026-08-01), with
`mcnemar_exact` vendored alongside from that repo's `src/baseline_ml.py`.
Vendored rather than depended on: the core is ~250 lines of stdlib-only code,
and a cross-repo dependency would couple two portfolio repos' release cycles
for less code than this docstring block. Omitted from the vendoring: the
classifier's prediction-CSV adapter and CLI (pandas-based, classifier-specific)
— this repo's adapter is `scripts/eval_compare.py`.

What this module is for: every A/B re-derives the same plumbing by hand — line
up two runs, count who won, hope nothing was silently dropped on the way. The
pairing is the easy part; what keeps getting re-litigated is the *bookkeeping*
— a row missing from one arm, a row scored twice — because a comparison that
quietly drops rows reports a lift that never happened. Three rules, in the
order they matter:

1. **Every observation gets a deterministic group key.** An explicit ``id`` if
   the record carries one, otherwise the SHA-256 of a canonicalized JSON
   serialization of the record. Canonicalization sorts keys and **fails loud**
   on anything it cannot represent honestly — a lossy key silently merges two
   different inputs into one "pair", which is worse than no key at all.
2. **Metrics are computed over PAIRS, and only over pairs where both arms
   scored.** A missing observation is never imputed and never counted as a
   zero: an arm that crashed on the hard rows would otherwise look better the
   more it crashed. Unmatched rows leave the numerator *and* the denominator.
3. **"Is this comparison trustworthy" is a separate output from "what did it
   find".** The harness-health section enumerates every observation that did
   not participate and why. Reading the lift without reading that section is
   the mistake this split exists to make difficult.

The group universe is the union of the two arms' group keys — a group exists
because some arm produced it. So ``missing-observation`` means "the other arm
has this row and this one does not".
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_MAX_LISTED_DIAGNOSTICS = 20


class CanonicalizationError(ValueError):
    """A record could not be canonicalized into a faithful JSON serialization.

    Raised instead of degrading to a lossy key: ``NaN``, a circular reference,
    or a non-plain container all mean the caller's record cannot be hashed
    honestly, and a group key that collides is undetectable downstream.
    """


class Outcome(StrEnum):
    """What happened to one observation, from the harness's point of view."""

    SCORED = "scored"
    ERRORED = "errored"
    UNSCORED = "unscored"
    UNSCORABLE = "unscorable"


class DiagnosticReason(StrEnum):
    """Why an observation did not participate in the paired comparison."""

    MISSING_OBSERVATION = "missing-observation"
    DUPLICATE_OBSERVATION = "duplicate-observation"
    HARNESS_ERROR = "harness-error"
    MISSING_SCORE = "missing-score"
    UNSCORABLE_OUTCOME = "unscorable-outcome"


_OUTCOME_REASONS = {
    Outcome.ERRORED: DiagnosticReason.HARNESS_ERROR,
    Outcome.UNSCORED: DiagnosticReason.MISSING_SCORE,
    Outcome.UNSCORABLE: DiagnosticReason.UNSCORABLE_OUTCOME,
}


# ---------------------------------------------------------------------------
# 1. Deterministic group keys.
# ---------------------------------------------------------------------------


def canonicalize(value: Any) -> Any:
    """Return a canonical, JSON-serializable copy of ``value``, or fail loudly.

    Dict keys are sorted so that two records differing only in key order
    produce the same serialization. Everything that cannot be represented
    exactly in JSON is rejected rather than coerced.

    Args:
        value: Any Python value intended to identify an eval input.

    Returns:
        The same data with every mapping's keys sorted, built only from
        ``None``, ``bool``, ``int``, ``float``, ``str``, ``list``, and ``dict``.

    Raises:
        CanonicalizationError: On a non-finite float (``nan``/``inf``), a
            circular reference, a non-string mapping key, a tuple/set/custom
            object, or a mapping/sequence that is not a plain ``dict``/``list``.
    """
    return _canonicalize(value, set())


def _canonicalize(value: Any, ancestors: set[int]) -> Any:
    """Recursive worker for ``canonicalize``; ``ancestors`` holds container ids."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(
                f"eval input must contain only finite numbers, got {value!r}"
            )
        return value

    # Plain containers only: a dict subclass or a Mapping that is not a dict can
    # carry behavior (defaults, ordering, lazy lookup) that a serialization
    # cannot capture, so it is refused rather than flattened.
    if type(value) is list:
        if id(value) in ancestors:
            raise CanonicalizationError("eval input must not contain circular references")
        ancestors.add(id(value))
        try:
            return [_canonicalize(item, ancestors) for item in value]
        finally:
            ancestors.discard(id(value))
    if type(value) is dict:
        if id(value) in ancestors:
            raise CanonicalizationError("eval input must not contain circular references")
        ancestors.add(id(value))
        try:
            items = []
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalizationError(
                        f"eval input mapping keys must be strings, got {key!r}"
                    )
                items.append((key, _canonicalize(item, ancestors)))
            return dict(sorted(items, key=lambda pair: pair[0]))
        finally:
            ancestors.discard(id(value))

    raise CanonicalizationError(
        "eval input must contain only plain dicts, lists, strings, finite "
        f"numbers, booleans, and None, got {type(value).__name__}"
    )


def canonical_json(value: Any) -> str:
    """Serialize ``value`` canonically: sorted keys, no insignificant whitespace.

    Args:
        value: Any Python value intended to identify an eval input.

    Returns:
        A compact JSON string. Two records that differ only in key order
        serialize identically.

    Raises:
        CanonicalizationError: If ``value`` cannot be canonicalized.
    """
    return json.dumps(canonicalize(value), separators=(",", ":"), ensure_ascii=False)


def derive_group_key(record: Any, id_field: str = "id") -> str:
    """Derive the deterministic key two arms are paired on.

    An explicit id wins, because it is the identity the eval author chose. A
    record without one is keyed by content, so the same input evaluated by two
    arms still pairs up.

    Args:
        record: The eval input. A mapping carrying a non-empty string-able
            ``id_field`` is keyed by that id; anything else is keyed by content.
        id_field: Which field holds the explicit id.

    Returns:
        The trimmed explicit id, or the hex SHA-256 of the canonical JSON.

    Raises:
        CanonicalizationError: If a record without a usable id cannot be
            canonicalized.
    """
    if isinstance(record, Mapping) and id_field in record:
        raw = record[id_field]
        if raw is not None and not isinstance(raw, (dict, list)):
            # A float id (a NaN from a blank CSV cell) is not an identity; fall
            # through to content-hashing, which will fail loud on the NaN.
            if not isinstance(raw, float):
                candidate = str(raw).strip()
                if candidate:
                    return candidate
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 2. Observations, pairing, and paired summaries.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One arm's result for one group key."""

    group_key: str
    arm: str
    outcome: Outcome
    score: float | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class Diagnostic:
    """One reason one arm's observation did not participate."""

    group_key: str
    arm: str
    reason: DiagnosticReason
    detail: str = ""


@dataclass(frozen=True)
class ObservationPair:
    """A baseline and candidate observation sharing one group key."""

    group_key: str
    baseline: Observation
    candidate: Observation

    @property
    def both_scored(self) -> bool:
        """True when both sides scored and so may enter a paired metric."""
        return self.baseline.outcome is Outcome.SCORED and self.candidate.outcome is Outcome.SCORED


@dataclass(frozen=True)
class PairingResult:
    """The pairs a comparison may use, and everything it had to leave out."""

    pairs: list[ObservationPair]
    diagnostics: list[Diagnostic]
    total_groups: int


@dataclass(frozen=True)
class CorrectnessLift:
    """Paired pass-rate summary: how often each arm was right, and who won."""

    total_pairs: int
    eligible_pairs: int
    baseline_pass_rate: float | None
    candidate_pass_rate: float | None
    lift: float | None
    baseline_wins: int
    candidate_wins: int
    ties: int
    p_value: float


@dataclass(frozen=True)
class PairedMetric:
    """Paired mean summary for one continuous metric."""

    name: str
    total_pairs: int
    eligible_pairs: int
    baseline_mean: float | None
    candidate_mean: float | None
    mean_delta: float | None


def mcnemar_exact(both_wrong_a_only: int, both_wrong_b_only: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pair counts.

    Hand-rolled binomial test (vendored from defense-news-classifier
    ``src/baseline_ml.py``, per its ADR-004: no framework for metric
    computation). Under H0 the discordant rows split 50/50 between the two
    systems.

    Args:
        both_wrong_a_only: Rows system A got wrong and system B got right.
        both_wrong_b_only: Rows system B got wrong and system A got right.

    Returns:
        Two-sided p-value; 1.0 when there are no discordant rows.
    """
    n = both_wrong_a_only + both_wrong_b_only
    if n == 0:
        return 1.0
    k = min(both_wrong_a_only, both_wrong_b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def pair_observations(
    baseline: Iterable[Observation], candidate: Iterable[Observation]
) -> PairingResult:
    """Line two arms up on the group key, recording every non-participant.

    A group contributes a pair only when each arm supplied **exactly one**
    observation for it. Zero observations is ``missing-observation``; more than
    one is ``duplicate-observation`` and the group is dropped from both arms —
    with duplicates there is no principled way to pick which run counts, and
    picking silently is how a comparison becomes a coin flip.

    Args:
        baseline: The baseline arm's observations.
        candidate: The candidate arm's observations.

    Returns:
        A ``PairingResult`` whose ``pairs`` are structurally valid (one
        observation per side) and whose ``diagnostics`` explain every
        observation that did not make it, including scored-but-unpaired ones.
    """
    grouped: dict[str, dict[str, list[Observation]]] = defaultdict(
        lambda: {"baseline": [], "candidate": []}
    )
    baseline_arm = ""
    candidate_arm = ""
    for observation in baseline:
        grouped[observation.group_key]["baseline"].append(observation)
        baseline_arm = baseline_arm or observation.arm
    for observation in candidate:
        grouped[observation.group_key]["candidate"].append(observation)
        candidate_arm = candidate_arm or observation.arm
    baseline_arm = baseline_arm or "baseline"
    candidate_arm = candidate_arm or "candidate"

    pairs: list[ObservationPair] = []
    diagnostics: list[Diagnostic] = []
    for group_key in sorted(grouped):
        sides = grouped[group_key]
        structural = False
        for side, arm_name in (
            ("baseline", baseline_arm),
            ("candidate", candidate_arm),
        ):
            observations = sides[side]
            if len(observations) == 0:
                diagnostics.append(
                    Diagnostic(
                        group_key,
                        arm_name,
                        DiagnosticReason.MISSING_OBSERVATION,
                        f"no observation from {side} arm",
                    )
                )
                structural = True
            elif len(observations) > 1:
                diagnostics.append(
                    Diagnostic(
                        group_key,
                        observations[0].arm,
                        DiagnosticReason.DUPLICATE_OBSERVATION,
                        f"{len(observations)} observations for one group key",
                    )
                )
                structural = True
        if structural:
            continue

        pair = ObservationPair(group_key, sides["baseline"][0], sides["candidate"][0])
        pairs.append(pair)
        for observation in (pair.baseline, pair.candidate):
            reason = _OUTCOME_REASONS.get(observation.outcome)
            if reason is not None:
                diagnostics.append(
                    Diagnostic(group_key, observation.arm, reason, observation.detail)
                )
    return PairingResult(pairs, diagnostics, len(grouped))


def summarize_correctness(pairs: Sequence[ObservationPair]) -> CorrectnessLift:
    """Paired pass rates, lift, and per-pair wins over the both-scored pairs.

    Pass is ``score >= 1`` (a binary axis scores 1.0 or 0.0). The lift is a
    paired difference over one shared set of pairs, so it is not the difference
    of two independently-computed rates over two different row sets — that
    difference is the one that lies.

    Args:
        pairs: Pairs from ``pair_observations``; ineligible ones are skipped.

    Returns:
        A ``CorrectnessLift``. Rates and lift are ``None`` when no pair is
        eligible, never 0.0 — "nothing to compare" is not "no difference".
    """
    eligible = [pair for pair in pairs if pair.both_scored]
    baseline_passes = 0
    candidate_passes = 0
    baseline_wins = 0
    candidate_wins = 0
    ties = 0
    for pair in eligible:
        baseline_passed = (pair.baseline.score or 0.0) >= 1
        candidate_passed = (pair.candidate.score or 0.0) >= 1
        baseline_passes += int(baseline_passed)
        candidate_passes += int(candidate_passed)
        if baseline_passed == candidate_passed:
            ties += 1
        elif baseline_passed:
            baseline_wins += 1
        else:
            candidate_wins += 1

    n = len(eligible)
    baseline_rate = baseline_passes / n if n else None
    candidate_rate = candidate_passes / n if n else None
    return CorrectnessLift(
        total_pairs=len(pairs),
        eligible_pairs=n,
        baseline_pass_rate=baseline_rate,
        candidate_pass_rate=candidate_rate,
        lift=(
            None
            if baseline_rate is None or candidate_rate is None
            else candidate_rate - baseline_rate
        ),
        baseline_wins=baseline_wins,
        candidate_wins=candidate_wins,
        ties=ties,
        # Discordant pairs only, which is exactly what the wins are.
        p_value=mcnemar_exact(candidate_wins, baseline_wins),
    )


def summarize_metric(
    pairs: Sequence[ObservationPair],
    name: str,
    select: Callable[[Observation], float | None] | None = None,
) -> PairedMetric:
    """Paired means for one continuous metric across the both-scored pairs.

    Args:
        pairs: Pairs from ``pair_observations``.
        name: Metric name; also the default ``Observation.metrics`` lookup key.
        select: Optional accessor overriding the ``metrics[name]`` lookup.
            Returning ``None`` drops that pair from this metric only.

    Returns:
        A ``PairedMetric``; means and delta are ``None`` when no pair carries
        finite values for the metric on both sides.
    """
    accessor = select or (lambda observation: observation.metrics.get(name))
    baseline_values: list[float] = []
    candidate_values: list[float] = []
    for pair in pairs:
        if not pair.both_scored:
            continue
        baseline_value = accessor(pair.baseline)
        candidate_value = accessor(pair.candidate)
        if baseline_value is None or candidate_value is None:
            continue
        if not math.isfinite(baseline_value) or not math.isfinite(candidate_value):
            continue
        baseline_values.append(float(baseline_value))
        candidate_values.append(float(candidate_value))

    n = len(baseline_values)
    baseline_mean = sum(baseline_values) / n if n else None
    candidate_mean = sum(candidate_values) / n if n else None
    return PairedMetric(
        name=name,
        total_pairs=len(pairs),
        eligible_pairs=n,
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        mean_delta=(
            None
            if baseline_mean is None or candidate_mean is None
            else candidate_mean - baseline_mean
        ),
    )


# ---------------------------------------------------------------------------
# 3. Harness health — a separate answer to a separate question.
# ---------------------------------------------------------------------------


def diagnostic_counts(diagnostics: Iterable[Diagnostic]) -> dict[str, int]:
    """Count diagnostics by reason, in the fixed reason order.

    Args:
        diagnostics: Diagnostics from ``pair_observations``.

    Returns:
        Dict from reason to count, including reasons with a zero count so a
        clean run and an unexamined one look different.
    """
    counts = Counter(diagnostic.reason for diagnostic in diagnostics)
    return {reason.value: counts.get(reason, 0) for reason in DiagnosticReason}


# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------


def _optional(value: float | None, spec: str = ".1%") -> str:
    """Format an optional number, distinguishing 'nothing to compare' from zero.

    Args:
        value: The number, or ``None`` when there was nothing to compute it over.
        spec: A format spec applied when the value is present.

    Returns:
        The formatted number, or ``"n/a"``.
    """
    return "n/a" if value is None else format(value, spec)


def _rate(value: float | None) -> str:
    """Format an optional rate as a percentage, or ``"n/a"``.

    Args:
        value: The rate, or ``None``.

    Returns:
        The formatted percentage, or ``"n/a"``.
    """
    return _optional(value, ".1%")


def build_report(
    result: PairingResult,
    lift: CorrectnessLift,
    baseline_name: str,
    candidate_name: str,
    axis: str,
    metrics: Sequence[PairedMetric] = (),
) -> str:
    """Format the two reports: what the comparison found, and whether to trust it.

    Args:
        result: Output of ``pair_observations``.
        lift: Output of ``summarize_correctness``.
        baseline_name: Label for the baseline arm.
        candidate_name: Label for the candidate arm.
        axis: What was compared (shown in the report header).
        metrics: Optional continuous metrics from ``summarize_metric``.

    Returns:
        Multi-line report string.
    """
    lines = [
        "=" * 62,
        f"PAIRED COMPARISON -- {axis}",
        "=" * 62,
        "",
        f"Baseline  : {baseline_name}",
        f"Candidate : {candidate_name}",
        "",
        f"Groups seen        : {result.total_groups}",
        f"Structural pairs   : {lift.total_pairs}   (one observation from each arm)",
        f"Eligible pairs     : {lift.eligible_pairs}   (both arms scored -- the ONLY rows below)",
        "",
        f"Baseline pass rate : {_rate(lift.baseline_pass_rate)}",
        f"Candidate pass rate: {_rate(lift.candidate_pass_rate)}",
        f"Paired lift        : {_optional(lift.lift, '+.1%')}",
        "",
        f"Per-pair wins      : candidate {lift.candidate_wins}, "
        f"baseline {lift.baseline_wins}, ties {lift.ties}",
        f"McNemar (exact)    : p={lift.p_value:.4f}   (over the discordant pairs only)",
    ]
    for metric in metrics:
        lines += [
            "",
            f"-- {metric.name} (paired mean over {metric.eligible_pairs} pairs) --",
            f"  baseline : {_optional(metric.baseline_mean, '.4f')}",
            f"  candidate: {_optional(metric.candidate_mean, '.4f')}",
            f"  delta    : {_optional(metric.mean_delta, '+.4f')}",
        ]

    counts = diagnostic_counts(result.diagnostics)
    lines += [
        "",
        "=" * 62,
        "HARNESS HEALTH -- why observations did not participate",
        "=" * 62,
        "",
        "Separate from the numbers above on purpose: a clean lift computed",
        "over a harness that dropped a third of its rows is not a finding.",
        "",
    ]
    for reason, count in counts.items():
        lines.append(f"  {reason:22s} : {count}")
    if result.diagnostics:
        lines += ["", "  Affected groups:"]
        for diagnostic in result.diagnostics[:_MAX_LISTED_DIAGNOSTICS]:
            detail = f" -- {diagnostic.detail}" if diagnostic.detail else ""
            lines.append(
                f"    {diagnostic.group_key:24s} {diagnostic.arm:16s} "
                f"{diagnostic.reason.value}{detail}"
            )
        remaining = len(result.diagnostics) - _MAX_LISTED_DIAGNOSTICS
        if remaining > 0:
            lines.append(f"    ... and {remaining} more")
    else:
        lines += ["", "  Clean: every group paired and scored on both arms."]
    lines.append("=" * 62)
    return "\n".join(lines) + "\n"
