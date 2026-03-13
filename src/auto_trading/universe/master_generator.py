from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import error, request

GITHUB_CONTENTS_API = "https://api.github.com/repos/koreainvestment/open-trading-api/contents/stocks_info"
KOSPI_MASTER_ZIP_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"

SYMBOL_KEYS = ("symbol", "code", "short_code", "단축코드", "종목코드", "표준코드")
NAME_KEYS = ("name", "name_kr", "한글명", "종목명", "한글종목명")
MARKET_KEYS = ("market", "market_name", "시장구분", "시장명")
ASSET_TYPE_KEYS = ("asset_type", "type", "상품구분", "자산구분", "증권구분")


@dataclass(slots=True)
class MasterRow:
    symbol: str
    name: str
    market: str
    asset_type: str


def generate_master_csv(
    *,
    output: Path,
    sources: list[str] | None = None,
    include_official: bool = True,
) -> int:
    explicit_sources = list(sources or [])
    rows: dict[str, MasterRow] = {}
    official_rows_found = False

    if include_official:
        official_sources = discover_official_sources()
        for source in official_sources:
            for row in load_source_rows(source):
                if not row.symbol:
                    continue
                if not is_supported_row(row):
                    continue
                rows[row.symbol] = row
                official_rows_found = True
        if not official_rows_found:
            for row in load_official_master_rows():
                if not row.symbol:
                    continue
                if not is_supported_row(row):
                    continue
                rows[row.symbol] = row

    for source in explicit_sources:
        for row in load_source_rows(source):
            if not row.symbol:
                continue
            if not is_supported_row(row):
                continue
            rows[row.symbol] = row

    output.parent.mkdir(parents=True, exist_ok=True)
    write_master_csv(output, rows.values())
    return len(rows)


def discover_official_sources() -> list[str]:
    payload = fetch_json(GITHUB_CONTENTS_API)
    sources: list[str] = []
    for item in payload:
        if item.get("type") != "file":
            continue
        name = str(item.get("name", "")).lower()
        download_url = str(item.get("download_url", ""))
        if not download_url:
            continue
        if not any(keyword in name for keyword in ("kospi", "etf", "etn")):
            continue
        if not name.endswith((".csv", ".txt")):
            continue
        sources.append(download_url)
    return sources


def load_official_master_rows() -> list[MasterRow]:
    rows: dict[str, MasterRow] = {}
    for row in load_remote_kospi_master_rows():
        rows[row.symbol] = row
    return list(rows.values())


def load_remote_kospi_master_rows() -> list[MasterRow]:
    archive_bytes = fetch_bytes(KOSPI_MASTER_ZIP_URL)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        target_name = next((name for name in archive.namelist() if name.lower().endswith('kospi_code.mst')), '')
        if not target_name:
            return []
        raw_text = archive.read(target_name).decode('cp949', errors='ignore')

    rows: list[MasterRow] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip('\r\n')
        if not line:
            continue
        left = line[:-228] if len(line) > 228 else line
        symbol = clean_symbol(left[:9].rstrip())
        name = left[21:].strip() if len(left) > 21 else ''
        if not symbol or not name:
            continue
        rows.append(MasterRow(symbol=symbol, name=name, market='KOSPI', asset_type='STOCK'))
    return rows


def load_source_rows(source: str) -> list[MasterRow]:
    text = fetch_text(source) if is_url(source) else Path(source).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines(), delimiter=detect_delimiter(text))
    inferred_market = infer_market_from_source(source)
    inferred_asset_type = infer_asset_type_from_source(source)
    rows: list[MasterRow] = []
    for raw in reader:
        symbol = clean_symbol(pick_value(raw, SYMBOL_KEYS))
        name = pick_value(raw, NAME_KEYS).strip()
        market = normalize_market(pick_value(raw, MARKET_KEYS), inferred_market)
        asset_type = normalize_asset_type(pick_value(raw, ASSET_TYPE_KEYS), inferred_asset_type)
        rows.append(MasterRow(symbol=symbol, name=name, market=market, asset_type=asset_type))
    return rows


def write_master_csv(path: Path, rows: Iterable[MasterRow]) -> None:
    sorted_rows = sorted(rows, key=lambda row: (row.market, row.asset_type, row.symbol))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "name", "market", "asset_type"])
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow(
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "market": row.market,
                    "asset_type": row.asset_type,
                }
            )


def fetch_json(url: str) -> list[dict[str, object]]:
    req = request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "auto-trading"})
    with request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": "auto-trading"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.read().decode("utf-8-sig")
    except error.HTTPError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc.code}") from exc


def fetch_bytes(url: str) -> bytes:
    req = request.Request(url, headers={"User-Agent": "auto-trading"})
    with request.urlopen(req, timeout=20) as response:
        return response.read()


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:3])
    if sample.count("\t") > sample.count(","):
        return "\t"
    return ","


def pick_value(row: dict[str, str], aliases: tuple[str, ...]) -> str:
    normalized = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
    for alias in aliases:
        if alias.lower() in normalized:
            return str(normalized[alias.lower()] or "")
    return ""


def clean_symbol(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    return digits.zfill(6) if digits else ""


def normalize_market(raw_market: str, inferred_market: str) -> str:
    value = raw_market.strip().upper()
    if "KOSPI" in value:
        return "KOSPI"
    if value:
        return value
    return inferred_market


def normalize_asset_type(raw_asset_type: str, inferred_asset_type: str) -> str:
    value = raw_asset_type.strip().upper()
    if "ETF" in value:
        return "ETF"
    if "ETN" in value:
        return "ETN"
    if value:
        return value
    return inferred_asset_type


def infer_market_from_source(source: str) -> str:
    lowered = source.lower()
    if "kospi" in lowered:
        return "KOSPI"
    return ""


def infer_asset_type_from_source(source: str) -> str:
    lowered = source.lower()
    if "etf" in lowered:
        return "ETF"
    if "etn" in lowered:
        return "ETN"
    return "STOCK"


def is_supported_row(row: MasterRow) -> bool:
    return row.market == "KOSPI" or row.asset_type == "ETF"
