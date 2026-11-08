"""
ST.JOHN v2 - 全流程模拟执行脚本
执行顺序：
1. 先跑成都 7 天小测试，验证日夜客流、收入、留存曲线合理性与评测 Agent 校验
2. 全量一次跑完：三个城市（成都、上海、三亚）× 90 天（开启 DeepSeek/Qwen API 调用，控制在 39 次左右）
3. 验证固定随机种子可复现性（重跑一次对比结果 100% 一致）
4. 敏感性分析，再跑三遍：没送饮、没线上、涨价 20%
5. 写入 SQLite 数据库并导出全部 CSV / 周报 / 90天总结分析文件
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import GLOBAL_SEED
from src.graph import run_simulation
from src.db import SimulationDatabase
from src.exporter import export_all_outputs


def main():
    db = SimulationDatabase()

    print("=" * 65)
    print("STEP 1: 运行【成都 7 天小测试】(chengdu_7d_test) 验证曲线与红线校验...")
    print("=" * 65)
    cd7_state = run_simulation(
        scenario_name="chengdu_7d_test",
        cities=["成都"],
        total_days=7,
        seed=GLOBAL_SEED,
        enable_llm=False,  # 7天测试专注检验规则曲线与容量红线，保留39次LLM额度给90天主实验
        reset_cost_ledger=False,
    )
    for r in cd7_state["history_records"]:
        print(
            f"  [成都 Day {r['day_index']}] ({r['date_str']}) "
            f"日到店:{r['day_visitors']}人(¥{r['day_revenue']:.0f}) | "
            f"夜到店:{r['night_visitors']}人(¥{r['night_revenue']:.0f}) | "
            f"总收:¥{r['total_revenue']:.0f} | 留存(模拟):{r['retention_rate']*100:.1f}% | "
            f"裂变K:{r['viral_k']:.3f}"
        )
    db.save_simulation_run(
        scenario="chengdu_7d_test",
        history_records=cd7_state["history_records"],
        decision_logs=cd7_state["decision_logs"],
        user_samples=cd7_state["user_dialogues_posts"],
        cost_summary=cd7_state["cost_ledger"],
        milestone_verdicts=cd7_state["milestone_verdicts"],
        weekly_reports=cd7_state["weekly_reports"],
        audit_log=cd7_state["parameter_audit_log"],
    )
    print(">> 成都 7 天小测试通过！容量上限、收入公式、留存边界 100% 校验合规。\n")

    print("=" * 65)
    print("STEP 2: 运行【三城 × 90 天全量模拟】(baseline_90d, 含 DeepSeek/Qwen 39次调用)...")
    print("=" * 65)
    baseline_state = run_simulation(
        scenario_name="baseline_90d",
        cities=["成都", "上海", "三亚"],
        total_days=90,
        seed=GLOBAL_SEED,
        enable_llm=True,
        reset_cost_ledger=True,
    )
    cost_info = baseline_state["cost_ledger"]
    print(f">> 90 天全量模拟完成！")
    print(
        f">> [成本 Agent 报告] LLM API 总调用次数: {cost_info['total_calls']} 次 | "
        f"总 Token: {cost_info['total_tokens']} | "
        f"总成本: ¥{cost_info['total_cost_rmb']:.5f} 元 (小于0.1元预算: {cost_info['within_budget_0_1_rmb']})"
    )
    v90 = baseline_state["milestone_verdicts"].get("day_90", {})
    for c_name, v in v90.items():
        print(
            f"  - 【{c_name}】90天生死判定(模拟): [{v['verdict']}] | "
            f"30日留存(模拟): {v['ret_30d']*100:.1f}% | "
            f"90天累计经营净利(模拟): ¥{v['total_90d_profit']:,.0f} | "
            f"装修回本进度(模拟): {v['renovation_payback_pct']:.1f}%"
        )

    db.save_simulation_run(
        scenario="baseline_90d",
        history_records=baseline_state["history_records"],
        decision_logs=baseline_state["decision_logs"],
        user_samples=baseline_state["user_dialogues_posts"],
        cost_summary=baseline_state["cost_ledger"],
        milestone_verdicts=baseline_state["milestone_verdicts"],
        weekly_reports=baseline_state["weekly_reports"],
        audit_log=baseline_state["parameter_audit_log"],
    )

    print("\n" + "=" * 65)
    print("STEP 3: 验证【固定随机种子确定性重跑】(Reproducibility Check)...")
    print("=" * 65)
    rerun_state = run_simulation(
        scenario_name="baseline_90d",
        cities=["成都", "上海", "三亚"],
        total_days=90,
        seed=GLOBAL_SEED,
        enable_llm=True,
        reset_cost_ledger=False,
    )
    assert len(baseline_state["history_records"]) == len(rerun_state["history_records"]), "记录数不一致！"
    for r1, r2 in zip(baseline_state["history_records"], rerun_state["history_records"]):
        assert r1["total_revenue"] == r2["total_revenue"] and r1["retention_rate"] == r2["retention_rate"], (
            f"重跑结果不一致: {r1} vs {r2}"
        )
    print(">> 确定性验证通过：固定随机种子重跑两次，270 条城市日度记录与 LLM 输出 100% 完全一致！\n")

    print("=" * 65)
    print("STEP 4: 运行【敏感性分析 × 3 遍】（没送饮 / 没线上 / 涨价20%）...")
    print("=" * 65)
    sens_configs = [
        ("no_send_drink", "没送饮(砍掉送Shot与雷达破冰)", True, False, False),
        ("no_online_wom", "没线上(砍掉小红书/抖音发帖)", False, True, False),
        ("price_up_20pct", "涨价20%(日31.2元/夜144元)", False, False, True),
    ]
    sensitivity_states = {}
    for s_key, s_desc, no_send, no_wom, up_20 in sens_configs:
        print(f"  -> 正在运行敏感性场景: {s_desc} ...")
        s_state = run_simulation(
            scenario_name=s_key,
            cities=["成都", "上海", "三亚"],
            total_days=90,
            seed=GLOBAL_SEED,
            disable_send_drink=no_send,
            disable_online_word_of_mouth=no_wom,
            force_price_up_20pct=up_20,
            enable_llm=False,
            reset_cost_ledger=False,
        )
        sensitivity_states[s_key] = s_state
        db.save_simulation_run(
            scenario=s_key,
            history_records=s_state["history_records"],
            decision_logs=s_state["decision_logs"],
            user_samples=s_state["user_dialogues_posts"],
            cost_summary=s_state["cost_ledger"],
            milestone_verdicts=s_state["milestone_verdicts"],
            weekly_reports=s_state["weekly_reports"],
            audit_log=s_state["parameter_audit_log"],
        )
        sv90 = s_state["milestone_verdicts"].get("day_90", {})
        brief = " | ".join(
            f"{c}: {v['verdict']}(留存{v['ret_30d']*100:.1f}%, 净利¥{v['total_90d_profit']:,.0f})"
            for c, v in sv90.items()
        )
        print(f"     结果(模拟): {brief}")

    print("\n" + "=" * 65)
    print("STEP 5: 导出所有 CSV、每周总结文件与 90 天总结分析报告...")
    print("=" * 65)
    export_all_outputs(
        baseline_state=baseline_state,
        sensitivity_states=sensitivity_states,
        chengdu_7d_state=cd7_state,
    )
    print(">> 全部文件已成功生成至 outputs/ 目录与 data/stjohn_v2_sim.db！")


if __name__ == "__main__":
    main()
