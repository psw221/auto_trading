from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MIN_PRICE = 3000.0
MIN_AVG_TURNOVER = 5_000_000_000.0
MAX_UNIVERSE_SIZE = 50
CURRENT_UNIVERSE_FILENAME = 'current_universe.csv'


@dataclass(slots=True)
class UniverseItem:
    symbol: str
    name: str = ""
    market: str = ""
    asset_type: str = ""
    price: float = 0.0
    avg_turnover_20d: float = 0.0
    kospi200: bool = False


@dataclass(slots=True)
class UniverseBuilder:
    kis_client: object
    symbols: list[str] = field(default_factory=list)

    def rebuild(self, as_of: datetime) -> list[UniverseItem]:
        master_items = self._load_master_items()
        filtered: list[UniverseItem] = []
        for item in master_items:
            current = self.kis_client.get_current_price(item.symbol)
            daily_history = self.kis_client.get_daily_turnover_history(item.symbol, lookback_days=20)
            avg_turnover = self._average_turnover(daily_history)
            if current["price"] < MIN_PRICE:
                continue
            if avg_turnover < MIN_AVG_TURNOVER:
                continue
            filtered.append(
                UniverseItem(
                    symbol=item.symbol,
                    name=item.name,
                    market=item.market,
                    asset_type=item.asset_type,
                    price=current["price"],
                    avg_turnover_20d=avg_turnover,
                    kospi200=item.kospi200,
                )
            )

        filtered.sort(key=lambda item: item.avg_turnover_20d, reverse=True)
        selected = filtered[:MAX_UNIVERSE_SIZE]
        self.symbols = [item.symbol for item in selected]
        if selected:
            self.save_current_universe(selected)
        return selected

    def load_current_universe(self) -> list[UniverseItem]:
        path = self._resolve_current_universe_path()
        if not path.exists():
            return []
        items: list[UniverseItem] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = (row.get("symbol") or "").strip()
                if not symbol:
                    continue
                items.append(
                    UniverseItem(
                        symbol=symbol,
                        name=(row.get("name") or "").strip(),
                        market=(row.get("market") or "").strip().upper(),
                        asset_type=(row.get("asset_type") or "").strip().upper(),
                        price=self._to_float(row.get("price")),
                        avg_turnover_20d=self._to_float(row.get("avg_turnover_20d")),
                        kospi200=self._parse_bool_flag(row.get("kospi200")),
                    )
                )
        self.symbols = [item.symbol for item in items]
        return items

    def save_current_universe(self, items: list[UniverseItem]) -> None:
        path = self._resolve_current_universe_path()
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["symbol", "name", "market", "asset_type", "price", "avg_turnover_20d", "kospi200"],
                lineterminator="\n",
            )
            writer.writeheader()
            for item in items:
                writer.writerow(
                    {
                        "symbol": item.symbol,
                        "name": item.name,
                        "market": item.market,
                        "asset_type": item.asset_type,
                        "price": item.price,
                        "avg_turnover_20d": item.avg_turnover_20d,
                        "kospi200": "Y" if item.kospi200 else "N",
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)

    def _load_master_items(self) -> list[UniverseItem]:
        path = self._resolve_master_path()
        if not path.exists():
            return []
        items: list[UniverseItem] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                market = (row.get("market") or "").strip().upper()
                asset_type = (row.get("asset_type") or "").strip().upper()
                kospi200 = self._parse_bool_flag(row.get("kospi200"))
                if not self._is_supported_asset(market, asset_type, kospi200):
                    continue
                symbol = (row.get("symbol") or "").strip()
                if not symbol:
                    continue
                items.append(
                    UniverseItem(
                        symbol=symbol,
                        name=(row.get("name") or "").strip(),
                        market=market,
                        asset_type=asset_type,
                        kospi200=kospi200,
                    )
                )
        return items

    def _resolve_master_path(self) -> Path:
        configured = getattr(self.kis_client.settings, "universe_master_path", None)
        if isinstance(configured, Path):
            return configured
        return Path("./data/universe_master.csv")

    def _resolve_current_universe_path(self) -> Path:
        return self._resolve_master_path().with_name(CURRENT_UNIVERSE_FILENAME)

    @staticmethod
    def _average_turnover(history: list[dict[str, float]]) -> float:
        if not history:
            return 0.0
        turnovers = [item["turnover"] for item in history if item["turnover"] > 0]
        if not turnovers:
            return 0.0
        return sum(turnovers) / len(turnovers)

    @staticmethod
    def _is_supported_asset(market: str, asset_type: str, kospi200: bool) -> bool:
        return market == "KOSPI" and asset_type != "ETF" and kospi200

    @staticmethod
    def _parse_bool_flag(value: object) -> bool:
        text = str(value or "").strip().upper()
        if text == "":
            return True
        return text not in {"0", "N", "NO", "FALSE"}

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
