"""Read-only Streamlit presentation layer for the paper-trading monitor."""

import argparse
from pathlib import Path
from time import sleep

import altair as alt
import duckdb
import streamlit as st

from quant_core.dashboard import load_activity, load_dashboard, load_news_monitor, load_track_evaluations


def _read(db_path: Path, account_id: str):
    """Open a short-lived read-only connection for every Streamlit rerun."""
    return _retry_read(lambda: _read_once(db_path, account_id))


def _read_once(db_path: Path, account_id: str):
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return (load_dashboard(connection, account_id), load_activity(connection, account_id),
                load_news_monitor(connection), load_track_evaluations(connection))
    finally:
        connection.close()


def _retry_read(operation):
    error = None
    for _ in range(30):
        try:
            return operation()
        except duckdb.IOException as caught:
            error = caught
            sleep(0.5)
    raise error


def _accounts(db_path: Path):
    return _retry_read(lambda: _accounts_once(db_path))


def _accounts_once(db_path: Path):
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return connection.execute("SELECT account_id, account_name FROM sim_accounts ORDER BY account_name").fetchall()
    finally:
        connection.close()


def _currency(value):
    return "—" if value is None else f"¥{value:,.2f}"


def _percent(value):
    return "—" if value is None else f"{value:.2%}"


def _position_rows(positions):
    return [{"代码": row["ticker"], "名称": row["security_name"], "数量": row["shares"], "成本": _currency(row["cost"]), "最新收盘": _currency(row["last_close"]),
             "收盘日期": row["price_date"] or "—", "浮动收益": _percent(row["unrealized_return"])} for row in positions]


def _portfolio_rows(positions, total_equity):
    """Build a decision-oriented position table from read-only dashboard data."""
    rows = []
    for row in positions:
        market_value = None if row["last_close"] is None else row["last_close"] * row["shares"]
        profit = None if market_value is None else market_value - row["cost"]
        weight = None if market_value is None or not total_equity else market_value / total_equity
        unit_cost = None if not row["shares"] else row["cost"] / row["shares"]
        rows.append({
            "代码": row["ticker"], "名称": row["security_name"], "数量": row["shares"],
            "成本价": _currency(unit_cost), "最新收盘": _currency(row["last_close"]),
            "浮动盈亏": _currency(profit), "浮动收益": _percent(row["unrealized_return"]),
            "仓位": _percent(weight), "数据日期": row["price_date"] or "—",
        })
    return sorted(rows, key=lambda item: (item["浮动收益"] == "—", item["浮动收益"]))


def _nav_delta(nav):
    if len(nav) < 2:
        return None
    return nav[-1]["equity"] - nav[-2]["equity"]


