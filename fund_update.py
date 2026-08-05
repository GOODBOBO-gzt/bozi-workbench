#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金每日自动更新脚本（GitHub Actions 定时运行）
- 读取 fund-basis.json：每只基金的固定基础持仓（代码 / 份额 / 投入成本 / 质量分）
- 调用天天基金收盘净值接口获取最新净值
- 计算每只基金的当前市值、持有收益率、浮动盈亏
- 基于收益率与基金质量给出当天操作建议
- 输出 fund-live.json 供 GitHub Pages 页面读取
"""
import json
import os
import sys
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_PATH = os.path.join(HERE, "fund-basis.json")
LIVE_PATH = os.path.join(HERE, "fund-live.json")


def fetch_nav(code):
    url = ("https://api.fund.eastmoney.com/f10/lsjz?fundCode=%s"
           "&pageIndex=1&pageSize=1&_=%d") % (code, int(datetime.datetime.now().timestamp() * 1000))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://fundf10.eastmoney.com/"
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    item = data.get("Data", {}).get("LSJZList", [{}])[0]
    return {
        "date": item.get("FSRQ"),
        "nav": float(item.get("DWJZ") or 0),
        "dayChg": float(item.get("JZZZL") or 0),
    }


def advice(f):
    ret = f["ret"]
    score = f.get("score", 3)
    if ret >= 0.30:
        return ("减仓止盈", "已盈利超30%，可分批止盈锁定收益")
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
    funds = []
    total_cost = 0
    total_value = 0
    nav_date = ""
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
        cur_value = round(shares * nav)
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
            nav_date = nav_info["date"]
    total_pnl = total_value - total_cost
    total_ret = (total_pnl / total_cost) if total_cost else 0
    out = {
        "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "navDate": nav_date,
        "totalCost": total_cost,
        "totalValue": total_value,
        "totalPnl": total_pnl,
        "totalRet": total_ret,
        "funds": funds,
    }
    json.dump(out, open(LIVE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("OK navDate=%s totalRet=%.2f%% totalPnl=%d" % (nav_date, total_ret * 100, total_pnl))


if __name__ == "__main__":
    main()
