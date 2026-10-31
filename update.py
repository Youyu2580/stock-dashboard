import json
import math
import urllib.request
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}

STOCKS = [
    ("601398", "工商银行", "sh", "核心"),
    ("601939", "建设银行", "sh", "核心"),
    ("601288", "农业银行", "sh", "核心"),
    ("601988", "中国银行", "sh", "核心"),
    ("600036", "招商银行", "sh", "核心"),
    ("601166", "兴业银行", "sh", "核心"),
    ("601998", "中信银行", "sh", "核心"),
    ("601088", "中国神华", "sh", "周期"),
    ("601225", "陕西煤业", "sh", "周期"),
    ("600900", "长江电力", "sh", "核心"),
    ("600025", "华能水电", "sh", "公用"),
    ("600795", "国电电力", "sh", "公用"),
    ("600011", "华能国际", "sh", "公用"),
    ("601985", "中国核电", "sh", "公用"),
    ("003816", "中国广核", "sz", "公用"),
    ("601857", "中国石油", "sh", "周期"),
    ("600028", "中国石化", "sh", "周期"),
    ("600938", "中国海油", "sh", "周期"),
    ("601318", "中国平安", "sh", "金融"),
    ("601601", "中国太保", "sh", "金融"),
    ("000333", "美的集团", "sz", "消费"),
    ("000651", "格力电器", "sz", "消费"),
    ("600690", "海尔智家", "sh", "消费"),
    ("000538", "云南白药", "sz", "医药"),
    ("000423", "东阿阿胶", "sz", "医药"),
    ("600750", "华润江中", "sh", "医药"),
    ("600519", "贵州茅台", "sh", "消费"),
    ("000858", "五粮液", "sz", "消费"),
    ("600941", "中国移动", "sh", "核心"),
    ("000429", "粤高速A", "sz", "公用"),
    ("600377", "宁沪高速", "sh", "公用"),
    ("601919", "中远海控", "sh", "周期"),
    ("600887", "伊利股份", "sh", "消费"),
    ("000807", "云铝股份", "sz", "周期"),
    ("002415", "海康威视", "sz", "科技"),
    ("000963", "华东医药", "sz", "医药"),
    ("600809", "山西汾酒", "sh", "消费"),
    ("601229", "上海银行", "sh", "核心"),
    ("000783", "长江证券", "sz", "金融"),
    ("600642", "申能股份", "sh", "公用"),
    ("601600", "中国铝业", "sh", "周期"),
    ("000895", "双汇发展", "sz", "消费"),
    ("601899", "紫金矿业", "sh", "周期"),
]


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def boll_pos(closes):
    n = 20
    if len(closes) < n:
        return "-"
    s = closes[-n:]
    ma = sum(s) / n
    sd = math.sqrt(sum((x - ma) ** 2 for x in s) / n)
    up, lo = ma + 2 * sd, ma - 2 * sd
    last = closes[-1]
    if last >= up:
        return "上轨以上"
    if last >= ma + sd:
        return "中上"
    if last >= ma - sd:
        return "中"
    if last >= lo:
        return "中下"
    return "下轨以下"


def fib(arr, days):
    s = arr[-days:] if len(arr) >= days else arr
    hi = max(x["high"] for x in s)
    lo = min(x["low"] for x in s)
    r = hi - lo
    last = s[-1]["close"]
    pos = round((last - lo) / r * 100, 1) if r > 0 else 0
    return {
        "hi": round(hi, 2),
        "lo": round(lo, 2),
        "f382": round(lo + r * 0.382, 2),
        "f500": round(lo + r * 0.5, 2),
        "f618": round(lo + r * 0.618, 2),
        "pos": pos,
    }


def main():
    with open("dividends.json", encoding="utf-8") as f:
        divs = json.load(f)

    rows = []
    for code, name, mkt, group in STOCKS:
        sym = mkt + code
        price = None
        boll_d = boll_w = None
        f60 = f120 = None
        try:
            k = fetch(
                "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=130"
            )
            if k and len(k) >= 60:
                for x in k:
                    x["high"] = float(x["high"])
                    x["low"] = float(x["low"])
                    x["close"] = float(x["close"])
                closes = [x["close"] for x in k]
                price = closes[-1]
                boll_d = boll_pos(closes)
                f60 = fib(k, 60)
                f120 = fib(k, 120)
        except Exception as e:
            print(f"{name} kline err: {e}")
        try:
            kw = fetch(
                "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"CN_MarketData.getKLineData?symbol={sym}&scale=1200&ma=no&datalen=60"
            )
            if kw and len(kw) >= 20:
                for x in kw:
                    x["close"] = float(x["close"])
                boll_w = boll_pos([x["close"] for x in kw])
        except Exception as e:
            print(f"{name} week err: {e}")

        div12 = float(divs.get(code, 0))
        dy = round(div12 / price * 100, 2) if price and price > 0 else None
        rows.append(
            {
                "name": name,
                "code": code,
                "group": group,
                "price": price,
                "dy": dy,
                "bollD": boll_d or "-",
                "bollW": boll_w or "-",
                "pos60": f60["pos"] if f60 else None,
                "pos120": f120["pos"] if f120 else None,
                "f60": f60,
                "f120": f120,
            }
        )
        print(f"{name} done")

    tz = timezone(timedelta(hours=8))
    payload = {
        "updated": datetime.now(tz).strftime("%Y-%m-%d %H:%M"),
        "stocks": rows,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"DONE: {len(rows)} stocks")


if __name__ == "__main__":
    main()
