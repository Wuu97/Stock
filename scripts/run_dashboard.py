"""Serve a dependency-free local dashboard for the paper-trading account."""

import argparse
import html
import json
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

import duckdb

from quant_core.dashboard import load_dashboard


def _page(data: dict) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(row['ticker'])}</td><td>{row['shares']}</td><td>{row['cost']:.2f}</td>"
        f"<td>{_money(row['last_close'])}</td><td>{_percent(row['unrealized_return'])}</td></tr>"
        for row in data["positions"]
    ) or "<tr><td colspan='5'>暂无持仓</td></tr>"
    exits = "".join(
        f"<li>{html.escape(row['ticker'])}：{html.escape(row['trigger'])}，{row['target_date']} 开盘执行</li>"
        for row in data["pending_exits"]
    ) or "<li>暂无待执行卖单</li>"
    recommendations = "".join(
        f"<tr><td>{row['target_date']}</td><td>{html.escape(row['ticker'])}</td><td>{row['rank']}</td><td>{row['score']:.4f}</td></tr>"
        for row in data["recommendations"]
    ) or "<tr><td colspan='4'>暂无冻结推荐</td></tr>"
    latest_nav = data["nav"][-1] if data["nav"] else None
    nav_text = "暂无估值" if latest_nav is None else f"{latest_nav['equity']:.2f}（回撤 {_percent(latest_nav['drawdown'])}）"
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>模拟盘看板</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:32px;background:#f6f8fb;color:#182230}}h1{{margin-bottom:4px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}section{{background:white;border:1px solid #e5e9f0;border-radius:12px;padding:18px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #edf0f4;text-align:left}}.metric{{font-size:24px;font-weight:700}}small{{color:#64748b}}</style>
<h1>{html.escape(data['account']['name'])}</h1><small>账户 {html.escape(data['account']['id'])} · 日线模拟盘</small>
<div class='grid'><section><small>初始资金</small><div class='metric'>{data['account']['initial_cash']:.2f}</div></section><section><small>最新净值</small><div class='metric'>{nav_text}</div></section><section><small>统一风控</small><div>止损 10% · 盈利 15% 后回撤 5% · 最长 60 日</div></section></div>
<section><h2>当前持仓</h2><table><tr><th>标的</th><th>股数</th><th>成本</th><th>最新收盘</th><th>浮动收益</th></tr>{rows}</table></section>
<section><h2>待执行风控卖单</h2><ul>{exits}</ul></section>
<section><h2>最近冻结推荐</h2><table><tr><th>目标日</th><th>标的</th><th>排名</th><th>得分</th></tr>{recommendations}</table></section>
</html>"""


def _money(value):
    return "—" if value is None else f"{value:.2f}"


def _percent(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    database = Path(args.db)

    def application(environ, start_response):
        requested_account = parse_qs(environ.get("QUERY_STRING", "")).get("account_id", [args.account_id])[0]
        connection = duckdb.connect(str(database), read_only=True)
        try:
            data = load_dashboard(connection, requested_account)
        finally:
            connection.close()
        if environ["PATH_INFO"] == "/api/dashboard":
            body, content_type = json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        else:
            body, content_type = _page(data).encode(), "text/html; charset=utf-8"
        start_response("200 OK", [("Content-Type", content_type), ("Content-Length", str(len(body)))])
        return [body]

    print(f"Dashboard: http://{args.host}:{args.port}")
    make_server(args.host, args.port, application).serve_forever()


if __name__ == "__main__":
    main()
