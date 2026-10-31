#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_refresh.py — 排雷数据自动刷新（在 update.py 之后由 GitHub Actions 每天运行）

自动刷新（客观数据，无判断空间）：
  ① 股权质押   RPT_CSDC_LIST（中登，约每周五更新）
  ② 商誉       RPT_F10_FINANCE_GBALANCE（工商企业）/ BBALANCE（银行）
  ③ 未来12月解禁 RPT_LIFT_STAGE

半自动（公告扫描，机器只报"新增"，人工核实后更新 known_anns.json）：
  ④ 减持 ⑤ 担保 ⑥ 诉讼/仲裁 ⑦ 冻结/司法拍卖 ⑧ 问询/监管/立案/处罚

规则：
  - risk.json 是人工核实基线（2026-10-31 七类排雷），脚本只更新自动字段，
    人工判定（base / guar / litig / freeze 的 level 与 note）原样保留。
  - 新公告（art_code 或 标题 不在 known_anns.json reviewed 里）→ 进 rec['pending']，
    页面显示 🔔N待核，不自动改判定等级。
  - 任一接口失败 → 保留旧值并在 rec['errors'] 里记录，不让整个脚本崩。
"""
import json, io, re, ssl, time, urllib.request, urllib.parse
from datetime import date, timedelta

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0',
      'Referer': 'https://data.eastmoney.com/'}

RISK_F = 'risk.json'
DATA_F = 'data.json'
KNOWN_F = 'known_anns.json'

# ETF 底仓：无个股排雷项（商誉/解禁/质押/公告均不适用），跳过自动刷新
ETF_SKIP = {'515180', '159307'}

# 公告关键词桶（与 2026-10-31 人工核查用的口径一致）
KW_BUCKETS = [('reduce', r'减持'), ('guar', r'担保'),
              ('litig', r'诉讼|仲裁'), ('freeze', r'冻结|司法拍卖|拍卖'),
              ('inquiry', r'问询|监管函|关注函|立案|处罚|警示')]

def _get(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                  timeout=timeout, context=CTX).read().decode('utf-8', 'ignore')

def _rpt(name, filt, cols='ALL', size=30, sort=None, asc=False):
    p = {'reportName': name, 'columns': cols, 'pageSize': str(size),
         'pageNumber': '1', 'source': 'WEB', 'client': 'WEB', 'filter': filt}
    if sort:
        p['sortColumns'] = sort
        p['sortTypes'] = '-1' if not asc else '1'
    try:
        d = json.loads(_get('https://datacenter-web.eastmoney.com/api/data/v1/get?'
                            + urllib.parse.urlencode(p)))
        return (d.get('result') or {}).get('data') or []
    except Exception:
        return []

def secucode(code):
    return '%s.%s' % (code, 'SH' if code[0] == '6' else 'SZ')

# ---------- ① 质押 ----------
def refresh_pledge(code):
    r = _rpt('RPT_CSDC_LIST', '(SECURITY_CODE="%s")' % code,
             'SECURITY_CODE,TRADE_DATE,PLEDGE_RATIO,REPURCHASE_BALANCE,PLEDGE_MARKET_CAP',
             size=2, sort='TRADE_DATE')
    if not r:
        return None
    x = r[0]
    ratio = x.get('PLEDGE_RATIO')
    if ratio is None:
        return None
    ratio = float(ratio)
    lv = 'red' if ratio >= 15 else ('warn' if ratio >= 5 else 'ok')
    return {'ratio': ratio, 'level': lv, 'date': str(x.get('TRADE_DATE'))[:10],
            'cap': round((x.get('PLEDGE_MARKET_CAP') or 0) / 1e4, 2),
            'shares': x.get('REPURCHASE_BALANCE'),
            'src': 'auto',
            'note': '质押 %.2f%%' % ratio if lv == 'ok' else '质押 %.2f%%（≥5%% 关注 / ≥15%% 红灯）' % ratio}

# ---------- ② 商誉 ----------
def refresh_goodwill(code):
    for rpt_name in ('RPT_F10_FINANCE_GBALANCE', 'RPT_F10_FINANCE_BBALANCE'):
        r = _rpt(rpt_name, '(SECUCODE="%s")' % secucode(code),
                 'REPORT_DATE,GOODWILL,TOTAL_EQUITY', size=1, sort='REPORT_DATE')
        if r:
            x = r[0]
            gw, eq = x.get('GOODWILL'), x.get('TOTAL_EQUITY')
            if gw is None:
                return {'ratio': None, 'level': 'na', 'date': str(x.get('REPORT_DATE'))[:10],
                        'yi': 0.0, 'src': 'auto', 'note': '最新报表无商誉项（通常为0）'}
            gw, eq = float(gw), float(eq or 0)
            ratio = (gw / eq * 100) if eq else None
            lv = 'warn' if (ratio or 0) >= 5 else 'ok'
            return {'ratio': round(ratio, 2) if ratio is not None else None,
                    'level': lv, 'date': str(x.get('REPORT_DATE'))[:10],
                    'yi': round(gw / 1e8, 2), 'src': 'auto',
                    'note': ('商誉 %.2f 亿，占净资产 %.2f%%' % (gw / 1e8, ratio))
                            + ('（≥5% 预警）' if lv == 'warn' else '')}
    return None  # 保险/券商取不到 → 保留人工 note

# ---------- ③ 解禁 ----------
def refresh_lift(code):
    today = date.today()
    horizon = today + timedelta(days=365)
    rows = _rpt('RPT_LIFT_STAGE', '(SECURITY_CODE="%s")' % code,
                'FREE_DATE,TOTALSHARES_RATIO,LIFT_MARKET_CAP,FREE_SHARES_TYPE',
                size=100, sort='FREE_DATE')
    if not rows:  # 降序没有 → 尝试升序（解禁可能全在未来）
        rows = _rpt('RPT_LIFT_STAGE', '(SECURITY_CODE="%s")' % code,
                    'FREE_DATE,TOTALSHARES_RATIO,LIFT_MARKET_CAP,FREE_SHARES_TYPE',
                    size=100, sort='FREE_DATE', asc=True)
    fut = []
    for x in rows:
        try:
            d = date.fromisoformat(str(x.get('FREE_DATE'))[:10])
        except Exception:
            continue
        if today <= d <= horizon:
            fut.append(x)
    if not fut:
        return {'n': 0, 'ratio': 0.0, 'level': 'ok', 'next': None,
                'src': 'auto', 'note': '未来12个月无解禁'}
    ratio = sum(float(x.get('TOTALSHARES_RATIO') or 0) for x in fut)
    cap = sum(float(x.get('LIFT_MARKET_CAP') or 0) for x in fut) / 1e8
    nxt = min(str(x.get('FREE_DATE'))[:10] for x in fut)
    lv = 'warn' if ratio >= 1.5 else 'ok'
    return {'n': len(fut), 'ratio': round(ratio, 2), 'cap': round(cap, 2),
            'level': lv, 'next': nxt, 'src': 'auto',
            'note': '未来12个月解禁 %d 笔，占总股本 %.2f%%，市值 %.2f 亿，最近 %s'
                    % (len(fut), ratio, cap, nxt)}

# ---------- ④-⑧ 公告扫描 → pending ----------
def _benign(title):
    """人工核查已定性的良性模式（2026-10-31 结论）：
    定增/可转债的"审核问询函回复"= 正常融资流程，不是监管处罚"""
    if re.search(r'审核问询|募集说明', title) and re.search(r'发行|可转换|可转债', title):
        return True
    return False

def scan_anns(code, known):
    out, total_new = [], 0
    window = (date.today() - timedelta(days=365)).isoformat()
    for pg in (1, 2):
        u = ('https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1'
             '&page_size=100&page_index=%d&ann_type=A&client_source=web'
             '&stock_list=%s&f_node=0&s_node=0' % (pg, code))
        try:
            d = json.loads(_get(u))
            lst = ((d.get('data') or {}).get('list')) or []
        except Exception:
            lst = []
        for x in lst:
            title = (x.get('title') or '').strip()
            art = x.get('art_code') or ''
            key = '%s|%s' % (code, art) if art else '%s|T|%s' % (code, title)
            nd = str(x.get('notice_date'))[:10]
            if key in known or not title or nd < window or _benign(title):
                continue
            for tag, pat in KW_BUCKETS:
                if re.search(pat, title):
                    out.append({'date': str(x.get('notice_date'))[:10],
                                'title': title[:80], 'art': art, 'bucket': tag})
                    total_new += 1
                    break
        if len(lst) < 100:
            break
        time.sleep(0.15)
    out.sort(key=lambda a: a['date'], reverse=True)
    return {'n': total_new, 'items': out[:8], 'src': 'auto'}

# ---------- 主流程 ----------
def main():
    risk = json.load(io.open(RISK_F, encoding='utf-8'))
    data = json.load(io.open(DATA_F, encoding='utf-8'))
    known = set(json.load(io.open(KNOWN_F, encoding='utf-8')).get('reviewed') or [])

    codes = [s['code'] for s in data['stocks']]
    errors = []
    for code in codes:
        rec = risk.get(code)
        if not rec:
            continue
        if code in ETF_SKIP:
            rec['level'] = 'ok'
            rec['tags'] = []
            continue
        try:
            pl = refresh_pledge(code)
            if pl:
                rec['pledge'] = pl
        except Exception as e:
            errors.append('%s pledge %s' % (code, type(e).__name__))
        try:
            gw = refresh_goodwill(code)
            if gw:
                rec['goodwill'] = gw
            else:
                rec.setdefault('goodwill', {'ratio': None, 'level': 'na', 'src': 'manual',
                                            'note': '保险/券商口径，未取到（2026-10-31 人工核查）'})
        except Exception as e:
            errors.append('%s goodwill %s' % (code, type(e).__name__))
        try:
            rec['lift'] = refresh_lift(code)
        except Exception as e:
            errors.append('%s lift %s' % (code, type(e).__name__))
        try:
            rec['pending'] = scan_anns(code, known)
        except Exception as e:
            errors.append('%s anns %s' % (code, type(e).__name__))

        # 汇总：人工字段(base/guar/litig/freeze) + 自动字段(pledge/goodwill/lift)
        def lv(k):
            return (rec.get(k) or {}).get('level') or 'ok'
        levels = [lv('base'), lv('pledge'), lv('goodwill'), lv('lift'),
                  lv('guar'), lv('litig'), lv('freeze')]
        old_level = rec.get('level')
        if 'red' in levels or old_level == 'red':
            new_lv = 'red'
        elif 'warn' in levels or old_level == 'warn':
            new_lv = 'warn'
        else:
            new_lv = 'ok'

        tags = []
        pl, gw2, lf, pd = rec.get('pledge') or {}, rec.get('goodwill') or {}, rec.get('lift') or {}, rec.get('pending') or {}
        if pl.get('level') in ('warn', 'red') and pl.get('ratio') is not None:
            tags.append('质押%.2f%%' % pl['ratio'])
        if gw2.get('level') == 'warn':
            tags.append('商誉%.1f%%' % (gw2.get('ratio') or 0))
        if lf.get('level') == 'warn' and lf.get('next'):
            tags.append('解禁' + lf['next'][:7])
        for k, label in (('guar', '担保'), ('litig', '诉讼'), ('freeze', '冻结')):
            if (rec.get(k) or {}).get('level') in ('warn', 'red'):
                tags.append(label)
        if pd.get('n'):
            tags.append('🔔%d条待核' % pd['n'])
        rec['level'] = new_lv
        rec['tags'] = tags

    risk['_meta'] = {'refreshed': str(date.today()), 'mode': 'auto+manual',
                     'errors': errors[:20]}
    io.open(RISK_F, 'w', encoding='utf-8').write(
        json.dumps(risk, ensure_ascii=False, indent=1))
    n_pending = sum((risk.get(c, {}).get('pending') or {}).get('n') or 0 for c in codes)
    print('risk.json 刷新完成：%d 只 | 新增待核公告 %d 条 | 接口错误 %d 个'
          % (len(codes), n_pending, len(errors)))
    if errors:
        print('  errors:', errors)

if __name__ == '__main__':
    main()