def _nav_chart(nav, field, title, color, tooltip_format):
    """Show small NAV movements without forcing the vertical scale to zero."""
    return alt.Chart(alt.Data(values=nav)).mark_line(color=color, point=True).encode(
        x=alt.X("date:T", title="日期"),
        y=alt.Y(f"{field}:Q", title=title, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T", title="日期"), alt.Tooltip(f"{field}:Q", title=title, format=tooltip_format)],
    ).properties(height=250)


def _recommendation_rows(recommendations):
    return [{"轨道": row["scope"], "目标交易日": row["target_date"], "排名": row["rank"], "代码": row["ticker"], "名称": row["security_name"],
             "排序分": f"{row['score']:.4f}", "参考收盘": _currency(row["reference_close"])} for row in recommendations]


def _order_rows(orders):
    return [{"日期": row["date"], "代码": row["ticker"], "名称": row["security_name"], "方向": row["direction"], "数量": row["shares"],
             "状态": row["status"], "原因": row["reason"] or "—"} for row in orders]


def _execution_rows(executions):
    return [{"日期": row["date"], "代码": row["ticker"], "名称": row["security_name"], "方向": row["direction"], "成交价": _currency(row["price"]),
             "数量": row["shares"], "成交额": _currency(row["amount"])} for row in executions]


def _empty(message: str) -> None:
    st.info(message, icon="ℹ️")


def _news_rows(documents):
    return [{"来源": row["source"], "发布时间": row["published_at"], "接收时间": row["received_at"],
             "标题": row["headline"], "原始快照哈希": (row["artifact_sha256"] or "—")[:12]} for row in documents]


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default="data/top50/quant_dashboard.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    args, _ = parser.parse_known_args()
    db_path = Path(args.db)
    st.set_page_config(page_title="量化模拟盘", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
    st.markdown("""
    <style>
      .block-container {max-width: 1440px; padding-top: 1.7rem; padding-bottom: 2.5rem;}
      [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: .65rem .8rem;}
      [data-testid="stMetricLabel"] {font-size: .86rem; color: #475569;}
      [data-testid="stMetricValue"] {font-size: 1.55rem;}
    </style>
    """, unsafe_allow_html=True)

    try:
        accounts = _accounts(db_path)
    except Exception as error:
        st.error(f"无法读取账户：{error}")
        return
    account_map = {account_id: name for account_id, name in accounts}
    if not account_map:
        st.warning("尚未创建模拟账户。")
        return
    default_index = list(account_map).index(args.account_id) if args.account_id in account_map else 0
    with st.sidebar:
        st.header("账户与数据")
        account_id = st.selectbox("模拟账户", list(account_map), index=default_index,
                                  format_func=lambda value: f"{account_map[value]} · {value}")
        st.caption("日频收盘数据 · 只读看板")
        st.caption("浏览器刷新仅重新读取最近一次已发布的数据，不会创建订单或修改账本。")
        st.divider()
        st.caption("生产基线参与模拟撮合；影子策略仅作对照研究。")

    try:
        data, activity, news, track_evaluations = _read(db_path, account_id)
    except Exception as error:
        st.error(f"无法读取看板：{error}")
        return

    summary, nav = data["summary"], data["nav"]
    latest_date = nav[-1]["date"] if nav else "尚无净值记录"
    st.title(data["account"]["name"])
    st.caption(f"日频模拟账户 · 收盘数据截至 {latest_date} · 非盘中实时行情")
    metrics = st.columns(4)
    metrics[0].metric("总资产", _currency(summary["equity"]))
    metrics[1].metric("可用现金", _currency(summary["cash"]))
    metrics[2].metric("持仓数", len(data["positions"]))
    if not data["margin"]:
        metrics[3].metric("最大回撤", _percent(summary["max_drawdown"]))
    if data["margin"]:
        metrics[3].metric("融资负债", _currency(data["margin"]["debt_balance"]),
                          help=f"年化融资利率：{_percent(data['margin']['annual_financing_rate'])}")
        margin = data["margin"]
        if margin["maintenance_ratio"] is None:
            st.caption("两融风险指标将在首个日终估值完成后显示；当前融资利息累计 "
                       f"{_currency(margin['accrued_interest'])}。")
        else:
            st.caption("两融：担保资产 " + _currency(margin["collateral_assets"]) + " · 净资产 "
                       + _currency(margin["net_assets"]) + " · 维持担保比例 "
                       + _percent(margin["maintenance_ratio"]) + " · 累计融资利息 "
                       + _currency(margin["accrued_interest"]))
    st.caption("较前一日 " + _currency(_nav_delta(nav)) + " · 最大回撤 " + _percent(summary["max_drawdown"]))
    pending_exits = data["pending_exits"]
    rejected_count = sum(item["count"] for item in activity["reject_reasons"])
    state_left, state_right = st.columns((2, 1))
    with state_left:
        if pending_exits:
            st.warning("待处理风控卖单：" + "；".join(f"{row['security_name']}（{row['ticker']}）· {row['trigger']}" for row in pending_exits), icon="⚠️")
        else:
            st.success("账户风控状态正常：当前没有待执行卖单。", icon="✅")
    with state_right:
        refresh = data["local_refresh"]
        if refresh:
            icon = {"SUCCEEDED": "✅", "SKIPPED": "⏭️", "FAILED": "🚨", "RUNNING": "⏳"}.get(refresh["status"], "ℹ️")
            st.info(f"{icon} 数据发布：{refresh['status']} · {refresh['trade_date']}")

    overview, positions_tab, activity_tab, nav_tab, strategy_tab, research_tab = st.tabs(
        ["首页", "我的持仓", "风控与订单", "资产净值", "策略", "研究与审计"]
    )
    with overview:
        left, right = st.columns((2, 1))
        with left:
            st.subheader("资产走势")
            if nav:
                st.altair_chart(_nav_chart(nav, "unit_nav", "单位净值", "#2563eb", ".4f"), use_container_width=True)
            else:
                _empty("尚无 NAV 记录；完成首笔结算后会显示净值曲线。")
        with right:
            st.subheader("今日关注")
            st.metric("待处理卖单", len(pending_exits))
            st.metric("累计拒单", rejected_count)
            st.caption(f"规则：止损 {_percent(data['exit_rules']['stop_loss'])} · 最大持有 {data['exit_rules']['max_holding_days']} 个交易日")
        st.subheader("持仓概览")
        position_view = _portfolio_rows(data["positions"], summary["equity"])
        if position_view:
            st.dataframe(position_view[:8], hide_index=True, width="stretch")
        else:
            _empty("当前无持仓。")
    with nav_tab:
        if nav:
            st.subheader("单位净值")
            st.altair_chart(_nav_chart(nav, "unit_nav", "单位净值", "#2563eb", ".4f"), use_container_width=True)
            st.subheader("回撤")
            st.altair_chart(_nav_chart(nav, "drawdown", "回撤", "#dc2626", ".2%"), use_container_width=True)
            st.dataframe([{"日期": row["date"], "现金": _currency(row["cash"]), "证券市值": _currency(row["market_value"]), "总资产": _currency(row["equity"]), "单位净值": f"{row['unit_nav']:.4f}", "回撤": _percent(row["drawdown"])} for row in nav], hide_index=True, width="stretch")
        else:
            _empty("尚无 NAV 记录。")
    with positions_tab:
        st.subheader("我的持仓")
        st.caption("价格为最近已发布的收盘价；成本、盈亏与仓位均按当前模拟账户计算。")
        position_view = _portfolio_rows(data["positions"], summary["equity"])
        if position_view:
            st.dataframe(position_view, hide_index=True, width="stretch")
        else:
            _empty("当前无持仓。")
    with activity_tab:
        st.subheader("风控与订单")
        if pending_exits:
            st.dataframe([{"代码": row["ticker"], "名称": row["security_name"], "触发原因": row["trigger"], "目标交易日": row["target_date"]} for row in pending_exits], hide_index=True, width="stretch")
        else:
            _empty("没有待执行的风控卖单。")
        st.caption("卖出规则：固定止损 10% · 盈利至少 15% 后从最高收盘价回撤 5% · 最长持有 60 个交易日。")
        order_rows = _order_rows(activity["orders"])
        st.subheader("订单状态")
        if order_rows:
            statuses = sorted({row["状态"] for row in order_rows})
            selected = st.multiselect("订单状态", statuses, default=statuses)
            st.dataframe([row for row in order_rows if row["状态"] in selected], hide_index=True, width="stretch")
        else:
            _empty("尚无订单。")
        st.subheader("最近成交")
        if activity["executions"]:
            st.dataframe(_execution_rows(activity["executions"]), hide_index=True, width="stretch")
        else:
            _empty("尚无成交。")
        st.subheader("拒单汇总")
        if activity["reject_reasons"]:
            st.dataframe([{"拒单原因": row["reason"], "次数": row["count"]} for row in activity["reject_reasons"]], hide_index=True, width="stretch")
        else:
            _empty("尚无拒单记录。")
    with strategy_tab:
        st.subheader("冻结推荐")
        st.caption("策略推荐是全局输出，不等同于当前账户的自动交易指令。生产基线可进入模拟撮合；影子轨不会创建订单。")
        if data["recommendations"]:
            st.dataframe(_recommendation_rows(data["recommendations"]), hide_index=True, width="stretch")
        else:
            _empty("尚无冻结推荐。")
        st.subheader("ML 影子策略")
        ml_shadow = data["ml_shadow"]
        if ml_shadow["model"] is None:
            _empty("尚未登记 ML 模型；训练时传入 --db 即可登记。")
        else:
            model = ml_shadow["model"]
            st.caption(f"仅研究，不创建订单 · 训练截止 {model['trained_through_date']} · 样本 {model['training_rows']:,} · 模型 {model['sha256'][:12]}")
            if ml_shadow["recommendations"]:
                st.dataframe([{"目标日": row["target_date"], "代码": row["ticker"], "名称": row["security_name"], "排名": row["rank"], "预测超额收益": _percent(row["score"])} for row in ml_shadow["recommendations"]], hide_index=True, width="stretch")
            else:
                _empty("模型已登记，尚无冻结的 ML 影子推荐。")
    with research_tab:
        st.subheader("基线与事件影子评估")
        st.caption("研究数据不直接创建账户订单。展开下方项目可查看新闻、公告风险与外部请求审计。")
        if track_evaluations:
            st.dataframe([{"目标日": row["target_date"], "轨道": "事件影子" if row["mode"] == "SHADOW" else "生产基线",
                           "评估版本": row["evaluation_version"], "推荐数": row["recommendation_count"],
                           "可成交率": _percent(row["execution_rate"]), "T+1": _percent(row["t1_return"]),
                           "T+1 超额": _percent(row["t1_excess"]), "T+5": _percent(row["t5_return"]),
                           "T+5 超额": _percent(row["t5_excess"]), "最大回撤": _percent(row["max_drawdown"])}
                          for row in track_evaluations], hide_index=True, width="stretch")
        else:
            _empty("尚无成熟评估；在推荐后的完整 T+20 数据到齐后运行影子评估脚本。")
        with st.expander("新闻事实与事件研究", expanded=False):
            st.caption("新闻仅在归档、时间因果、来源质量和行业字典校验后，才可能进入影子事件假设；不会直接创建账户订单。")
            if news["hypotheses"]:
                for hypothesis in news["hypotheses"]:
                    status = "✅ 影子可用" if hypothesis["status"] == "SHADOW_ELIGIBLE" else "⛔ 已拒绝"
                    with st.expander(f"{status} · {hypothesis['effective_as_of']} · {hypothesis['evidence_quality']}"):
                        st.caption(f"独立域名：{hypothesis['domain_count']} · 拒绝原因：{hypothesis['rejection_reason'] or '—'}")
                        if hypothesis["impacts"]:
                            st.dataframe([{"申万代码": item["industry_code"], "行业": item["industry_name"], "方向": item["direction"],
                                           "事件分": item["score"], "预期天数": item["duration_days"], "不确定性": item["uncertainty"]}
                                          for item in hypothesis["impacts"]], hide_index=True, width="stretch")
            else:
                _empty("尚无已生成的事件假设；先归档新闻并运行事件推理。")
            st.subheader("事件簇与独立证据")
            if news["clusters"]:
                st.dataframe([{"事件簇": row["id"][:12], "最近事件时间": row["latest_published_at"],
                               "文档数": row["document_count"], "独立代表": row["representative_count"],
                               "去重/转载": row["excluded_count"], "关联假设": ", ".join(item[:8] for item in row["hypothesis_ids"]) or "—"}
                              for row in news["clusters"]], hide_index=True, width="stretch")
            else:
                _empty("尚未形成事件簇；下一次合格宏观证据分析时会自动创建。")
            st.subheader("最近归档新闻")
            if news["documents"]:
                st.dataframe(_news_rows(news["documents"]), hide_index=True, width="stretch")
            else:
                _empty("尚未归档新闻。")
            st.subheader("个股公告风险评估（影子）")
            st.caption("仅展示正式公告的受控评估；当前不自动拦截生产基线订单。")
            review = news["risk_review_summary"]
            review_metrics = st.columns(4)
            review_metrics[0].metric("高风险评估", review["high_assessment_count"])
            review_metrics[1].metric("已人工复核", review["reviewed_high_count"])
            review_metrics[2].metric("确认风险", review["confirmed_risk_count"])
            review_metrics[3].metric("高风险复核准确率", _percent(review["review_precision"]))
            if news["risk_assessments"]:
                st.dataframe([{"代码": row["ticker"], "风险等级": row["result"]["risk_level"],
                               "风险标签": ", ".join(row["result"]["flags"]) or "—", "公告": row["headline"],
                               "理由": row["result"]["rationale"], "人工复核": row["review"]["label"] if row["review"] else "待复核",
                               "评估 ID": row["assessment_id"], "评估时间": row["created_at"]}
                              for row in news["risk_assessments"]], hide_index=True, width="stretch")
            else:
                _empty("尚无可展示的个股公告风险评估。")
            st.subheader("外部请求审计")
            if news["requests"]:
                st.dataframe([{"来源": row["source"], "请求时间": row["requested_at"], "HTTP": row["status"] or "—",
                               "缓存命中": "是" if row["cache_hit"] else "否", "退避秒数": row["backoff_seconds"],
                               "错误": row["error"] or "—"} for row in news["requests"]], hide_index=True, width="stretch")
            else:
                _empty("尚无外部请求审计记录。")


if __name__ == "__main__":
    main()
