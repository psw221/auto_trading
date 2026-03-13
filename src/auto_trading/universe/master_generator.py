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
KOSPI_FIELD_SPECS = [
    2, 1, 4, 4, 4,
    1, 1, 1, 1, 1,
    1, 1, 1, 1, 1,
    1, 1, 1, 1, 1,
    1, 1, 1, 1, 1,
    1, 1, 1, 1, 1,
    1, 9, 5, 5, 1,
    1, 1, 2, 1, 1,
    1, 2, 2, 2, 3,
    1, 3, 12, 12, 8,
    15, 21, 2, 7, 1,
    1, 1, 1, 1, 9,
    9, 9, 5, 9, 8,
    9, 3, 1, 1, 1,
]
KOSPI_PART2_COLUMNS = [
    '그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류',
    '제조업', '저유동성', '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100',
    'KOSPI50', 'KRX', 'ETP', 'ELW발행', 'KRX100',
    'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC',
    'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설',
    'Non1', 'KRX증권', 'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송',
    'SRI', '기준가', '매매수량단위', '시간외수량단위', '거래정지',
    '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시',
    '우회상장', '락구분', '액면변경', '증자구분', '증거금비율',
    '신용가능', '신용기간', '전일거래량', '액면가', '상장일자',
    '상장주수', '자본금', '결산월', '공모가', '우선주',
    '공매도과열', '이상급등', 'KRX300', 'KOSPI200', '매출액',
    '영업이익', '경상이익', '당기순이익', 'ROE', '기준년월',
    '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능', '대주가능',
]

SYMBOL_KEYS = ("symbol", "code", "short_code", "단축코드", "종목코드", "표준코드")
NAME_KEYS = ("name", "name_kr", "한글명", "종목명", "한글종목명")
MARKET_KEYS = ("market", "market_name", "시장구분", "시장명")
ASSET_TYPE_KEYS = ("asset_type", "type", "상품구분", "자산구분", "증권구분")
KOSPI200_KEYS = ("kospi200", "KOSPI200", "코스피200")


@dataclass(slots=True)
class MasterRow:
    symbol: str
    name: str
    market: str
    asset_type: str
    kospi200: bool = False


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
                if not row.symbol or not is_supported_row(row):
                    continue
                rows[row.symbol] = row
                official_rows_found = True
        if not official_rows_found:
            for row in load_official_master_rows():
                if not row.symbol or not is_supported_row(row):
                    continue
                rows[row.symbol] = row

    for source in explicit_sources:
        for row in load_source_rows(source):
            if not row.symbol or not is_supported_row(row):
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
        tail = line[-228:] if len(line) > 228 else ''
        symbol = clean_symbol(left[:9].rstrip())
        name = left[21:].strip() if len(left) > 21 else ''
        if not symbol or not name:
            continue
        fields = split_fixed_width(tail, KOSPI_FIELD_SPECS)
        part2 = {column: fields[index].strip() if index < len(fields) else '' for index, column in enumerate(KOSPI_PART2_COLUMNS)}
        rows.append(
            MasterRow(
                symbol=symbol,
                name=name,
                market='KOSPI',
                asset_type='STOCK',
                kospi200=parse_bool_flag(part2.get('KOSPI200', '')),
            )
        )
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
        kospi200 = parse_bool_flag(pick_value(raw, KOSPI200_KEYS))
        rows.append(MasterRow(symbol=symbol, name=name, market=market, asset_type=asset_type, kospi200=kospi200))
    return rows


def write_master_csv(path: Path, rows: Iterable[MasterRow]) -> None:
    sorted_rows = sorted(rows, key=lambda row: (row.market, row.asset_type, row.symbol))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "name", "market", "asset_type", "kospi200"])
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow(
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "market": row.market,
                    "asset_type": row.asset_type,
                    "kospi200": "Y" if row.kospi200 else "N",
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


def split_fixed_width(text: str, widths: list[int]) -> list[str]:
    fields: list[str] = []
    cursor = 0
    for width in widths:
        fields.append(text[cursor:cursor + width])
        cursor += width
    return fields


def parse_bool_flag(value: str) -> bool:
    normalized = value.strip().upper()
    return normalized not in {"", "0", "N", "NO", "FALSE"}


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
    return row.market == "KOSPI"
