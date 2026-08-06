#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金每日自动更新脚本（GitHub Actions 定时运行）
- 读取 fund-basis.json：每只基金的固定基础持仓（代码 / 份额 / 投入成本 / 质量分）
- 盘中（约 14:30）：优先采用新浪盘中估值（hq.sinajs.cn）作为当日实时数据
- 收盘后（约 22:30）：采用东方财富确认净值（api.fund.eastmoney.com/f10/lsjz）
- 计算每只基金的当前市值、持有收益率、浮动盈亏，并给出操作建议
- 输出 fund-live.json 供 GitHub Pages 页面读取
"""
import json
import os
import sys
import datetime
import urllib.request
import re

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_PATH = os.path.join(HERE, "fund-basis.json")
LIVE_PATH = os.path.join(HERE, "fund-live.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def beijing_now():
    # GitHub Actions 运行在 UTC，统一换算为北京时间
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
    """新浪盘中估值，单请求批量获取。返回 {code: {gsz, dwjz, gszzl, gztime, date}}。
    字段：name,time,gsz,dwjz,ljjz,?,gszzl,date,...（GBK 编码）"""
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
                gsz = float(f[2])
                dwjz = float(f[3])
                gszzl = float(f[6].replace("%", ""))
            except ValueError:
                continue
            if gsz <= 0:
                continue
            out[code] = {"gsz": gsz, "dwjz": dwjz, "gszzl": gszzl, "gztime": f[1], "date": f[7]}
    except Exception as e:
        sys.stderr.write("sina intraday failed: %s\n" % e)
    return out


def advice(f):
    """操作建议：综合 14:30 当日涨跌(todayChg) + 累计持有收益率(ret) + 质量分(score)。
    当日涨跌来自盘中实时估值(gszzl)或收盘确认净值(dayChg)，使建议随行情逐日变化。"""
    ret = f["ret"]
    t = float(f.get("todayChg") or 0.0)   # 当日涨跌幅（%）
    score = f.get("score", 3)
    # 当日明显上涨
    if t >= 2.0:
        if ret >= 0.20:
            return ("分批止盈", f"当日上涨{t:.1f}% 且累计盈利超20%，可分批落袋为安")
        if ret >= 0:
            return ("持有/可减", f"当日上涨{t:.1f}% 已回本或小赚，可酌情部分止盈")
        return ("持有观察", f"当日上涨{t:.1f}% 但仍浮亏，继续观察不急于割肉")
    # 当日明显下跌
    if t <= -2.0:
        if ret <= -0.30:
            if score >= 4:
                return ("逢低小补", f"当日下跌{t:.1f}% 且深度套牢，优质基可小额定投摊薄")
            return ("减仓止损", f"当日下跌{t:.1f}% 且深度套牢、质地偏弱，建议控制风险")
        if ret <= -0.10:
            return ("持有不动", f"当日下跌{t:.1f}% 属正常回调，继续持有")
        return ("持有观察", f"当日下跌{t:.1f}% 但浮亏不大，关注后续")
    # 当日窄幅震荡：回到累计收益维度
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
    basis = json.load(open(BASE_PATH, encoding="utf-8"))
    codes = [b["code"] for b in basis]
    beijing_today = beijing_now().strftime("%Y-%m-%d")
    intraday = fetch_intraday_sina(codes)

    funds = []
    total_cost = 0
    total_value = 0
    nav_dates = []
    for b in basis:
        code = b["code"]
        try:
            nav_info = fetch_nav(code)
        except Exception as e:
            sys.stderr.write("fetch %s failed: %s\n" % (code, e))
            nav_info = {"date": "", "nav": 0, "dayChg": 0}
        nav = nav_info["nav"]
        shares = b["shares"]
        cost = b["cost"]

        it = intraday.get(code)
        intraday_usable = bool(it and it.get("date") == beijing_today and it.get("gsz", 0) > 0)
        nav_is_today = bool(nav_info.get("date") == beijing_today)

        # 决策优先级：收盘后确认净值 > 盘中实时估值 > 确认净值兜底
        if nav_is_today:
            price, today_chg, mode, asof = nav, nav_info["dayChg"], "nav", nav_info["date"]
        elif intraday_usable:
            price, today_chg, mode, asof = it["gsz"], it["gszzl"], "intraday", it["gztime"]
        else:
            price, today_chg, mode, asof = nav, nav_info["dayChg"], "nav", nav_info["date"]

        cur_value = round(shares * price)
        ret = (cur_value / cost - 1) if cost else 0
        pnl = cur_value - cost
        f = {
            "code": code,
            "name": b["name"],
            "shares": shares,
            "cost": cost,
            "nav": nav,
            "navDate": nav_info["date"],
            "dayChg": nav_info["dayChg"],
            "gsz": it["gsz"] if it else None,
            "gszzl": it["gszzl"] if it else None,
            "gztime": it["gztime"] if it else None,
            "intradayDate": it["date"] if it else None,
            "price": price,
            "mode": mode,
            "todayChg": today_chg,
            "curValue": cur_value,
            "ret": ret,
            "pnl": pnl,
            "score": b.get("score", 3),
        }
        adv = advice(f)
        f["advice"] = adv[0]
        f["adviceReason"] = adv[1]
        funds.append(f)
        total_cost += cost
        total_value += cur_value
        if nav_info["date"]:
            nav_dates.append(nav_info["date"])

    total_pnl = total_value - total_cost
    total_ret = (total_pnl / total_cost) if total_cost else 0

    global_mode = "intraday" if any(f["mode"] == "intraday" for f in funds) else "nav"
    intraday_asof = next((f["gztime"] for f in funds if f["mode"] == "intraday"), None)
    intraday_date = next((f["intradayDate"] for f in funds if f["mode"] == "intraday"), "")
    nav_date = max(nav_dates) if nav_dates else ""

    out = {
        "updatedAt": beijing_now().strftime("%Y-%m-%d %H:%M"),
        "navDate": nav_date,
        "mode": global_mode,
        "intradayDate": intraday_date,
        "intradayAsOf": intraday_asof,
        "totalCost": total_cost,
        "totalValue": total_value,
        "totalPnl": total_pnl,
        "totalRet": total_ret,
        "funds": funds,
    }
    json.dump(out, open(LIVE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("OK mode=%s navDate=%s intraday=%s totalRet=%.2f%% totalPnl=%d"
          % (global_mode, nav_date, intraday_date, total_ret * 100, total_pnl))


if __name__ == "__main__":
    main()
