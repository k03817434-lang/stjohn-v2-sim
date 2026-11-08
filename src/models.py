"""
ST.JOHN v2 - 数据模型（User, CityState, GraphState）
神 / 派系不做 Agent，只做用户数据。
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, TypedDict, Annotated, Any
import operator


@dataclass
class UserProfile:
    user_id: str
    city: str
    age: int
    is_tourist: bool
    faction: str          # 天堂 / 人间 / 地狱
    god: str              # 九神之一
    domain: str           # 精神审美 / 肉身与关系 / 现实杠杆
    join_day: int         # 第几天首次到店 (1-90)
    last_visit_day: int   # 最近一次到店天数
    stay_end_day: int     # 若为游客，行程结束的天数；本地人为 999
    visit_count_day: int = 0
    visit_count_night: int = 0
    quiz_completed: bool = False
    unlocked_drink: str = ""
    satisfaction_score: float = 4.0  # 1-5分
    churned: bool = False
    has_posted_online: bool = False
    drinks_sent: int = 0
    radar_chats: int = 0


@dataclass
class OnlinePost:
    post_id: str
    city: str
    user_id: str
    created_day: int
    remaining_days: int = 7
    daily_conversion: float = 0.5 / 7.0  # 1条笔记7天内带来0.5个新到店
    content_snippet: str = ""


@dataclass
class DailyCityMetrics:
    day_index: int
    date_str: str
    city: str
    day_visitors: int
    night_visitors: int
    day_revenue: float
    night_revenue: float
    total_revenue: float
    retention_rate: float      # 留存率 (0.0 ~ 1.0)
    new_users: int             # 新增到店人数
    viral_k: float             # 裂变系数
    online_impressions: int    # 线上曝光量
    pricing_record: str        # 调价记录
    # 附加财务与运营细分指标（用于深度分析与回本计算）
    day_price: float = 26.0
    night_price: float = 120.0
    daily_fixed_cost: float = 0.0   # 每日房租+人工+投放分摊
    daily_cogs: float = 0.0         # 每日酒水咖啡原料变动成本 (按28%计)
    daily_net_profit: float = 0.0   # 当日经营净利润（不含装修）
    cumulative_profit: float = 0.0  # 累计经营净利润（不含装修）
    renovation_payback_pct: float = 0.0  # 装修回本进度百分比
    active_pool_size: int = 0       # 活跃用户池规模
    total_acquired_users: int = 0   # 累计获取用户总数 (上限1000)
    quiz_rate: float = 0.0
    send_drink_rate: float = 0.0
    radar_chat_rate: float = 0.0
    satisfaction_rate: float = 0.0
    event_tag: str = "日常"


def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    if not left:
        return dict(right) if right else {}
    if not right:
        return dict(left)
    merged = dict(left)
    merged.update(right)
    return merged


class SimulationState(TypedDict):
    """
    LangGraph 全局共享 State：
    包含天数、每个城市的资金/利润、人数、留存、裂变系数、各 Agent 状态与日志。
    """
    scenario_name: str
    total_days: int
    current_day: int
    current_date: str
    seed: int
    # 敏感性实验开关
    disable_send_drink: bool
    disable_online_word_of_mouth: bool
    force_price_up_20pct: bool
    enable_llm: bool

    # 总调度指令
    dispatch_instruction: str

    # 三个城市的实时状态字典 (key: "成都" | "上海" | "三亚")，支持三城 Agent 并行更新合并
    cities_runtime: Annotated[Dict[str, dict], merge_dicts]

    # 当日三个城市的产出指标 (key: "成都" | "上海" | "三亚")，支持三城 Agent 并行更新合并
    daily_outputs: Annotated[Dict[str, dict], merge_dicts]

    # 历史每日记录列表
    history_records: List[dict]

    # 每周总结与学习决策日志
    weekly_reports: List[dict]
    decision_logs: List[dict]

    # 抽样生成的真实用户发帖/对话样本
    user_dialogues_posts: List[dict]

    # 第 30 / 60 / 90 天生死判定与审计报告
    milestone_verdicts: Dict[str, dict]
    parameter_audit_log: List[str]
    final_summary_markdown: str

    # 评测与成本 Agent 状态
    validation_errors: List[str]
    cost_ledger: dict
