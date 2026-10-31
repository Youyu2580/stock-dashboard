import json
import math
import urllib.request
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}

# 字段顺序：代码 / 名称 / 市场 / 分组 / 驱动 / 驱动说明 / 核实状态
# 驱动 = 「订单」订单与销量驱动 ｜ 「价差」产品价差(或净息差/运价)驱动
#   判据（用户铁律）：利润由订单放量决定 → 订单；由产品价差决定 → 价差
#   价差 ≠ 必剔，只标识利润来源；真正的风险是「商品价差」（煤价/油价/运价/铝价）
# 核实 = 「已核实」经三源交叉核对（东财分红表 + 腾讯行情 + 独立复算股息率）
#   新增标的未经核对务必先填「未核实」，不得默认写已核实
STOCKS = [
    # 银行（利润由净息差决定）
    ("601398", "工商银行", "sh", "银行", "价差", "净息差×规模", "已核实"),
    ("601939", "建设银行", "sh", "银行", "价差", "净息差×规模", "已核实"),
    ("601288", "农业银行", "sh", "银行", "价差", "净息差×规模", "已核实"),
    ("601988", "中国银行", "sh", "银行", "价差", "净息差×规模", "已核实"),
    ("600036", "招商银行", "sh", "银行", "价差", "净息差+财富管理", "已核实"),
    ("601166", "兴业银行", "sh", "银行", "价差", "净息差", "已核实"),
    ("601998", "中信银行", "sh", "银行", "价差", "净息差", "已核实"),
    ("601229", "上海银行", "sh", "银行", "价差", "净息差", "已核实"),
    # 商品周期（利润由商品价差决定，最需警惕）
    ("601088", "中国神华", "sh", "商品", "价差", "煤炭长协价与市场价", "已核实"),
    ("601225", "陕西煤业", "sh", "商品", "价差", "动力煤价", "已核实"),
    ("601857", "中国石油", "sh", "商品", "价差", "油价×产量", "已核实"),
    ("600028", "中国石化", "sh", "商品", "价差", "油价与炼油毛利", "已核实"),
    ("600938", "中国海油", "sh", "商品", "价差", "油价×产量", "已核实"),
    ("601919", "中远海控", "sh", "商品", "价差", "集运运价(SCFI)", "已核实"),
    ("601600", "中国铝业", "sh", "商品", "价差", "铝价与氧化铝成本", "已核实"),
    ("000807", "云铝股份", "sz", "商品", "价差", "铝价与电力成本", "已核实"),
    ("601899", "紫金矿业", "sh", "商品", "价差", "金价+铜价", "已核实"),
    # 公用事业
    ("600900", "长江电力", "sh", "公用", "订单", "来水量×上网电价", "已核实"),
    ("600025", "华能水电", "sh", "公用", "订单", "来水量×上网电价", "已核实"),
    ("601985", "中国核电", "sh", "公用", "订单", "发电量×核准电价", "已核实"),
    ("003816", "中国广核", "sz", "公用", "订单", "发电量×核准电价", "已核实"),
    ("600795", "国电电力", "sh", "公用", "价差", "上网电价−煤价(火电为主)", "已核实"),
    ("600011", "华能国际", "sh", "公用", "价差", "上网电价−煤价", "已核实"),
    ("600642", "申能股份", "sh", "公用", "价差", "电价−煤价+燃气量", "已核实"),
    ("000429", "粤高速A", "sz", "公用", "订单", "车流量×收费标准", "已核实"),
    ("600377", "宁沪高速", "sh", "公用", "订单", "车流量×收费标准", "已核实"),
    # 运营商
    ("600941", "中国移动", "sh", "运营商", "订单", "用户数×ARPU", "已核实"),
    # 金融
    ("601318", "中国平安", "sh", "金融", "价差", "利差+投资收益", "已核实"),
    ("601601", "中国太保", "sh", "金融", "价差", "利差+投资收益", "已核实"),
    ("000783", "长江证券", "sz", "金融", "价差", "成交量×费率", "已核实"),
    # 消费
    ("000333", "美的集团", "sz", "消费", "订单", "销量×份额", "已核实"),
    ("000651", "格力电器", "sz", "消费", "订单", "销量×渠道份额", "已核实"),
    ("600690", "海尔智家", "sh", "消费", "订单", "销量×品牌份额", "已核实"),
    ("600519", "贵州茅台", "sh", "消费", "订单", "销量×品牌溢价", "已核实"),
    ("000858", "五粮液", "sz", "消费", "订单", "销量×出厂价", "已核实"),
    ("600809", "山西汾酒", "sh", "消费", "订单", "销量×渠道扩张", "已核实"),
    ("600887", "伊利股份", "sh", "消费", "订单", "销量×份额", "已核实"),
    ("000895", "双汇发展", "sz", "消费", "价差", "屠宰利差×猪价", "已核实"),
    # 医药
    ("000538", "云南白药", "sz", "医药", "订单", "销量×品牌", "已核实"),
    ("000423", "东阿阿胶", "sz", "医药", "订单", "销量×提价", "已核实"),
    ("600750", "华润江中", "sh", "医药", "订单", "销量×品牌", "已核实"),
    ("000963", "华东医药", "sz", "医药", "订单", "新品放量×份额", "已核实"),
    # 科技
    ("002415", "海康威视", "sz", "科技", "订单", "订单×份额", "已核实"),
    # ETF 底仓（两层仓之「底仓层」：死拿定投、不择时、只加不减、不做波段）
    ("515180", "红利ETF易方达", "sh", "ETF底仓", "底仓", "中证红利·死拿定投", "已核实"),
    ("159307", "红利低波100ETF博时", "sz", "ETF底仓", "底仓", "红利低波100(930955)·死拿定投", "已核实"),
]

# 底仓 ETF：股息率走基金分红（腾讯月K 复权差值自动算），不走 dividends.json
ETF_CODES = {"515180", "159307"}


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


def etf_div12(sym):
    """ETF 近12月每份分红：腾讯月K 不复权价 − 前复权价 的阶梯下降额之和。
    原理：前复权把历史价减去其后所有分红，两价之差在该 bar 之后的分红发生时下降一步。"""
    def kl(fq=""):
        u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
             "?param=%s,month,,,16,%s" % (sym, fq))
        j = fetch(u)
        return j["data"][sym].get("month") or j["data"][sym].get("qfqmonth")
    raw, qfq = kl(), kl("qfq")
    if not raw or not qfq or len(raw) != len(qfq):
        return None
    diffs = [float(r[2]) - float(q[2]) for r, q in zip(raw, qfq)]
    months = [x[0][:7] for x in raw]
    cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=365)).strftime("%Y-%m")
    total = 0.0
    for i in range(1, len(diffs)):
        if months[i] >= cutoff:
            step = diffs[i - 1] - diffs[i]
            if step > 0.002:
                total += step
    return round(total, 4)


def main():
    with open("dividends.json", encoding="utf-8") as f:
        divs = json.load(f)

    rows = []
    for code, name, mkt, group, driver, dnote, verified in STOCKS:
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

        if code in ETF_CODES:
            try:
                div12 = etf_div12(sym) or 0.0
            except Exception as e:
                print(f"{name} etf_div err: {e}")
                div12 = 0.0
        else:
            div12 = float(divs.get(code, 0))
        dy = round(div12 / price * 100, 2) if price and price > 0 else None
        rows.append(
            {
                "name": name,
                "code": code,
                "group": group,
                "driver": driver,
                "driverNote": dnote,
                "verified": verified,
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
