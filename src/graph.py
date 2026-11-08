"""
ST.JOHN v2 - LangGraph 状态图构建与执行引擎
严格遵循架构：
总调度 (master_dispatch)
  → 三个城市并行 (city_chengdu, city_shanghai, city_sanya)
  → 三个杠杆 (lever_social_viral → lever_online_wom → lever_pricing_repurchase)
  → 检查 Agent 校验 (evaluator_check)
  → 回总调度汇总、学习、写周报、判生死 (master_summarize_and_learn)
  → 循环 90 天 (或 7 天小测试)。
"""
from typing import List, Optional
from langgraph.graph import StateGraph, START, END

from src.config import GLOBAL_SEED
from src.models import SimulationState
from src.agents import (
    GLOBAL_LLM_CLIENT,
    init_city_runtime,
    master_dispatch_node,
    city_chengdu_node,
    city_shanghai_node,
    city_sanya_node,
    lever_social_viral_node,
    lever_online_wom_node,
    lever_pricing_repurchase_node,
    evaluator_check_node,
    master_summarize_and_learn_node,
)


def should_continue_loop(state: SimulationState) -> str:
    if state["current_day"] <= state["total_days"]:
        return "continue"
    return "end"


def build_stjohn_graph():
    builder = StateGraph(SimulationState)

    # 1. 总调度 Agent 发指令节点
    builder.add_node("master_dispatch", master_dispatch_node)

    # 2. 三个城市 Agent 并行节点（成都、上海、三亚）
    builder.add_node("city_chengdu", city_chengdu_node)
    builder.add_node("city_shanghai", city_shanghai_node)
    builder.add_node("city_sanya", city_sanya_node)

    # 3. 三个杠杆 Agent 节点（社交裂变、线上口碑、价格/复购）
    builder.add_node("lever_social_viral", lever_social_viral_node)
    builder.add_node("lever_online_wom", lever_online_wom_node)
    builder.add_node("lever_pricing_repurchase", lever_pricing_repurchase_node)

    # 4. 检查 Agent 节点（评测 Agent 数据红线校验）
    builder.add_node("evaluator_check", evaluator_check_node)

    # 5. 总调度 Agent 汇总、学习决策、写周报、30/60/90天判生死与审计（内置成本 Agent 记账）
    builder.add_node("master_summarize_and_learn", master_summarize_and_learn_node)

    # 构建拓扑边：总调度 → 三城并行 → 三杠杆 → 评测校验 → 总调度汇总 → 条件循环
    builder.add_edge(START, "master_dispatch")

    builder.add_edge("master_dispatch", "city_chengdu")
    builder.add_edge("master_dispatch", "city_shanghai")
    builder.add_edge("master_dispatch", "city_sanya")

    builder.add_edge("city_chengdu", "lever_social_viral")
    builder.add_edge("city_shanghai", "lever_social_viral")
    builder.add_edge("city_sanya", "lever_social_viral")

    builder.add_edge("lever_social_viral", "lever_online_wom")
    builder.add_edge("lever_online_wom", "lever_pricing_repurchase")
    builder.add_edge("lever_pricing_repurchase", "evaluator_check")
    builder.add_edge("evaluator_check", "master_summarize_and_learn")

    builder.add_conditional_edges(
        "master_summarize_and_learn",
        should_continue_loop,
        {
            "continue": "master_dispatch",
            "end": END,
        },
    )

    return builder.compile()


def run_simulation(
    scenario_name: str = "baseline_90d",
    cities: Optional[List[str]] = None,
    total_days: int = 90,
    seed: int = GLOBAL_SEED,
    disable_send_drink: bool = False,
    disable_online_word_of_mouth: bool = False,
    force_price_up_20pct: bool = False,
    enable_llm: bool = True,
    reset_cost_ledger: bool = True,
) -> SimulationState:
    if cities is None:
        cities = ["成都", "上海", "三亚"]

    if reset_cost_ledger:
        GLOBAL_LLM_CLIENT.reset_ledger()

    cities_runtime = {
        c: init_city_runtime(c, seed=seed, force_price_up_20pct=force_price_up_20pct)
        for c in cities
    }

    initial_state: SimulationState = {
        "scenario_name": scenario_name,
        "total_days": total_days,
        "current_day": 1,
        "current_date": "2026-12-20",
        "seed": seed,
        "disable_send_drink": disable_send_drink,
        "disable_online_word_of_mouth": disable_online_word_of_mouth,
        "force_price_up_20pct": force_price_up_20pct,
        "enable_llm": enable_llm,
        "dispatch_instruction": "",
        "cities_runtime": cities_runtime,
        "daily_outputs": {},
        "history_records": [],
        "weekly_reports": [],
        "decision_logs": [],
        "user_dialogues_posts": [],
        "milestone_verdicts": {},
        "parameter_audit_log": [],
        "final_summary_markdown": "",
        "validation_errors": [],
        "cost_ledger": {},
    }

    graph = build_stjohn_graph()
    # 90天循环 × 每步约8个节点 ≈ 720步，设置 recursion_limit=2000
    final_state = graph.invoke(initial_state, config={"recursion_limit": 2500})
    return final_state
