"""把已导出的大商所日行情读进 DailyBar。

接受 JSON 数组或 CSV。列名同时认中文（DCE 网页 / DCEData xlsx）和英文。
不访问交易所网站。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..market import DCE_INSTRUMENTS, DailyBar, instrument

_NAME_TO_CODE = {ins.name: ins.code for ins in DCE_INSTRUMENTS.values()}

_ALIASES = {
    "date": ("date", "日期", "交易日"),
    "variety_name": ("variety_name", "品种", "品种名称"),
    "variety_code": ("variety_code", "variety", "品种代码"),
    "contract": ("contract", "symbol", "合约", "合约代码"),
    "open": ("open", "开盘价"),
    "high": ("high", "最高价"),
    "low": ("low", "最低价"),
    "close": ("close", "收盘价"),
    "pre_settle": ("pre_settle", "前结算价"),
    "settle": ("settle", "结算价"),
    "volume": ("volume", "成交量"),
    "open_interest": ("open_interest", "持仓量"),
    "turnover": ("turnover", "成交额"),
    "trade_type": ("trade_type", "类型", "交易类型"),
}


def _pick(row: dict[str, str], *keys: str) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in ("", None):
            return lowered[key.lower()].strip()
    return None


def _num(v: str | None) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def _norm_date(v: str) -> str:
    s = v.strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _code_of(row: dict[str, str], name: str | None, contract: str) -> str:
    raw = _pick(row, *_ALIASES["variety_code"])
    if raw:
        return raw.lower()
    if name and name in _NAME_TO_CODE:
        return _NAME_TO_CODE[name]
    # c2509 / C2509 / 玉米2509
    prefix = "".join(ch for ch in contract if ch.isalpha()).lower()
    if prefix in DCE_INSTRUMENTS:
        return prefix
    raise ValueError(f"无法识别品种: name={name!r} contract={contract!r}")


def row_to_bar(row: dict[str, str], source: str = "dce-file") -> DailyBar:
    contract = _pick(row, *_ALIASES["contract"]) or ""
    name = _pick(row, *_ALIASES["variety_name"])
    date = _pick(row, *_ALIASES["date"])
    if not date or not contract:
        raise ValueError(f"缺少日期或合约: {row}")
    code = _code_of(row, name, contract)
    ins = instrument(code)
    return DailyBar(
        date=_norm_date(date),
        variety_code=code,
        variety_name=name or (ins.name if ins else code),
        contract=contract,
        open=_num(_pick(row, *_ALIASES["open"])),
        high=_num(_pick(row, *_ALIASES["high"])),
        low=_num(_pick(row, *_ALIASES["low"])),
        close=_num(_pick(row, *_ALIASES["close"])),
        pre_settle=_num(_pick(row, *_ALIASES["pre_settle"])),
        settle=_num(_pick(row, *_ALIASES["settle"])),
        volume=_num(_pick(row, *_ALIASES["volume"])),
        open_interest=_num(_pick(row, *_ALIASES["open_interest"])),
        turnover=_num(_pick(row, *_ALIASES["turnover"])),
        trade_type=_pick(row, *_ALIASES["trade_type"]) or "期货",
        source=source,
    )


def load_json(path: str | Path) -> list[DailyBar]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("JSON 必须是对象数组")
    return [row_to_bar({str(k): "" if v is None else str(v) for k, v in item.items()}, Path(path).name) for item in raw]


def load_csv(path: str | Path) -> list[DailyBar]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [row_to_bar(row, Path(path).name) for row in csv.DictReader(f)]


def load(path: str | Path) -> list[DailyBar]:
    p = Path(path)
    if p.suffix.lower() == ".json":
        return load_json(p)
    if p.suffix.lower() == ".csv":
        return load_csv(p)
    raise ValueError(f"仅支持 csv/json: {p}")


def sample_path() -> Path:
    return Path(__file__).with_name("dce_sample.json")
