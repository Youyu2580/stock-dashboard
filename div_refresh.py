# -*- coding: utf-8 -*-
"""div_refresh.py · 在 GitHub Actions 里用东财接口重算"年度口径"分红快照

为什么要有它：
  原来 dividends.json 是人工维护的每股分红，TTM / 旧年份 / 漏中期混在一起，
  导致工行被算成 5.09%（一年派三次叠加）、双汇被算成 3.14%（漏了中期）。
  update.py 现在优先读本脚本产出的 dividends_fy.json（年度口径），没有才回落。

口径（与 stock-dive/pool_sync.py 完全一致）：
  年度每股分红 = 最近完整财年（中期 + 末期）分红合计
  完整财年     = 该年有 12-31 的年报行、且年报行带 EPS
  趋势         = 只比已出年报的完整财年；实质下降才算（≥5% 且 ≥0.02 元）
  flag         = 分红率 > 100%（分红超过当年利润，不可持续）

失败处理（云端网络不稳时不破坏页面）：
  单只失败 → 保留上一次快照里的旧值，不写坏数据
  整体失败过多（>40%）→ 不写文件，退出码仍为 0（workflow 用 continue-on-error 兜底）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dividends_fy.json")

# 与 update.py 保持同一份名单；ETF(5/15/1 开头)不走个股分红口径
try:
    from update import STOCKS as _S  # noqa: N812
    CODES = [(x[0], x[1]) for x in _S if not x[0][:2] in ("15", "16", "18", "50", "51", "52", "56", "58")]
except Exception:
    CODES = []


def http_json(url, timeout=20, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.0 * (i + 1))
    raise RuntimeError(str(last)[:120])


def num(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_dividend(code):
    j = http_json("%s?reportName=RPT_SHAREBONUS_DET&columns=ALL&filter=(SECURITY_CODE%%3D%%22%s%%22)"
                  "&pageNumber=1&pageSize=14&sortTypes=-1&sortColumns=REPORT_DATE" % (DC, code))
    rows = ((j.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        per10 = num(r.get("PRETAX_BONUS_RMB"))
        if per10 is None:
            continue
        out.append({
            "period": (r.get("REPORT_DATE") or "")[:10],
            "per_share": per10 / 10.0,
            "eps": num(r.get("BASIC_EPS")),
            "progress": r.get("ASSIGN_PROGRESS") or "",
            "ex_date": (r.get("EX_DIVIDEND_DATE") or "")[:10] or None,
        })
    return out


def build(rows):
    """按完整财年聚合，返回快照条目（不含 dy，dy 由 update.py 用自己的价格算）。"""
    by_year, eps_year = {}, {}
    for d in rows:
        ps = d["per_share"]
        y = (d["period"] or "")[:4]
        if not ps or not y:
            continue
        by_year[y] = round(by_year.get(y, 0.0) + ps, 4)
        if d["period"].endswith("12-31") and d["eps"]:
            eps_year[y] = d["eps"]
    complete = sorted(y for y in by_year if y in eps_year)
    if not complete:
        return None
    fy = by_year[complete[-1]]
    payout = round(fy / eps_year[complete[-1]] * 100, 1) if eps_year[complete[-1]] else None

    # TTM：近 365 天已实施除权
    today = datetime.now(CST).date()
    ttm = 0.0
    for d in rows:
        if not d["ex_date"] or "实施" not in d["progress"]:
            continue
        try:
            dt = datetime.strptime(d["ex_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - dt).days <= 365:
            ttm += d["per_share"]

    seq = [by_year[y] for y in complete[-3:]]
    trend = None
    if len(seq) >= 2:
        prev, last = seq[-2], seq[-1]
        drop = prev - last
        pct = (drop / prev * 100) if prev else 0.0
        if drop > 0 and (pct >= 15 or (pct >= 5 and drop >= 0.02)):
            trend = "最近一年下降"
        elif drop > 0:
            trend = "基本持平"
        elif all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1)):
            trend = "近 3 年未下降" if len(seq) >= 3 else "近 2 年未降"
        else:
            trend = "有波动"
    return {
        "fy": round(fy, 4),
        "ttm": round(ttm, 4),
        "trend": trend,
        "flag": bool(payout is not None and payout > 100),
        "payout": payout,
        "years": complete[-3:],
        "asof": datetime.now(CST).strftime("%Y-%m-%d"),
    }


def main():
    if not CODES:
        print("拿不到股票名单（update.py 导入失败），不动快照")
        return 0
    prev = {}
    try:
        with open(SNAP, encoding="utf-8") as f:
            prev = (json.load(f) or {}).get("codes") or {}
    except Exception:
        prev = {}

    out, ok, kept, bad = {}, 0, 0, []
    for code, name in CODES:
        try:
            item = build(fetch_dividend(code))
            if item:
                out[code] = item
                ok += 1
            else:
                raise RuntimeError("无可用财年数据")
        except Exception as e:  # noqa: BLE001
            if prev.get(code):
                out[code] = prev[code]
                kept += 1
            else:
                bad.append((code, name, str(e)[:60]))
        time.sleep(0.15)

    total = len(CODES)
    print("分红快照：成功 %d / 沿用旧值 %d / 失败 %d（共 %d）" % (ok, kept, len(bad), total))
    for c, n, m in bad[:8]:
        print("  ✗ %s %s %s" % (c, n, m))

    if ok == 0:
        print("⚠️ 本次没有任何一只取到数据（云端可能访问不了东财），保留原快照不写")
        return 0
    if ok < total * 0.6:
        print("⚠️ 成功率不足 60%%，疑似网络受限，保留原快照不写")
        return 0

    payload = {"asof": datetime.now(CST).strftime("%Y-%m-%d"),
               "source": "eastmoney RPT_SHAREBONUS_DET（div_refresh.py 自动重算，年度口径）",
               "codes": out}
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("已写入 %s（%d 条）" % (SNAP, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
