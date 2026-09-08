"""Read-only Streamlit presentation layer for the paper-trading monitor."""

import argparse
from pathlib import Path

import duckdb
import streamlit as st

from quant_core.dashboard import load_activity, load_dashboard


def _read(db_path: Path, account_id: str):
    """Open a short-lived read-only connection for each Streamlit rerun."""
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return load_dashboard(connection, account_id), load_activity(connection, account_id)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--account-id", default="top50_forward_account")
    args, _ = parser.parse_known_args()
    st.set_page_config(page_title="量化模拟盘", layout="wide")
    st.title("量化模拟盘")
    account_id = st.sidebar.text_input("账户 ID", args.account_id)
    try:
        data, activity = _read(Path(args.db), account_id)
    except Exception as error:
        st.error(f"无法读取看板：{error}")
        return

    summary = data["summary"]
    metrics = st.columns(4)
    metrics[0].metric("总资产", f"¥{summary['equity']:,.2f}")
    metrics[1].metric("现金", f"¥{summary['cash']:,.2f}")
    metrics[2].metric("持仓市值", f"¥{summary['market_value']:,.2f}")
    metrics[3].metric("最大回撤", f"{summary['max_drawdown']:.2%}")
    rejected = [row for row in activity["orders"] if row["status"] == "REJECTED"]
    if data["pending_exits"] or rejected:
        st.warning("需要关注：" + "；".join(
            [f"{row['ticker']} {row['trigger']}" for row in data["pending_exits"]] +
            [f"{row['ticker']} {row['reason']}" for row in rejected[:5]]
        ))
    overview, nav_tab, positions, recommendations, risk, activity_tab = st.tabs(
        ["总览", "净值", "持仓", "推荐", "风控", "活动记录"]
    )
    with overview:
        st.json(data["exit_rules"], expanded=False)
    with nav_tab:
        st.subheader("净值")
        st.line_chart({"NAV": [row["unit_nav"] for row in data["nav"]]})
        st.subheader("回撤")
        st.line_chart({"Drawdown": [row["drawdown"] for row in data["nav"]]})
        st.dataframe(data["nav"], use_container_width=True)
    with positions:
        st.dataframe(data["positions"], use_container_width=True)
    with recommendations:
        st.dataframe(data["recommendations"], use_container_width=True)
    with risk:
        st.dataframe(data["pending_exits"], use_container_width=True)
        st.caption("止损 10% · 盈利 15% 后从最高收盘价回撤 5% · 最长持有 60 个交易日")
    with activity_tab:
        st.subheader("订单")
        st.dataframe(activity["orders"], use_container_width=True)
        st.subheader("成交")
        st.dataframe(activity["executions"], use_container_width=True)
        st.subheader("拒单原因")
        st.dataframe(activity["reject_reasons"], use_container_width=True)


if __name__ == "__main__":
    main()
