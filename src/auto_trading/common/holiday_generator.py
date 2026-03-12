from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib import parse, request

HOLIDAY_API_URL = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"


@dataclass(slots=True)
class HolidayRow:
    day: date
    name: str


def needs_holiday_refresh(path: Path, year: int) -> bool:
    if not path.exists():
        return True
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            years = {date.fromisoformat((row.get("date") or "").strip()).year for row in reader if (row.get("date") or "").strip()}
    except Exception:
        return True
    return year not in years


def generate_holiday_csv(output: Path, year: int, service_key: str) -> int:
    holidays = {row.day: row for row in fetch_public_holidays(year, service_key)}
    for row in build_krx_extra_holidays(year):
        holidays[row.day] = row

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "name"])
        writer.writeheader()
        for row in sorted(holidays.values(), key=lambda item: item.day):
            writer.writerow({"date": row.day.isoformat(), "name": row.name})
    return len(holidays)


def fetch_public_holidays(year: int, service_key: str) -> list[HolidayRow]:
    if not service_key:
        return []
    rows: list[HolidayRow] = []
    for month in range(1, 13):
        query = parse.urlencode(
            {
                "ServiceKey": service_key,
                "solYear": str(year),
                "solMonth": f"{month:02d}",
                "numOfRows": "100",
                "pageNo": "1",
            }
        )
        url = f"{HOLIDAY_API_URL}?{query}"
        with request.urlopen(url, timeout=10) as response:
            xml_text = response.read().decode("utf-8")
        rows.extend(parse_holiday_response(xml_text))
    return rows


def parse_holiday_response(xml_text: str) -> list[HolidayRow]:
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")
    rows: list[HolidayRow] = []
    for item in items:
        is_holiday = (item.findtext("isHoliday") or "").strip().upper()
        if is_holiday != "Y":
            continue
        locdate = (item.findtext("locdate") or "").strip()
        date_name = (item.findtext("dateName") or "").strip()
        if len(locdate) != 8:
            continue
        rows.append(
            HolidayRow(
                day=date(int(locdate[:4]), int(locdate[4:6]), int(locdate[6:8])),
                name=date_name or "Public Holiday",
            )
        )
    return rows


def build_krx_extra_holidays(year: int) -> list[HolidayRow]:
    rows = [
        HolidayRow(day=date(year, 5, 1), name="Labor Day"),
    ]
    rows.append(HolidayRow(day=resolve_year_end_closure(year), name="Year-end Market Closure"))
    return rows


def resolve_year_end_closure(year: int) -> date:
    day = date(year, 12, 31)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
