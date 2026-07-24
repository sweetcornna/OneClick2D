"""Deterministic blinding, adjudication, and exact Gate F paired statistics."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

from .contracts import StageContractError
from .runtime import canonical_json_bytes, digest_framed


@dataclass(frozen=True)
class ArmIdentity:
    normalized_raster_sha256: str
    sequence_sha256: str
    renderer_contract_id: str
    renderer_profile_id: str
    width: int
    height: int
    frame_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlindMapping:
    asset_id: str
    left_arm: str
    right_arm: str
    sha256: str


@dataclass(frozen=True)
class PairOutcome:
    asset_id: str
    outcome: str
    f_usable: bool
    reason: str


def validate_arm_parity(candidate: ArmIdentity, comparator: ArmIdentity) -> None:
    if candidate != comparator:
        raise StageContractError("candidate and comparator arm identities do not match")


def build_blind_mapping(asset_id: str, seed_u64: str) -> BlindMapping:
    if not asset_id or not seed_u64.isdigit() or len(seed_u64) != 20 or int(seed_u64) > 18_446_744_073_709_551_615:
        raise StageContractError("invalid blind mapping input")
    digest = hashlib.sha256(b"oneclick2d.gate-f.blind-mapping.v1\0" + int(seed_u64).to_bytes(8, "big") + b"\0" + asset_id.encode("ascii")).digest()
    left, right = ("candidate", "comparator") if digest[0] & 1 == 0 else ("comparator", "candidate")
    document = {"asset_id": asset_id, "left_arm": left, "right_arm": right}
    return BlindMapping(asset_id, left, right, digest_framed("oneclick2d.gate-f.blind-mapping.v1", (canonical_json_bytes(document),)))


def adjudicate_ballots(votes: Iterable[str]) -> str:
    ballots = tuple(votes)
    if len(ballots) not in {2, 3} or any(vote not in {"left", "right", "tie"} for vote in ballots):
        raise StageContractError("invalid review ballots")
    if ballots[0] == ballots[1]:
        return ballots[0]
    if len(ballots) != 3:
        raise StageContractError("a third ballot is required for disagreement")
    counts = {vote: ballots.count(vote) for vote in {"left", "right", "tie"}}
    winners = [vote for vote, count in counts.items() if count >= 2]
    return winners[0] if len(winners) == 1 else "tie"


def reveal_outcome(mapping: BlindMapping, blind_outcome: str) -> str:
    if blind_outcome == "tie":
        return "tie"
    if blind_outcome not in {"left", "right"}:
        raise StageContractError("invalid blind outcome")
    winning_arm = mapping.left_arm if blind_outcome == "left" else mapping.right_arm
    return "candidate_win" if winning_arm == "candidate" else "comparator_win"


def classify_pair(
    asset_id: str,
    *,
    candidate_status: str,
    comparator_status: str,
    review_outcome: str | None,
    candidate_f_usable: bool,
) -> PairOutcome:
    if comparator_status != "succeeded":
        return PairOutcome(asset_id, "invalid", False, "comparator_infrastructure_failure")
    if candidate_status != "succeeded":
        return PairOutcome(asset_id, "comparator_win", False, "candidate_failure")
    if review_outcome is None:
        return PairOutcome(asset_id, "tie", candidate_f_usable, "missing_review_evidence")
    if review_outcome not in {"candidate_win", "comparator_win", "tie"}:
        raise StageContractError("invalid revealed review outcome")
    return PairOutcome(asset_id, review_outcome, candidate_f_usable, "reviewed")


def exact_binomial_upper_tail(wins: int, losses: int) -> Fraction:
    if wins < 0 or losses < 0 or wins + losses == 0:
        raise StageContractError("exact binomial requires at least one non-tie")
    total = wins + losses
    return Fraction(sum(math.comb(total, count) for count in range(wins, total + 1)), 2**total)


def _binomial_cdf(k: int, n: int, probability: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, count) * probability**count * (1.0 - probability) ** (n - count) for count in range(k + 1))


def clopper_pearson_interval(wins: int, losses: int, alpha: float = 0.05) -> tuple[float, float]:
    if wins < 0 or losses < 0 or wins + losses == 0 or not 0.0 < alpha < 1.0:
        raise StageContractError("invalid Clopper-Pearson inputs")
    total = wins + losses
    if wins == 0:
        lower = 0.0
    else:
        low, high = 0.0, wins / total
        for _ in range(100):
            middle = (low + high) / 2
            tail = 1.0 - _binomial_cdf(wins - 1, total, middle)
            if tail > alpha / 2:
                high = middle
            else:
                low = middle
        lower = (low + high) / 2
    if wins == total:
        upper = 1.0
    else:
        low, high = wins / total, 1.0
        for _ in range(100):
            middle = (low + high) / 2
            cdf = _binomial_cdf(wins, total, middle)
            if cdf > alpha / 2:
                low = middle
            else:
                high = middle
        upper = (low + high) / 2
    return lower, upper


def evaluate_experiment(outcomes: Iterable[PairOutcome]) -> dict[str, object]:
    expected_assets = 20
    rows = tuple(outcomes)
    if len(rows) != expected_assets or len({row.asset_id for row in rows}) != len(rows):
        raise StageContractError("experiment asset denominator is invalid")
    if any(row.outcome == "invalid" for row in rows):
        raise StageContractError("experiment contains an invalid comparator pair")
    wins = sum(row.outcome == "candidate_win" for row in rows)
    losses = sum(row.outcome == "comparator_win" for row in rows)
    ties = sum(row.outcome == "tie" for row in rows)
    if wins + losses + ties != expected_assets:
        raise StageContractError("experiment outcomes do not cover the denominator")
    p_value = exact_binomial_upper_tail(wins, losses) if wins + losses else Fraction(1)
    interval = clopper_pearson_interval(wins, losses) if wins + losses else (0.0, 1.0)
    usable = sum(row.f_usable for row in rows)
    superiority = wins - losses >= 4 and p_value < Fraction(1, 20)
    return {
        "asset_count": expected_assets,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "net_margin": wins - losses,
        "net_margin_rate": (wins - losses) / expected_assets,
        "exact_one_sided_p": float(p_value),
        "exact_one_sided_p_fraction": f"{p_value.numerator}/{p_value.denominator}",
        "clopper_pearson_95": [interval[0], interval[1]],
        "f_usable_count": usable,
        "superiority_pass": superiority,
        "f_usable_pass": usable >= 12,
        "primary_pair_rule_pass": superiority and usable >= 12,
    }
