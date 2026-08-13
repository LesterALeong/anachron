"""Paired inference for Anachron's unrestricted and enforced run modes."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from math import comb, floor, isfinite


@dataclass(frozen=True)
class ModeComparison:
    """Effect size and paired evidence for temporal enforcement.

    Positive ``mean_reduction`` means the enforced mode leaked less. The
    confidence interval is a paired percentile-bootstrap interval over sample
    differences. ``sign_test_p_value`` is an exact two-sided sign test over the
    non-tied pairs, providing a distribution-free check that improvements are
    not balanced by regressions.
    """

    n: int
    unrestricted_mean: float
    enforced_mean: float
    mean_reduction: float
    ci_low: float
    ci_high: float
    confidence: float
    relative_reduction: float | None
    improved_samples: int
    worsened_samples: int
    unchanged_samples: int
    sign_test_p_value: float
    n_resamples: int

    def to_dict(self) -> dict[str, int | float | None]:
        """Return a JSON-serializable representation of the comparison."""
        return asdict(self)

    def table(self) -> str:
        """Render a compact research summary suitable for logs and reports."""
        interval_level = self.confidence * 100
        relative = (
            "n/a"
            if self.relative_reduction is None
            else f"{self.relative_reduction * 100:.1f}%"
        )
        return "\n".join(
            [
                f"Paired mode comparison (n={self.n})",
                f"  unrestricted mean TCLR  {self.unrestricted_mean:.3f}",
                f"  enforced mean TCLR      {self.enforced_mean:.3f}",
                f"  mean reduction           {self.mean_reduction:+.3f}",
                f"  {interval_level:g}% paired bootstrap CI  "
                f"[{self.ci_low:+.3f}, {self.ci_high:+.3f}]",
                f"  relative reduction       {relative}",
                f"  improved / worsened / tied  {self.improved_samples} / "
                f"{self.worsened_samples} / {self.unchanged_samples}",
                f"  exact sign-test p         {self.sign_test_p_value:.4f}",
            ]
        )


def _validate_scores(name: str, scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        raise ValueError(f"{name} scores must be non-empty")

    validated: dict[str, float] = {}
    for sample_id, value in scores.items():
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"{name} sample ids must be non-empty strings")
        try:
            score = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name}[{sample_id!r}] must be a numeric TCLR") from error
        if not isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError(
                f"{name}[{sample_id!r}] must be a finite TCLR in [0, 1]; got {value!r}"
            )
        validated[sample_id] = score
    return validated


def _percentile(sorted_values: list[float], percentile: float) -> float:
    rank = (len(sorted_values) - 1) * percentile
    lower = floor(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _exact_sign_test(improved: int, worsened: int) -> float:
    """Two-sided exact binomial sign test, excluding tied pairs."""
    changed = improved + worsened
    if changed == 0:
        return 1.0
    less_common = min(improved, worsened)
    tail = sum(comb(changed, k) for k in range(less_common + 1)) / (2**changed)
    return min(1.0, 2.0 * tail)


def compare_modes(
    unrestricted: Mapping[str, float],
    enforced: Mapping[str, float],
    *,
    confidence: float = 0.95,
    n_resamples: int = 10_000,
    seed: int | None = 0,
) -> ModeComparison:
    """Compare per-sample TCLR scores from the two run modes.

    The mappings must contain exactly the same sample ids. Resampling happens
    over paired differences, so task difficulty is held constant rather than
    treated as independent noise. Results are deterministic by default.
    """
    unrestricted_scores = _validate_scores("unrestricted", unrestricted)
    enforced_scores = _validate_scores("enforced", enforced)
    if unrestricted_scores.keys() != enforced_scores.keys():
        missing = sorted(unrestricted_scores.keys() - enforced_scores.keys())
        extra = sorted(enforced_scores.keys() - unrestricted_scores.keys())
        raise ValueError(
            "mode scores must contain identical sample ids; "
            f"missing from enforced={missing}, only in enforced={extra}"
        )
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1); got {confidence}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1; got {n_resamples}")

    sample_ids = sorted(unrestricted_scores)
    differences = [
        unrestricted_scores[sample_id] - enforced_scores[sample_id]
        for sample_id in sample_ids
    ]
    n = len(differences)
    unrestricted_mean = sum(unrestricted_scores.values()) / n
    enforced_mean = sum(enforced_scores.values()) / n
    mean_reduction = sum(differences) / n

    rng = random.Random(seed)
    bootstrap_means = []
    for _ in range(n_resamples):
        bootstrap_means.append(sum(differences[rng.randrange(n)] for _ in range(n)) / n)
    bootstrap_means.sort()
    alpha = 1.0 - confidence
    ci_low = _percentile(bootstrap_means, alpha / 2.0)
    ci_high = _percentile(bootstrap_means, 1.0 - alpha / 2.0)

    improved = sum(difference > 0.0 for difference in differences)
    worsened = sum(difference < 0.0 for difference in differences)
    unchanged = n - improved - worsened
    relative_reduction = (
        mean_reduction / unrestricted_mean if unrestricted_mean > 0.0 else None
    )

    return ModeComparison(
        n=n,
        unrestricted_mean=unrestricted_mean,
        enforced_mean=enforced_mean,
        mean_reduction=mean_reduction,
        ci_low=ci_low,
        ci_high=ci_high,
        confidence=confidence,
        relative_reduction=relative_reduction,
        improved_samples=improved,
        worsened_samples=worsened,
        unchanged_samples=unchanged,
        sign_test_p_value=_exact_sign_test(improved, worsened),
        n_resamples=n_resamples,
    )
