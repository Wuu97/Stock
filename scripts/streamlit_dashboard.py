"""Read-only Streamlit presentation layer for the paper-trading monitor."""

import argparse
from pathlib import Path

import duckdb
import streamlit as st

from quant_core.dashboard import load_activity, load_dashboard


def _read(db_path: Path, account_id: str):
    """Open a short-lived read-only connection for every Streamlit rerun."""
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return load_dashboard(connection, account_id), load_activity(connection, account_id)
    finally:
        connection.close()


def _accounts(db_path: Path):
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
    return [{"代码": row["ticker"], "数量": row["shares"], "成本": _currency(row["cost"]), "最新收盘": _currency(row["last_close"]),
             "收盘日期": row["price_date"] or "—", "浮动收益": _percent(row["unrealized_return"])} for row in positions]


def _recommendation_rows(recommendations):
    return [{"轨道": row["scope"], "目标交易日": row["target_date"], "排名": row["rank"], "代码": row["ticker"],
             "排序分": f"{row['score']:.4f}", "参考收盘": _currency(row["reference_close"])} for row in recommendations]


def _order_rows(orders):
    return [{"日期": row["date"], "代码": row["ticker"], "方向": row["direction"], "数量": row["shares"],
             "状态": row["status"], "原因": row["reason"] or "—"} for row in orders]


def _execution_rows(executions):
    return [{"日期": row["date"], "代码": row["ticker"], "方向": row["direction"], "成交价": _currency(row["price"]),
             "数量": row["shares"], "成交额": _currency(row["amount"])} for row in executions]


def _empty(message: str) -> None:
    st.info(message, icon="ℹ️")


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    args, _ = parser.parse_known_args()
    db_path = Path(args.db)
    st.set_page_config(page_title="量化模拟盘", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

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
        st.header("查看设置")
        account_id = st.selectbox("模拟账户", list(account_map), index=default_index,
                                  format_func=lambda value: f"{account_map[value]} · {value}")
        st.caption("只读看板。刷新页面不会创建订单或修改账本。")
        st.divider()
        st.caption("生产基线会参与模拟撮合；事件影子仅用于对照评估。")

    try:
        data, activity = _read(db_path, account_id)
    except Exception as error:
        st.error(f"无法读取看板：{error}")
        return

    summary, nav = data["summary"], data["nav"]
    st.title("量化模拟盘")
    st.caption(f"{data['account']['name']} · 日频审计账户 · 最近更新：{nav[-1]['date'] if nav else '尚无净值记录'}")
    metrics = st.columns(4)
    metrics[0].metric("总资产", _currency(summary["equity"]))
    metrics[1].metric("可用现金", _currency(summary["cash"]))
    metrics[2].metric("持仓市值", _currency(summary["market_value"]))
    metrics[3].metric("最大回撤", _percent(summary["max_drawdown"]))

    rejected = [row for row in activity["orders"] if row["status"] == "REJECTED"]
    pending_exits = data["pending_exits"]
    if rejected:
        st.error("存在拒单：" + "；".join(f"{row['ticker']} · {row['reason']}" for row in rejected[:5]), icon="🚨")
    if pending_exits:
        st.warning("待执行风控卖单：" + "；".join(f"{row['ticker']} · {row['trigger']}" for row in pending_exits), icon="⚠️")
    if not rejected and not pending_exits:
        st.success("当前没有拒单或待执行风控卖单。", icon="✅")

    overview, nav_tab, positions_tab, rec_tab, risk_tab, activity_tab = st.tabs(
        ["总览", "资产净值", "当前持仓", "冻结推荐", "风控状态", "订单与成交"]
    )
    with overview:
        left, right = st.columns((2, 1))
        with left:
            st.subheader("最近净值")
            if nav:
                st.line_chart(nav, x="date", y="unit_nav", color="#2563eb", height=260)
            else:
                _empty("尚无 NAV 记录；完成首笔结算后会显示净值曲线。")
        with right:
            st.subheader("统一卖出规则")
            st.metric("固定止损", _percent(data["exit_rules"]["stop_loss"]))
            st.metric("技术止盈回撤", _percent(data["exit_rules"]["trailing_drawdown"]))
            st.caption(f"盈利门槛 {_percent(data['exit_rules']['take_profit_gate'])} · 最长持有 {data['exit_rules']['max_holding_days']} 个交易日")
        st.subheader("最新冻结推荐")
        if data["recommendations"]:
            st.dataframe(_recommendation_rows(data["recommendations"][:5]), hide_index=True, width="stretch")
        else:
            _empty("尚无冻结推荐。")
    with nav_tab:
        if nav:
            st.subheader("单位净值")
            st.line_chart(nav, x="date", y="unit_nav", color="#2563eb", height=280)
            st.subheader("回撤")
            st.line_chart(nav, x="date", y="drawdown", color="#dc2626", height=220)
            st.dataframe([{"日期": row["date"], "现金": _currency(row["cash"]), "证券市值": _currency(row["market_value"]), "总资产": _currency(row["equity"]), "单位净值": f"{row['unit_nav']:.4f}", "回撤": _percent(row["drawdown"])} for row in nav], hide_index=True, width="stretch")
        else:
            _empty("尚无 NAV 记录。")
    with positions_tab:
        st.subheader("当前持仓")
        if data["positions"]:
            st.dataframe(_position_rows(data["positions"]), hide_index=True, width="stretch")
        else:
            _empty("当前无持仓。")
    with rec_tab:
        st.subheader("冻结推荐")
        st.caption("生产基线可进入模拟撮合；事件影子不会创建订单。推荐为全局策略输出，并非账户专属。")
        if data["recommendations"]:
            st.dataframe(_recommendation_rows(data["recommendations"]), hide_index=True, width="stretch")
        else:
            _empty("尚无冻结推荐。")
    with risk_tab:
        st.subheader("待执行卖单")
        if pending_exits:
            st.dataframe([{"代码": row["ticker"], "触发原因": row["trigger"], "目标交易日": row["target_date"]} for row in pending_exits], hide_index=True, width="stretch")
        else:
            _empty("没有待执行的风控卖单。")
        st.caption("固定止损 10% · 盈利至少 15% 后从持仓期最高收盘价回撤 5% · 最长持有 60 个交易日")
    with activity_tab:
        order_rows = _order_rows(activity["orders"])
        st.subheader("订单")
        if order_rows:
            statuses = sorted({row["状态"] for row in order_rows})
            selected = st.multiselect("订单状态", statuses, default=statuses)
            st.dataframe([row for row in order_rows if row["状态"] in selected], hide_index=True, width="stretch")
        else:
            _empty("尚无订单。")
        st.subheader("成交")
        if activity["executions"]:
            st.dataframe(_execution_rows(activity["executions"]), hide_index=True, width="stretch")
        else:
            _empty("尚无成交。")
        st.subheader("拒单汇总")
        if activity["reject_reasons"]:
            st.dataframe([{"拒单原因": row["reason"], "次数": row["count"]} for row in activity["reject_reasons"]], hide_index=True, width="stretch")
        else:
            _empty("尚无拒单记录。")


if __name__ == "__main__":
    main()
