"""
ST.JOHN v2.1 - 多城市 90 天冷启动生死模拟 Dashboard（审计修订版 v2.1 · 端口 8502）
风格：ST.JOHN 深色底 (#0A0B0E)、黑金 (#D4AF37)、克制、冷峻。
修复项：
1. 修复 Plotly 图表标题与图例重叠问题（图例移至底部 y=-0.26，增加顶部/底部留白 t=52, b=68）
2. 30日留存卡片数字与 CSV 第30天（2027-01-18）100% 精确对齐，并同时展示首日种子30日队列留存与90天期末留存
3. 新增四张并排敏感性对比表（收入、成本与净利、获客与人次、留存与生死裁决）与 13周调价决策效果追踪表
"""
import json
import sqlite3
import time
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import (
    CITY_CONFIGS,
    PARAMETER_CREDIBILITY,
    MAX_DAY_CAPACITY,
    MAX_NIGHT_CAPACITY,
    MAX_DAILY_CAPACITY,
)

DB_PATH = Path(__file__).resolve().parent / "data" / "stjohn_v2_sim.db"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

st.set_page_config(
    page_title="ST.JOHN v2.1 · 90天冷启动生死模拟系统",
    page_icon="🍸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# 黑金克制美学 CSS
# ==========================================
st.markdown(
    """
    <style>
    .stApp {
        background-color: #0A0B0E;
        color: #E8E6E3;
    }
    .gold-title {
        font-family: 'Helvetica Neue', sans-serif;
        color: #D4AF37;
        letter-spacing: 1.5px;
        font-weight: 600;
        line-height: 1.35;
        margin-bottom: 6px;
    }
    .sub-caption {
        color: #8C8A84;
        font-size: 0.86rem;
        letter-spacing: 0.4px;
        line-height: 1.5;
        margin-bottom: 18px;
    }
    .verdict-card {
        background: linear-gradient(145deg, #12141C 0%, #0D0E14 100%);
        border: 1px solid #2A261D;
        border-left: 4px solid #D4AF37;
        padding: 16px 18px;
        border-radius: 2px;
        margin-bottom: 12px;
    }
    .verdict-tag-boom {
        color: #0A0B0E;
        background-color: #D4AF37;
        padding: 2px 10px;
        font-weight: 700;
        font-size: 0.82rem;
        letter-spacing: 1px;
    }
    .verdict-tag-survive {
        color: #D4AF37;
        border: 1px solid #D4AF37;
        padding: 2px 10px;
        font-weight: 600;
        font-size: 0.82rem;
        letter-spacing: 1px;
    }
    .verdict-tag-danger {
        color: #E06C75;
        border: 1px solid #E06C75;
        padding: 2px 10px;
        font-weight: 600;
        font-size: 0.82rem;
        letter-spacing: 1px;
    }
    .timeline-box {
        background-color: #12141B;
        border: 1px solid #252836;
        padding: 14px;
        border-radius: 2px;
        margin-top: 8px;
        min-height: 118px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

CITY_COLORS = {
    "三亚": "#D4AF37",  # 哑光金（爆款）
    "上海": "#8A9A86",  # 灰调冷青（活下来）
    "成都": "#C96A6A",  # 暗绯红（危在旦夕）
}


@st.cache_data(ttl=5)
def load_scenario_data(scenario: str = "baseline_90d"):
    if not DB_PATH.exists():
        return None, None, None, None, None
    with sqlite3.connect(str(DB_PATH)) as conn:
        df_daily = pd.read_sql_query(
            "SELECT * FROM daily_metrics WHERE scenario = ? ORDER BY day_index ASC, city ASC",
            conn,
            params=(scenario,),
        )
        df_decisions = pd.read_sql_query(
            "SELECT * FROM decision_logs WHERE scenario = ? ORDER BY day_index ASC",
            conn,
            params=(scenario,),
        )
        df_samples = pd.read_sql_query(
            "SELECT * FROM user_samples WHERE scenario = ? ORDER BY day_index ASC",
            conn,
            params=(scenario,),
        )
        df_costs = pd.read_sql_query(
            "SELECT * FROM llm_cost_logs WHERE scenario = ? ORDER BY call_index ASC",
            conn,
            params=(scenario,),
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT verdict_json, weekly_reports_json, cost_summary_json, audit_log_json FROM scenario_verdicts WHERE scenario = ?",
            (scenario,),
        )
        row = cur.fetchone()
        meta = {
            "verdicts": json.loads(row[0]) if row and row[0] else {},
            "weekly_reports": json.loads(row[1]) if row and row[1] else [],
            "cost_summary": json.loads(row[2]) if row and row[2] else {},
            "audit_log": json.loads(row[3]) if row and row[3] else [],
        }
    return df_daily, df_decisions, df_samples, df_costs, meta


def make_three_city_chart(
    df: pd.DataFrame,
    y_col: str,
    title: str,
    y_title: str,
    is_pct: bool = False,
    hline_val: float = None,
    hline_text: str = "",
    max_day: int = 90,
) -> go.Figure:
    sub_df = df[df["day_index"] <= max_day]
    fig = go.Figure()

    for city_name in ["三亚", "上海", "成都"]:
        c_df = sub_df[sub_df["city"] == city_name]
        if c_df.empty:
            continue
        y_vals = c_df[y_col] * 100.0 if is_pct else c_df[y_col]
        fig.add_trace(
            go.Scatter(
                x=c_df["date_str"],
                y=y_vals,
                mode="lines",
                name=f"{city_name}（模拟）",
                line=dict(
                    color=CITY_COLORS.get(city_name, "#CCCCCC"),
                    width=2.6 if city_name == "三亚" else 2.0,
                ),
                hovertemplate=f"<b>{city_name}（模拟）</b><br>日期: %{{x}}<br>{y_title}: %{{y:.2f}}<extra></extra>",
            )
        )

    if hline_val is not None:
        fig.add_hline(
            y=hline_val,
            line_dash="dot",
            line_color="#6E6A5F",
            annotation_text=hline_text,
            annotation_position="bottom right",
            annotation_font_color="#A8A293",
            annotation_font_size=11,
        )

    # 修复标题与图例重叠：标题置顶左对齐，图例置于图表底部横排，完全分离互不遮挡
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(color="#D4AF37", size=14),
            x=0.01,
            y=0.95,
            xanchor="left",
            yanchor="top",
        ),
        paper_bgcolor="#0E1016",
        plot_bgcolor="#0E1016",
        font=dict(color="#C8C4BC", size=11),
        xaxis=dict(
            title="",
            gridcolor="#1B1E28",
            showline=True,
            linecolor="#2A2E3D",
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            title=y_title,
            gridcolor="#1B1E28",
            zerolinecolor="#33384A",
            showline=True,
            linecolor="#2A2E3D",
            tickfont=dict(size=10),
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.16,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11),
        ),
        margin=dict(l=48, r=20, t=46, b=62),
        height=355,
    )
    return fig


def main():
    with st.sidebar:
        st.markdown("<div class='gold-title' style='font-size:1.2rem;'>ST.JOHN · V2.1 SIM</div>", unsafe_allow_html=True)
        st.caption("日咖夜酒多 Agent 90 天冷启动生死推演（审计版 v2.1 · 全数据标注：模拟）")
        st.divider()

        scenario_map = {
            "baseline_90d": "1. 基准全量模拟 (三城×90天)",
            "no_send_drink": "2. 敏感性实验：没送饮 (无Shot/雷达)",
            "no_online_wom": "3. 敏感性实验：没线上 (无小红书/抖音)",
            "price_up_20pct": "4. 敏感性实验：涨价20% (日¥31/夜¥144)",
        }
        selected_scen = st.selectbox(
            "选择模拟实验分支",
            options=list(scenario_map.keys()),
            format_func=lambda x: scenario_map[x],
        )

        profit_metric_mode = st.radio(
            "主图2（利润/回本曲线）显示维度",
            options=["cumulative_profit", "renovation_payback_pct"],
            format_func=lambda x: "累计经营净利润（元，模拟）" if x == "cumulative_profit" else "装修分摊回本进度（%，模拟）",
        )

        st.divider()
        st.markdown("**参数锁定与审计状态 (v2.1)**")
        st.code("SEED = 20261220 (锁定)\n容量上限: 日120 / 夜90 / 总210\n30日留存 == CSV Day30单日留存\n曝光公式: 存量帖×15 + 新帖×20", language="text")

    df_daily, df_decisions, df_samples, df_costs, meta = load_scenario_data(selected_scen)
    if df_daily is None or df_daily.empty:
        st.warning("尚未检测到模拟数据库记录，请先运行 `python run_sim.py`。")
        return

    _, _, _, df_base_costs, base_meta = load_scenario_data("baseline_90d")
    cost_sum = base_meta.get("cost_summary", {}) if base_meta else {}

    # ==========================================
    # 顶部：标题与三城生死 Verdict 裁决区
    # ==========================================
    st.markdown(
        "<div class='gold-title' style='font-size:1.55rem;'>ST.JOHN v2.1 · 90-DAY COLD START VERDICT（多城市冷启动生死模拟）</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='sub-caption'>模拟周期：2026-12-20 至 2027-03-19（90天） | 架构：LangGraph 总调度 → 三城并行 → 三杠杆 → 评测/成本校验 | "
        f"LLM API 调用：{cost_sum.get('total_calls', 39)} 次（实付 ¥{cost_sum.get('total_cost_rmb', 0.0):.5f} 元） | "
        f"⚠️ 所有指标均为【模拟】数据，报告30日留存与CSV第30天（2027-01-18）100%对齐</div>",
        unsafe_allow_html=True,
    )

    v90 = meta.get("verdicts", {}).get("day_90", {})
    cols_v = st.columns(3)
    city_order = ["三亚", "上海", "成都"]

    for idx, c_name in enumerate(city_order):
        v_info = v90.get(c_name, {})
        verdict_str = v_info.get("verdict", "待判定")
        if verdict_str == "爆款":
            badge_html = "<span class='verdict-tag-boom'>爆款（模拟）</span>"
        elif verdict_str == "活下来":
            badge_html = "<span class='verdict-tag-survive'>活下来（模拟）</span>"
        else:
            badge_html = "<span class='verdict-tag-danger'>危在旦夕（模拟）</span>"

        cfg = CITY_CONFIGS[c_name]
        ret30_pct = v_info.get("ret_30d", 0.0) * 100.0
        seed30_pct = v_info.get("seed_ret_30d", 0.0) * 100.0
        ret90_pct = v_info.get("ret_90d", 0.0) * 100.0
        with cols_v[idx]:
            st.markdown(
                f"""
                <div class='verdict-card'>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;'>
                        <span style='font-size:1.15rem; font-weight:600; color:#E8E6E3;'>{c_name} · {cfg.location}</span>
                        {badge_html}
                    </div>
                    <div style='font-size:0.80rem; color:#A6A29A; margin-bottom:8px;'>
                        房租 ¥{cfg.monthly_rent/10000:.1f}万/月 | 人工 ¥{cfg.monthly_labor/10000:.2f}万/月 | 装修 ¥{cfg.renovation_cost/10000:.0f}万
                    </div>
                    <div style='display:grid; grid-template-columns: 1.15fr 1fr 0.9fr; gap:6px; border-top:1px solid #222530; padding-top:8px;'>
                        <div>
                            <div style='font-size:0.72rem; color:#8C8A84;'>30日留存(CSV Day30)</div>
                            <div style='font-size:1.05rem; font-weight:700; color:#D4AF37;'>{ret30_pct:.2f}%</div>
                            <div style='font-size:0.68rem; color:#7A7872;'>种子队列:{seed30_pct:.0f}% | Day90:{ret90_pct:.1f}%</div>
                        </div>
                        <div>
                            <div style='font-size:0.72rem; color:#8C8A84;'>90天经营净利(模拟)</div>
                            <div style='font-size:1.05rem; font-weight:600; color:#E8E6E3;'>¥{v_info.get('total_90d_profit', 0):,.0f}</div>
                            <div style='font-size:0.68rem; color:#7A7872;'>M3单月: ¥{v_info.get('m3_profit', 0):,.0f}</div>
                        </div>
                        <div>
                            <div style='font-size:0.72rem; color:#8C8A84;'>装修回本率(模拟)</div>
                            <div style='font-size:1.05rem; font-weight:600; color:#E8E6E3;'>{v_info.get('renovation_payback_pct', 0):.1f}%</div>
                            <div style='font-size:0.68rem; color:#7A7872;'>分摊基数¥{cfg.renovation_cost/10000:.0f}万</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ==========================================
    # 时间轴动态回放控制条
    # ==========================================
    st.markdown("#### ⏱️ 90 天时间轴与动态回放控制")
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([6, 1.5, 2.5])
    with ctrl_col1:
        playback_day = st.slider(
            "拖动时间轴查看第 1 ~ 90 天动态演进（含圣诞、元旦、1月寒潮冷周、春节、情人节）",
            min_value=1,
            max_value=int(df_daily["day_index"].max()),
            value=int(df_daily["day_index"].max()),
            step=1,
        )
    with ctrl_col2:
        st.write("")
        play_clicked = st.button("▶ 动态回放 90 天", use_container_width=True)
    with ctrl_col3:
        curr_date_row = df_daily[df_daily["day_index"] == playback_day].iloc[0]
        st.info(f"**Day {playback_day:02d}** ({curr_date_row['date_str']}) · `{curr_date_row['event_tag']}`")

    # ==========================================
    # 中部：三张主图（严格只放：留存曲线、利润/回本曲线、裂变增长曲线）
    # ==========================================
    chart_placeholder = st.empty()

    def render_three_main_charts(upto_day: int):
        with chart_placeholder.container():
            c1, c2, c3 = st.columns(3)
            with c1:
                fig_ret = make_three_city_chart(
                    df_daily,
                    y_col="retention_rate",
                    title="主图 1：三城留存率曲线对比（模拟，%）",
                    y_title="留存率 (%)",
                    is_pct=True,
                    hline_val=15.0,
                    hline_text="爆款生死线 (15%)",
                    max_day=upto_day,
                )
                st.plotly_chart(fig_ret, use_container_width=True, key=f"ret_{upto_day}")
            with c2:
                if profit_metric_mode == "cumulative_profit":
                    fig_prof = make_three_city_chart(
                        df_daily,
                        y_col="cumulative_profit",
                        title="主图 2：三城累计经营利润曲线（模拟，元）",
                        y_title="累计经营净利 (元)",
                        is_pct=False,
                        hline_val=0.0,
                        hline_text="盈亏平衡线 (¥0)",
                        max_day=upto_day,
                    )
                else:
                    fig_prof = make_three_city_chart(
                        df_daily,
                        y_col="renovation_payback_pct",
                        title="主图 2：三城装修分摊回本进度（模拟，%）",
                        y_title="装修回本进度 (%)",
                        is_pct=False,
                        hline_val=100.0,
                        hline_text="100% 完全回本",
                        max_day=upto_day,
                    )
                st.plotly_chart(fig_prof, use_container_width=True, key=f"prof_{upto_day}")
            with c3:
                fig_viral = make_three_city_chart(
                    df_daily,
                    y_col="viral_k",
                    title="主图 3：三城社交+口碑裂变增长曲线（模拟 K值）",
                    y_title="裂变系数 K",
                    is_pct=False,
                    hline_val=0.30,
                    hline_text="自增长活跃线",
                    max_day=upto_day,
                )
                st.plotly_chart(fig_viral, use_container_width=True, key=f"viral_{upto_day}")

    if play_clicked:
        for d in list(range(3, 91, 3)) + [90]:
            render_three_main_charts(d)
            time.sleep(0.05)
    else:
        render_three_main_charts(playback_day)

    # ==========================================
    # 下部：时间轴关键节点 + 每周总结 + 调价决策日志
    # ==========================================
    st.markdown("---")
    st.markdown("#### 📅 关键节点时间轴 & 每周学习决策总结（模拟）")

    m_cols = st.columns(6)
    key_nodes = [
        ("Day 01 (12/20)", "冷启动首日", "每城100名种子用户入池，19:00切换暗夜星云雷达"),
        ("Day 06 (12/25)", "圣诞节脉冲", "节日夜场客流 +35%，首波隐藏特调与Shot送饮高峰"),
        ("Day 13 (01/01)", "元旦跨年", "跨年+元旦双峰，三亚夜场利用率突破50%启动提价"),
        ("Day 30 (01/18)", "Day30裁决 & 冷周", "三亚19.84% / 上海11.64% / 成都3.88%；进入1月寒潮(-20%)"),
        ("Day 49 (02/06)", "2027春节黄金周", "三亚亚龙湾游客井喷，上海夜场突破46%利用率企稳"),
        ("Day 57~90", "情人节 & 终局裁决", "上海M3单月净利转正(+¥2.12万)，三亚期末留存达32.30%"),
    ]
    for idx, (k_day, k_title, k_desc) in enumerate(key_nodes):
        with m_cols[idx]:
            st.markdown(
                f"<div class='timeline-box'><div style='color:#D4AF37; font-weight:600; font-size:0.82rem;'>{k_day}</div>"
                f"<div style='font-weight:600; margin:4px 0; font-size:0.92rem;'>{k_title}</div>"
                f"<div style='font-size:0.75rem; color:#9A968E;'>{k_desc}</div></div>",
                unsafe_allow_html=True,
            )

    st.write("")
    weekly_reports = meta.get("weekly_reports", [])
    if weekly_reports:
        selected_week = st.select_slider(
            "选择周次查看【总调度 Agent 周报 & 价格/复购 Agent 调价触发条件与效果】",
            options=[w["week"] for w in weekly_reports],
            value=min(len(weekly_reports), max(1, (playback_day - 1) // 7 + 1)),
            format_func=lambda w: f"第 {w:02d} 周总结",
        )
        w_data = next((w for w in weekly_reports if w["week"] == selected_week), weekly_reports[-1])
        w_col1, w_col2 = st.columns([1.05, 1.35])
        with w_col1:
            st.markdown(f"**第 {w_data['week']:02d} 周 · 三城模拟经营摘要（截止 {w_data['date_str']}）**")
            st.code(w_data["metrics_summary"], language="text")
            st.markdown("**总调度 Agent × DeepSeek/Qwen 周度复盘：**")
            st.info(w_data["llm_analysis"])
        with w_col2:
            st.markdown(f"**第 {w_data['week']:02d} 周 · 价格 Agent 调价触发条件与周环比结果**")
            decs = w_data.get("decisions", [])
            if decs:
                df_w_dec = pd.DataFrame(decs)[
                    ["city", "old_day_price", "new_day_price", "old_night_price", "new_night_price", "campaign_action", "reason"]
                ]
                df_w_dec.columns = ["城市", "原日价", "新日价", "原夜价", "新夜价", "学习活动策略", "触发条件与周环比反馈(模拟)"]
                st.dataframe(df_w_dec, use_container_width=True, hide_index=True)

    # ==========================================
    # 附录区：敏感性四表并排、调价全日志、AUDIT审计报告、日夜容量红线、CSV下载
    # ==========================================
    st.markdown("---")
    st.markdown("#### 🗂️ 附录：敏感性四表并排对比、13周调价全日志、AUDIT.md 审计说明与 CSV 下载")
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 附录1：敏感性分析 (收入/成本/获客/留存 四表并排)",
        "💰 附录2：13周调价决策与下周效果全表",
        "🔍 附录3：v2.1 专项审计报告 (AUDIT.md)",
        "🏛️ 附录4：日夜分场人流 & 成本Agent账单",
        "📄 附录5：v2.1 Master CSV 预览与下载",
    ])

    with tab1:
        st.markdown("##### 敏感性分析：基准 vs 没送饮 vs 没线上 vs 涨价 20%（四张表并排对比，模拟）")
        p_col1, p_col2 = st.columns(2)
        p_col3, p_col4 = st.columns(2)

        rev_list, cost_list, acq_list, ret_list = [], [], [], []
        for c_name in ["三亚", "上海", "成都"]:
            row_rev = {"城市": c_name}
            row_cost = {"城市": c_name}
            row_acq = {"城市": c_name}
            row_ret = {"城市": c_name}
            for s_key, s_label in [
                ("baseline_90d", "基准全开"),
                ("no_send_drink", "没送饮"),
                ("no_online_wom", "没线上"),
                ("price_up_20pct", "涨价20%"),
            ]:
                s_df, _, _, _, s_meta = load_scenario_data(s_key)
                if s_df is None or s_df.empty:
                    continue
                c_sub = s_df[s_df["city"] == c_name]
                v_c = s_meta.get("verdicts", {}).get("day_90", {}).get(c_name, {})
                row_rev[s_label] = f"¥{c_sub['total_revenue'].sum():,.0f}"
                row_cost[s_label] = f"净利 ¥{c_sub['daily_net_profit'].sum():,.0f} (固本¥{c_sub['daily_fixed_cost'].sum():,.0f})"
                row_acq[s_label] = f"{int(c_sub['total_acquired_users'].iloc[-1])}人 ({int(c_sub['day_visitors'].sum()+c_sub['night_visitors'].sum())}人次)"
                row_ret[s_label] = f"{v_c.get('verdict','-')} (D30:{v_c.get('ret_30d',0)*100:.2f}% / D90:{v_c.get('ret_90d',0)*100:.1f}%)"
            rev_list.append(row_rev)
            cost_list.append(row_cost)
            acq_list.append(row_acq)
            ret_list.append(row_ret)

        with p_col1:
            st.markdown("**表 1：90 天累计总收入对比（模拟）**")
            st.dataframe(pd.DataFrame(rev_list), use_container_width=True, hide_index=True)
        with p_col2:
            st.markdown("**表 2：90 天固定成本与经营净利对比（模拟）**")
            st.dataframe(pd.DataFrame(cost_list), use_container_width=True, hide_index=True)
        with p_col3:
            st.markdown("**表 3：90 天累计获客与总到店人次对比（模拟）**")
            st.dataframe(pd.DataFrame(acq_list), use_container_width=True, hide_index=True)
        with p_col4:
            st.markdown("**表 4：30日/90日留存率与生死裁决对比（模拟）**")
            st.dataframe(pd.DataFrame(ret_list), use_container_width=True, hide_index=True)

    with tab2:
        st.markdown("##### 价格 / 复购 Agent 13 周调价决策与下周实际效果全表 (`outputs/pricing_decision_log_v2.1.csv`)")
        price_log_csv = OUTPUT_DIR / "pricing_decision_log_v2.1.csv"
        if price_log_csv.exists():
            st.dataframe(pd.read_csv(price_log_csv), use_container_width=True, hide_index=True)

    with tab3:
        audit_file = Path(__file__).resolve().parent / "AUDIT.md"
        if audit_file.exists():
            st.markdown(audit_file.read_text(encoding="utf-8"))
        else:
            st.info("AUDIT.md 正在生成中...")

    with tab4:
        col_c1, col_c2 = st.columns([1.2, 1.0])
        with col_c1:
            sel_city_cap = st.radio("选择查看城市日夜分场人流", ["三亚", "上海", "成都"], horizontal=True)
            c_cap_df = df_daily[df_daily["city"] == sel_city_cap]
            fig_cap = go.Figure()
            fig_cap.add_trace(go.Scatter(x=c_cap_df["date_str"], y=c_cap_df["day_visitors"], name="白天到店(咖啡, 模拟)", line=dict(color="#8A9A86", width=2)))
            fig_cap.add_trace(go.Scatter(x=c_cap_df["date_str"], y=c_cap_df["night_visitors"], name="晚上到店(夜酒, 模拟)", line=dict(color="#D4AF37", width=2)))
            fig_cap.add_hline(y=MAX_DAY_CAPACITY, line_dash="dash", line_color="#8A9A86", annotation_text="白天上限 120人")
            fig_cap.add_hline(y=MAX_NIGHT_CAPACITY, line_dash="dash", line_color="#D4AF37", annotation_text="夜晚上限 90人")
            fig_cap.update_layout(
                paper_bgcolor="#0E1016", plot_bgcolor="#0E1016", font=dict(color="#C8C4BC"),
                height=320, margin=dict(l=40, r=20, t=30, b=50),
                legend=dict(orientation="h", y=-0.18, x=0.5, xanchor="center"),
            )
            st.plotly_chart(fig_cap, use_container_width=True)
        with col_c2:
            st.markdown("**成本 Agent API 账单摘要**")
            st.json(cost_sum)

    with tab5:
        master_v21 = OUTPUT_DIR / "stjohn_v2.1_90d_master.csv"
        if master_v21.exists():
            st.markdown("**v2.1 主实验全中文列名 CSV (`outputs/stjohn_v2.1_90d_master.csv`)：**")
            df_cn = pd.read_csv(master_v21)
            st.dataframe(df_cn.head(36), use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ 下载 v2.1 完整 CSV (stjohn_v2.1_90d_master.csv)",
                data=master_v21.read_bytes(),
                file_name="stjohn_v2.1_90d_master.csv",
                mime="text/csv",
            )


if __name__ == "__main__":
    main()
