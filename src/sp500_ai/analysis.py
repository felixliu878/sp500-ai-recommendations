from __future__ import annotations

from typing import Any

import pandas as pd


RATING_SCORE = {
    "STRONG_BUY": 2,
    "BUY": 1,
    "HOLD": 0,
    "SELL": -1,
    "STRONG_SELL": -2,
}


def analyst_score(counts: dict[str, Any]) -> float | None:
    strong_buy = int(counts.get("strong_buy", counts.get("strongBuy", 0)) or 0)
    buy = int(counts.get("buy", 0) or 0)
    hold = int(counts.get("hold", 0) or 0)
    sell = int(counts.get("sell", 0) or 0)
    strong_sell = int(counts.get("strong_sell", counts.get("strongSell", 0)) or 0)
    total = strong_buy + buy + hold + sell + strong_sell
    if total == 0:
        return None
    return (2 * strong_buy + buy - sell - 2 * strong_sell) / total


def score_to_rating(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 1.5:
        return "STRONG_BUY"
    if score >= 0.5:
        return "BUY"
    if score > -0.5:
        return "HOLD"
    if score > -1.5:
        return "SELL"
    return "STRONG_SELL"


def attach_analyst_comparison(
    recommendations: pd.DataFrame, consensus: pd.DataFrame
) -> pd.DataFrame:
    if recommendations.empty:
        return recommendations.copy()
    result = recommendations.copy()
    if consensus.empty:
        result["analyst_score"] = pd.NA
        result["analyst_rating"] = pd.NA
        result["ordinal_gap_vs_analyst"] = pd.NA
        result["exact_match_analyst"] = pd.NA
        return result
    lookup = consensus.set_index("symbol")
    result["analyst_score"] = result["symbol"].map(lookup["analyst_score"])
    result["analyst_rating"] = result["symbol"].map(lookup["analyst_rating"])
    result["ordinal_gap_vs_analyst"] = result.apply(
        lambda row: (
            row["rating_score"] - RATING_SCORE[row["analyst_rating"]]
            if pd.notna(row["analyst_rating"])
            else pd.NA
        ),
        axis=1,
    )
    result["exact_match_analyst"] = result.apply(
        lambda row: row["rating"] == row["analyst_rating"]
        if pd.notna(row["analyst_rating"])
        else pd.NA,
        axis=1,
    )
    return result


def condition_flip_summary(recommendations: pd.DataFrame) -> pd.DataFrame:
    if recommendations.empty:
        return pd.DataFrame()
    pivot = recommendations.pivot_table(
        index=["symbol", "repetition"], columns="condition", values="rating_score", aggfunc="first"
    )
    rows = []
    conditions = list(pivot.columns)
    for i, left in enumerate(conditions):
        for right in conditions[i + 1 :]:
            pair = pivot[[left, right]].dropna()
            rows.append(
                {
                    "left_condition": left,
                    "right_condition": right,
                    "observations": int(len(pair)),
                    "flip_rate": float((pair[left] != pair[right]).mean()) if len(pair) else None,
                    "mean_absolute_ordinal_change": float((pair[left] - pair[right]).abs().mean())
                    if len(pair)
                    else None,
                }
            )
    return pd.DataFrame(rows)
