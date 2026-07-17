from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PromptBuilder:
    def __init__(self, prompt_dir: Path, prompt_version: str):
        self.prompt_dir = prompt_dir
        self.prompt_version = prompt_version
        self.base = (prompt_dir / "base.txt").read_text(encoding="utf-8").strip()

    def build(
        self,
        condition: str,
        company: str,
        symbol: str,
        sector: str,
        as_of_date: str,
        horizon_months: int,
        packet: dict[str, Any],
    ) -> str:
        overlay_path = self.prompt_dir / f"{condition}.txt"
        overlay = overlay_path.read_text(encoding="utf-8").strip()
        base = self.base.format(
            company=company,
            symbol=symbol,
            as_of_date=as_of_date,
            horizon_months=horizon_months,
        )
        identity = {
            "symbol": symbol,
            "company": company,
            "sector": sector,
            "as_of_date": as_of_date,
            "horizon_months": horizon_months,
            "prompt_version": self.prompt_version,
        }
        return (
            f"{base}\n\n{overlay}\n\n"
            f"COMPANY IDENTITY\n{json.dumps(identity, indent=2, sort_keys=True)}\n\n"
            f"CONDITION DATA PACKET\n{json.dumps(packet, indent=2, sort_keys=True)}"
        )


def packet_for_condition(
    condition: str,
    price_history: list[dict[str, Any]],
    features: dict[str, Any],
    fundamentals: dict[str, Any] | None,
    benchmark_features: dict[str, Any],
    sector_features: dict[str, Any] | None,
) -> dict[str, Any]:
    if condition == "A_MEMORY_ONLY":
        return {"supplied_information": "company identity only"}
    if condition == "B_PRICE_HISTORY":
        return {"price_history_monthly": price_history}
    if condition in {"C_QUANTITATIVE", "D_CURRENT_WEB"}:
        return {
            "price_history_monthly": price_history,
            "technical_and_return_features": features,
            "fundamentals": fundamentals or {"status": "not available in no-key prototype"},
            "spy_market_context": benchmark_features,
            "sector_context": sector_features or {"status": "not available"},
        }
    raise ValueError(f"Unknown condition: {condition}")
