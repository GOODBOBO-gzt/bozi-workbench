#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金每日自动更新脚本（WorkBuddy 自动化 14:30/14:45/15:30 定时运行）

【核心约束】市值(value)与收益率(ret) 以用户最新报告为准，钉死不变。
本脚本只刷新「净值 / 盘中估值」等信号字段（用于展示与当日策略信号），
绝不动 value / ret / cost，也绝不重算总市值/总盈亏（它们由用户报告决定）。
"""
import json
import os
import sys
import datetime
import urllib.request
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_PATH = os.path.join(HERE, "fund-live.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def beijing_now():
    # 运行环境可能是 UTC，统一换算为北京时间
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def fetch_nav(code):
    url = ("https://api.fund.eastmoney.com/f10/lsjz?fundCode=%s"
           "&pageIndex=1&pageSize=1&_=%d") % (code, int(datetime.datetime.now().timestamp() * 1000))
    req = urllib.request.Request(url, headers={**UA, "Referer": "https://fundf10.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    item = data.get("Data", {}).get("LSJZList", [{}])[0]
    return {
        "date": item.get("FSRQ"),
        "nav": float(item.get("DWJZ") or 0),
        "dayChg": float(item.get("JZZZL") or 0),
    }


def fetch_intraday_sina(codes):
    """新浪盘中估值，单请求批量获取。返回 {code: {gsz, dwjz, gszzl, gztime, date}}。"""
    url = "https://hq.sinajs.cn/list=" + ",".join("fu_" + c for c in codes)
    req = urllib.request.Request(url, headers={**UA, "Referer": "https://finance.sina.com.cn/"})
    out = {}
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("gbk", "ignore")
        for line in text.splitlines():
            m = re.search(r'hq_str_fu_(\d{6})="([^"]*)"', line)
            if not m:
                continue
            code = m.group(1)
            f = m.group(2).split(",")
            if len(f) < 8:
                continue
            try:
                gsz = float(f[2]); dwjz = float(f[3]); gszzl = float(f[6].replace("%", ""))
            except ValueError:
                continue
            if gsz <= 0:
                continue
            out[code] = {"gsz": gsz, "dwjz": dwjz, "gszzl": gszzl, "gztime": f[1], "date": f[7]}
    except Exception as e:
        sys.stderr.write("sina intraday failed: %s\n" % e)
    return out


def advice(f):
    """操作建议：综合当日涨跌(todayChg) + 累计持有收益率(ret) + 质量分(score)。"""
    ret = f["ret"]
    t = float(f.get("todayChg") or 0.0)
    score = f.get("score", 3)
    if t >= 2.0:
        if ret >= 0.20:
            return ("分批止盈", f"当日上涨{t:.1f}% 且累计盈利超20%，可分批落袋为安")
        if ret >= 0:
            return ("持有/可减", f"当日上涨{t:.1f}% 已回本或小赚，可酌情部分止盈")
        return ("持有观察", f"当日上涨{t:.1f}% 但仍浮亏，继续观察不急于割肉")
    if t <= -2.0:
        if ret <= -0.30:
            if score >= 4:
                return ("逢低小补", f"当日下跌{t:.1f}% 且深度套牢，优质基可小额定投摊薄")
            return ("减仓止损", f"当日下跌{t:.1f}% 且深度套牢、质地偏弱，建议控制风险")
        if ret <= -0.10:
            return ("持有不动", f"当日下跌{t:.1f}% 属正常回调，继续持有")
        return ("持有观察", f"当日下跌{t:.1f}% 但浮亏不大，关注后续")
    if ret >= 0.30:
        return ("减仓止盈", "累计盈利超30%，可分批止盈锁定收益")
    if ret >= 0:
        return ("持有不动", "当前盈利，继续持有观察")
    if ret <= -0.30:
        if score >= 4:
            return ("逢低补仓", "深度亏损且基金质地较好，可小额定投摊薄成本")
        return ("减仓止损", "深度亏损且质地偏弱，建议控制风险")
    if score >= 5:
        return ("持有不动", "小幅浮亏，优质基金可继续持有")
    return ("持有观察", "小幅浮亏，关注后续走势")


def main():
    live = json.load(open(LIVE_PATH, encoding="utf-8"))
    codes = [f["code"] for f in live["funds"]]
    beijing_today = beijing_now().strftime("%Y-%m-%d")
    intraday = fetch_intraday_sina(codes)

    nav_dates = []
    for f in live["funds"]:
        code = f["code"]
        try:
            nav_info = fetch_nav(code)
        except Exception as e:
            sys.stderr.write("fetch %s failed: %s\n" % (code, e))
            nav_info = {"date": "", "nav": 0, "dayChg": 0}
        nav = nav_info["nav"]
        nav_date = nav_info["date"]
        it = intraday.get(code)
        intraday_usable = bool(it and it.get("date") == beijing_today and it.get("gsz", 0) > 0)
        nav_is_today = bool(nav_date == beijing_today)

        # 决策优先级：收盘后确认净值 > 盘中实时估值 > 确认净值兜底
        if nav_is_today:
            price, today_chg, mode, asof = nav, nav_info["dayChg"], "nav", nav_date
        elif intraday_usable:
            price, today_chg, mode, asof = it["gsz"], it["gszzl"], "intraday", it["gztime"]
        else:
            price, today_chg, mode, asof = nav, nav_info["dayChg"], "nav", nav_date

        # ===== 只刷新净值/信号字段；市值/收益率/成本 钉死不变 =====
        f["nav"] = nav
        f["navDate"] = nav_date
        f["dayChg"] = nav_info["dayChg"]
        f["gsz"] = it["gsz"] if it else None
        f["gszzl"] = it["gszzl"] if it else None
        f["gztime"] = it["gztime"] if it else None
        f["intradayDate"] = it["date"] if it else None
        f["price"] = price
        f["mode"] = mode
        f["todayChg"] = today_chg
        f["estGszzl"] = today_chg
        # 盈亏由钉死值重算（value-cost 恒定）
        f["pnl"] = round(f["value"] - f["cost"], 2)
        adv = advice(f)
        f["advice"] = adv[0]
        f["adviceReason"] = adv[1]
        if nav_date:
            nav_dates.append(nav_date)

    # 汇总保持钉死（totalValue/totalCost/totalPnl/totalRet 不重算）
    live["updatedAt"] = beijing_now().strftime("%Y-%m-%d %H:%M")
    live["navDate"] = max(nav_dates) if nav_dates else live.get("navDate", "")
    live["mode"] = "intraday" if any(f["mode"] == "intraday" for f in live["funds"]) else "nav"
    live["intradayDate"] = next((f["intradayDate"] for f in live["funds"] if f["mode"] == "intraday"), "")
    live["intradayAsOf"] = next((f["gztime"] for f in live["funds"] if f["mode"] == "intraday"), None)

    json.dump(live, open(LIVE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("OK 钉死市值 mode=%s navDate=%s totalRet=%.2f%% totalPnl=%d"
          % (live["mode"], live["navDate"], live["totalRet"] * 100, live["totalPnl"]))


if __name__ == "__main__":
    main()
