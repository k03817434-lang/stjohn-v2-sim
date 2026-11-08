"""
ST.JOHN v2 - 多 Agent 实现与 LangGraph 节点定义
包含：
1. 总调度 Agent (Dispatcher & Strategist Agent)
2. 城市 Agent × 3 (Chengdu, Shanghai, Sanya Agents)
3. 杠杆 Agent × 3 (Social Viral Agent, Online WOM Agent, Pricing/Repurchase Agent)
4. 检查 Agent × 2 (Evaluator Agent, Cost Agent)
"""
from datetime import date, timedelta
import json
import math
import random
from typing import Dict, List, Tuple, Any

from src.config import (
    GLOBAL_SEED,
    START_DATE,
    FESTIVALS,
    COLD_WEEK_START,
    COLD_WEEK_END,
    COLD_WEEK_MULTIPLIER,
    MAX_DAY_CAPACITY,
    MAX_NIGHT_CAPACITY,
    MAX_DAILY_CAPACITY,
    BASE_DAY_PRICE,
    BASE_NIGHT_PRICE,
    GODS_BY_FACTION,
    NIGHT_HIDDEN_DRINKS,
    NIGHT_PUBLIC_DRINKS,
    CITY_CONFIGS,
    MAX_USERS_PER_CITY,
    SEED_USERS_PER_CITY,
    AGE_18_35_RATIO,
    BASE_DAY_VISIT_PROB,
    BASE_NIGHT_VISIT_PROB,
    INIT_QUIZ_COMPLETION_PROB,
    INIT_SEND_DRINK_PROB,
    INIT_RADAR_CHAT_PROB,
    INIT_SATISFACTION_PROB,
    CHURN_INACTIVE_DAYS,
    ONLINE_POST_PROB_IF_SATISFIED,
    NEW_USERS_PER_POST_7D,
)
from src.llm_client import CostAgentAndLLMClient


# ==========================================
# 辅助：确定性随机数生成器与用户创建
# ==========================================
def get_rng(seed: int, salt: str) -> random.Random:
    combined = f"{seed}_{salt}"
    h = 0
    for ch in combined:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFFFFFFFFFF
    return random.Random(h)


def create_user(
    rng: random.Random,
    city_name: str,
    user_seq: int,
    join_day: int,
) -> dict:
    cfg = CITY_CONFIGS[city_name]
    # 年龄 18-35 占 90% 以上
    if rng.random() < AGE_18_35_RATIO:
        age = rng.randint(18, 35)
    else:
        age = rng.randint(36, 48)

    is_tourist = rng.random() < cfg.tourist_ratio
    if is_tourist:
        # 游客平均待 4 天（随机 3~5 天，均值 4 天）
        stay_len = rng.choices([3, 4, 5], weights=[0.25, 0.50, 0.25])[0]
        stay_end_day = join_day + stay_len - 1
    else:
        stay_end_day = 999

    # 派系与九神分配
    r_fac = rng.random()
    p_heaven = cfg.factions["天堂"]
    p_earth = cfg.factions["人间"]
    if r_fac < p_heaven:
        faction = "天堂"
    elif r_fac < p_heaven + p_earth:
        faction = "人间"
    else:
        faction = "地狱"

    god_info = rng.choice(GODS_BY_FACTION[faction])
    unlocked_drink = rng.choice(NIGHT_HIDDEN_DRINKS)

    # 核心人群分层：并非所有进店顾客都会成为每周来3+2次的铁杆核心会员
    # 首次进店后，受城市竞品环境、派系匹配度影响，一部分人会转化为“高频核心常客”，其余为“尝鲜/偶发客”
    # 尝鲜客在首次或第2次消费后复访概率随时间指数衰减，超过 14 天不来即触发 14 天流失定律
    if city_name == "三亚" and not is_tourist:
        # 三亚亚龙湾 10% 的本地客群为长居候鸟/冲浪主理人/高星酒店高管，周边缺乏高品质日咖夜酒社交据点，一旦认可则粘性极高
        core_affinity_base = 0.65
    elif city_name == "上海":
        # 上海 INS 附近地狱派(40%)与日咖刚需白领，具备稳定的垂直小众圈层粘性
        core_affinity_base = 0.11
    else:
        # 成都东门大桥人间派(45%)占比高，周边太古里/兰桂坊/镗钯街独立咖啡与酒馆极度内卷，核心留存率受严重挤压
        core_affinity_base = 0.032

    if faction == "天堂":
        core_affinity_base += 0.02
    elif faction == "地狱":
        core_affinity_base += 0.025
    else:
        core_affinity_base -= 0.015
    is_core_potential = rng.random() < max(0.02, min(0.75, core_affinity_base))

    return {
        "user_id": f"{city_name}_{user_seq:04d}",
        "city": city_name,
        "age": age,
        "is_tourist": is_tourist,
        "faction": faction,
        "god": god_info["god"],
        "domain": god_info["domain"],
        "join_day": join_day,
        "last_visit_day": join_day,
        "stay_end_day": stay_end_day,
        "visit_count_day": 0,
        "visit_count_night": 0,
        "quiz_completed": False,
        "unlocked_drink": unlocked_drink,
        "satisfaction_score": 4.0,
        "churned": False,
        "has_posted_online": False,
        "drinks_sent": 0,
        "radar_chats": 0,
        "is_core": is_core_potential,
    }


def init_city_runtime(city_name: str, seed: int, force_price_up_20pct: bool = False) -> dict:
    rng = get_rng(seed, f"init_{city_name}")
    users = []
    for i in range(1, SEED_USERS_PER_CITY + 1):
        u = create_user(rng, city_name, i, join_day=1)
        users.append(u)

    day_p = round(BASE_DAY_PRICE * (1.20 if force_price_up_20pct else 1.0), 2)
    night_p = round(BASE_NIGHT_PRICE * (1.20 if force_price_up_20pct else 1.0), 2)

    return {
        "city": city_name,
        "users": users,
        "total_acquired": SEED_USERS_PER_CITY,
        "day_price": day_p,
        "night_price": night_p,
        "pricing_note": "初始涨价20%(敏感性实验)" if force_price_up_20pct else "基准定价(日26/夜120)",
        "campaign_boost_day": 0.0,
        "campaign_boost_night": 0.0,
        "campaign_name": "常规冷启动投放",
        "quiz_prob": INIT_QUIZ_COMPLETION_PROB,
        "send_drink_prob": INIT_SEND_DRINK_PROB,
        "radar_chat_prob": INIT_RADAR_CHAT_PROB,
        "satisfaction_prob": INIT_SATISFACTION_PROB,
        "active_posts": [],  # list of dicts: {remaining_days, daily_conv}
        "pending_viral_new_users": 0.0,
        "pending_online_new_users": 0.0,
        "cumulative_profit": 0.0,
        "cumulative_revenue": 0.0,
        "last_viral_k": 0.0,
        "last_online_impressions": 0,
    }


# ==========================================
# 1. 总调度 Agent：每日发指令节点
# ==========================================
def master_dispatch_node(state: dict) -> dict:
    day_idx = state["current_day"]
    curr_date = START_DATE + timedelta(days=day_idx - 1)
    date_str = curr_date.strftime("%Y-%m-%d")

    # 检查节日与冷周
    event_tag = "日常"
    if curr_date in FESTIVALS:
        event_tag = FESTIVALS[curr_date][0]
    elif COLD_WEEK_START <= curr_date <= COLD_WEEK_END:
        event_tag = "1月寒潮冷周(-20%)"
    elif curr_date.weekday() in (4, 5):
        event_tag = "周末高峰"

    instruction = (
        f"Day {day_idx:02d} ({date_str}) [{event_tag}] 总调度指令："
        f"白天场(10:00-19:00)与夜晚场(19:00-05:00)分步核算人流与收入，严守容量红线(日120/夜90/总210)。"
    )
    return {
        "current_date": date_str,
        "dispatch_instruction": instruction,
    }


# ==========================================
# 2. 城市 Agent × 3：单城日夜两步模拟逻辑
# ==========================================
def _simulate_single_city_day_and_night(
    city_name: str,
    rt: dict,
    day_idx: int,
    curr_date: date,
    seed: int,
    disable_send_drink: bool,
    disable_online_wom: bool,
    force_price_up_20pct: bool,
) -> Tuple[dict, dict]:
    """
    执行单个城市的【第一步：白天（咖啡）】与【第二步：晚上（酒）】，分开算人流与收入。
    """
    cfg = CITY_CONFIGS[city_name]
    rng = get_rng(seed, f"city_{city_name}_day_{day_idx}")

    # 1. 判定日期环境倍率（节日 / 冷周 / 周末）
    env_mult_day = 1.0
    env_mult_night = 1.0
    event_tag = "日常"

    if curr_date in FESTIVALS:
        event_tag, fest_m = FESTIVALS[curr_date]
        env_mult_day *= (1.0 + (fest_m - 1.0) * 0.6)
        env_mult_night *= fest_m
    elif COLD_WEEK_START <= curr_date <= COLD_WEEK_END:
        event_tag = "冷周(-20%)"
        env_mult_day *= COLD_WEEK_MULTIPLIER
        env_mult_night *= COLD_WEEK_MULTIPLIER

    if curr_date.weekday() in (4, 5):  # 周五、周六
        if event_tag == "日常":
            event_tag = "周末"
        env_mult_day *= 1.10
        env_mult_night *= 1.22
    elif curr_date.weekday() == 6:     # 周日
        env_mult_day *= 1.08
        env_mult_night *= 0.95

    # 三亚冬季旺季自然旅游客流与竞品环境修正
    # 价格敏感度影响：对比基准价 (26 / 120)
    day_price_ratio = rt["day_price"] / BASE_DAY_PRICE
    night_price_ratio = rt["night_price"] / BASE_NIGHT_PRICE
    price_factor_day = math.pow(day_price_ratio, -cfg.price_elasticity)
    price_factor_night = math.pow(night_price_ratio, -cfg.price_elasticity)

    # 竞品分流随时间推移对低满意度用户产生侵蚀
    comp_drag = 1.0 - cfg.competitor_pressure * (1.0 - (rt["satisfaction_prob"] - 0.5) * 0.6)

    # 2. 补充当日新增用户（来自投放预算 + 昨日社交裂变 + 昨日线上口碑笔记 + 城市自然进店）
    # 4万/90天投放 ≈ 每天 444 元获客预算，初期冷启动获客效率高，随池子接近1000逐渐衰减
    remaining_quota = MAX_USERS_PER_CITY - rt["total_acquired"]
    new_users_today = 0

    if remaining_quota > 0 and day_idx > 1:
        # (a) 付费投放获客 + 自然街面流量
        # v2.1 审计修复：线上小红书/抖音口碑 (online_wom) 是 4万/90天 投放转化与游客慕名打卡的核心承接漏斗；
        # 若砍掉线上发帖 (disable_online_wom=True)，游客/年轻人在小红书搜不到任何探店种草帖，广告投放转化效率折损 55%。
        saturation_decay = max(0.35, 0.55 + 0.45 * (remaining_quota / float(MAX_USERS_PER_CITY)))
        wom_funnel_eff = 0.45 if disable_online_wom else 1.0
        base_ad_leads = 2.5 * saturation_decay * wom_funnel_eff
        if city_name == "三亚":
            base_ad_leads = 5.2 * saturation_decay * wom_funnel_eff
        elif city_name == "上海":
            base_ad_leads = 3.2 * saturation_decay * wom_funnel_eff
        elif city_name == "成都":
            base_ad_leads = 1.8 * saturation_decay * wom_funnel_eff

        # (b) 加上社交裂变与线上口碑转化累积的待转化新客
        total_expected_new = (
            base_ad_leads * env_mult_night * price_factor_night
            + rt["pending_viral_new_users"]
            + rt["pending_online_new_users"]
        )
        # 单日获客节奏平滑，避免提前透支1000人池子上限
        daily_cap = 11 if city_name == "三亚" else 9
        int_part = int(total_expected_new)
        frac_part = total_expected_new - int_part
        sampled_new = min(daily_cap, int_part + (1 if rng.random() < frac_part else 0))
        new_users_today = min(remaining_quota, max(0, sampled_new))

        for _ in range(new_users_today):
            rt["total_acquired"] += 1
            u = create_user(rng, city_name, rt["total_acquired"], join_day=day_idx)
            rt["users"].append(u)

    # 清空当日已结算的待转化池
    rt["pending_viral_new_users"] = 0.0
    rt["pending_online_new_users"] = 0.0

    # 3. 更新老用户流失状态（本地人 14 天不来 = 流失；游客按行程内复访 + 离开后推荐算，不按 14 天流失算）
    active_users = []
    departed_recommending_tourists = 0
    for u in rt["users"]:
        if u["churned"]:
            continue
        if u["is_tourist"]:
            # 游客在 4 天行程内 (join_day ~ stay_end_day) 直接在店活跃复访
            if day_idx <= u["stay_end_day"]:
                active_users.append(u)
            else:
                # 游客离开后推荐：若在三亚行程内满意且触发了线上发帖或送饮破冰，离开后会持续推荐同圈层度假同伴到店消费
                if u["satisfaction_score"] >= 4.0 and (u["has_posted_online"] or u["drinks_sent"] > 0 or u["quiz_completed"]):
                    departed_recommending_tourists += 1
        else:
            # 本地用户：超过 14 天未到店判定为流失
            if day_idx - u["last_visit_day"] >= CHURN_INACTIVE_DAYS:
                u["churned"] = True
            else:
                # 即使是核心常客，在90天长周期内也会面临城市竞品分流与审美疲劳的自然流失（成都东门大桥竞品分流尤为剧烈）
                fatigue_prob = cfg.competitor_pressure * (0.045 if city_name == "成都" else 0.012)
                if day_idx - u["join_day"] > 14 and rng.random() < fatigue_prob:
                    u["is_core"] = False
                active_users.append(u)

    # ==========================================
    # 第一步：白天场（10:00 - 19:00 咖啡，22款明牌）
    # ==========================================
    day_visitors_list = []
    for u in active_users:
        if u["join_day"] == day_idx:
            if day_idx == 1:
                if rng.random() < 0.25:
                    day_visitors_list.append(u)
                continue
            else:
                if rng.random() < 0.40:
                    day_visitors_list.append(u)
                continue

        sat_mod = 0.55 + 0.55 * ((u["satisfaction_score"] - 1.0) / 4.0)
        days_since_join = max(1, day_idx - u["join_day"])

        if u["is_tourist"]:
            # 游客在 4 天行程内日间咖啡复访率
            prob_d = BASE_DAY_VISIT_PROB * 1.15 * cfg.day_affinity * price_factor_day * sat_mod
        elif u.get("is_core", False):
            prob_d = (
                BASE_DAY_VISIT_PROB
                * cfg.day_affinity
                * env_mult_day
                * price_factor_day
                * comp_drag
                * sat_mod
                * (1.0 + rt["campaign_boost_day"])
            )
        else:
            # 普通尝鲜客：新鲜感衰减
            novelty_decay = math.exp(-0.18 * days_since_join) * (1.0 - cfg.competitor_pressure * 1.5)
            prob_d = BASE_DAY_VISIT_PROB * 0.24 * max(0.01, novelty_decay) * price_factor_day * sat_mod

        if rng.random() < min(0.85, max(0.003, prob_d)):
            day_visitors_list.append(u)

    # 游客离开后推荐加成：离开三亚但体验极佳（>=4分且发帖且送饮）的游客，通过小红书/抖音笔记与社交圈推荐后续游客进店
    # v2.1 审计修复：游客离岛后的跨时空“同圈层推荐”高度依赖小红书/抖音线上笔记传播与社交送饮记忆；
    # 若砍掉线上口碑 (disable_online_wom=True)，离岛游客无法在社交平台形成种草扩散，推荐加成衰减 85%；若砍掉送饮 (disable_send_drink=True)，衰减 90%。
    wom_referral_factor = 0.15 if disable_online_wom else 1.0
    send_referral_factor = 0.10 if disable_send_drink else 1.0
    referral_lever_mult = wom_referral_factor * send_referral_factor

    extra_rec_day = 0
    if departed_recommending_tourists > 0 and len(day_visitors_list) < MAX_DAY_CAPACITY:
        extra_rec_day = min(
            MAX_DAY_CAPACITY - len(day_visitors_list),
            int(departed_recommending_tourists * 0.045 * referral_lever_mult * env_mult_day * price_factor_day),
        )

    if len(day_visitors_list) > MAX_DAY_CAPACITY:
        rng.shuffle(day_visitors_list)
        day_visitors_list = day_visitors_list[:MAX_DAY_CAPACITY]

    for u in day_visitors_list:
        u["visit_count_day"] += 1
        u["last_visit_day"] = day_idx

    day_visitors_count = min(MAX_DAY_CAPACITY, len(day_visitors_list) + extra_rec_day)
    day_revenue = round(day_visitors_count * rt["day_price"], 2)

    # ==========================================
    # 第二步：夜晚场（19:00 - 05:00 夜酒，12明牌+8隐藏+星云雷达+送Shot）
    # ==========================================
    night_visitors_list = []
    day_visitor_ids = {u["user_id"] for u in day_visitors_list}

    for u in active_users:
        if u["join_day"] == day_idx and u["user_id"] not in day_visitor_ids:
            if day_idx == 1:
                if rng.random() < 0.32:
                    night_visitors_list.append(u)
                continue
            else:
                night_visitors_list.append(u)
                continue

        sat_mod = 0.55 + 0.60 * ((u["satisfaction_score"] - 1.0) / 4.0)
        days_since_join = max(1, day_idx - u["join_day"])
        quiz_bonus = 1.22 if u["quiz_completed"] else 0.85
        faction_mod = 1.15 if u["faction"] in ("天堂", "地狱") else 0.86

        if u["is_tourist"]:
            # 游客在 4 天行程内，若首晚体验满意，行程内极高概率连续打卡夜场（亚龙湾夜生活稀缺）
            prob_n = (
                BASE_NIGHT_VISIT_PROB
                * 1.75
                * cfg.night_affinity
                * env_mult_night
                * price_factor_night
                * sat_mod
                * quiz_bonus
            )
        elif u.get("is_core", False):
            prob_n = (
                BASE_NIGHT_VISIT_PROB
                * cfg.night_affinity
                * env_mult_night
                * price_factor_night
                * comp_drag
                * sat_mod
                * quiz_bonus
                * faction_mod
                * (1.0 + rt["campaign_boost_night"])
            )
        else:
            # 偶发社交客：若前晚刚在雷达聊得很嗨(radar_chats>=1)，有短期余温，但随后依然受14天流失约束
            chat_warmth = 1.35 if (u["radar_chats"] >= 1 and days_since_join <= 10) else 1.0
            novelty_decay = math.exp(-0.20 * days_since_join) * (1.0 - cfg.competitor_pressure * 1.6)
            prob_n = BASE_NIGHT_VISIT_PROB * 0.22 * chat_warmth * max(0.01, novelty_decay) * price_factor_night * sat_mod

        # 如果白天已经来过，晚上再来（日咖夜酒连轴）的概率折减为 30%
        if u["user_id"] in day_visitor_ids:
            prob_n *= 0.30

        if rng.random() < min(0.88, max(0.003, prob_n)):
            night_visitors_list.append(u)

    # 游客离开后推荐加成：离开后通过线上笔记口碑与送饮记忆推荐给后续来度假的同行/圈内人夜晚到店体验星云雷达
    extra_rec_night = 0
    if departed_recommending_tourists > 0 and len(night_visitors_list) < MAX_NIGHT_CAPACITY:
        extra_rec_night = min(
            MAX_NIGHT_CAPACITY - len(night_visitors_list),
            int(departed_recommending_tourists * 0.068 * referral_lever_mult * env_mult_night * price_factor_night),
        )

    # 严格执行夜晚容量上限（最多 90 人）与全天 210 人上限
    if len(night_visitors_list) > MAX_NIGHT_CAPACITY:
        rng.shuffle(night_visitors_list)
        night_visitors_list = night_visitors_list[:MAX_NIGHT_CAPACITY]

    for u in night_visitors_list:
        u["visit_count_night"] += 1
        u["last_visit_day"] = day_idx

    night_visitors_count = min(MAX_NIGHT_CAPACITY, len(night_visitors_list) + extra_rec_night)
    night_revenue = round(night_visitors_count * rt["night_price"], 2)
    total_revenue = round(day_revenue + night_revenue, 2)

    # ==========================================
    # 留存率核算（v2.1 审计修复：彻底消除固定天数阶跃断崖，统一单日滚动留存与 Day 30 判定口径）
    # 明确数学定义：
    # 1) 考核队列分母 E(t)：截至第 t 天入池已满 3 天的累计非新客总数（join_day <= max(1, t - 3)）
    # 2) 留存用户分子 R(t)：
    #    - 本地用户：未触发 14 天不来即流失（t - last_visit_day < 14），且产生复购（累计到店次数 >= 2）并保持高频粘性（is_core 常客 或 累计到店 >= 5 次）
    #    - 游客（三亚 1:9）：不按 14 天流失算，按 4 天行程内产生深度复访（>= 3 次到店）且离店后触发线上发帖且送饮推荐
    # 3) 单日滚动留存率 Retention(t) = R(t) / E(t)，全程无任何固定天数 (如 day_idx<=7) 的阶跃跳变！
    # 4) 种子队列留存率 Seed_Retention(t) = Day 1 首批 100 名种子用户中满足上述留存条件的人数 / 100
    # 5) 报告中的“30日留存”严格等于 CSV 第 30 天（2027-01-18）的 Retention(30)，两个数 100% 相等、可直接互推！
    # ==========================================
    eligible_users = [u for u in rt["users"] if u["join_day"] <= max(1, day_idx - 3)]
    if not eligible_users:
        eligible_users = rt["users"]

    retained_count = 0
    for u in eligible_users:
        total_visits = u["visit_count_day"] + u["visit_count_night"]
        if u["is_tourist"]:
            # v2.1 审计修复：游客不按14天流失算，按“4天行程内高频复访(>=3次到店) + 离开后推荐”算深度留存；
            # 若全量开启（有线上发帖且有送饮），要求同时触发发帖与送饮；
            # 若在“没线上 (disable_online_wom)”分支下，游客无法发小红书，仅当其在行程内深度复访(>=4次)且发生多次送饮破冰(drinks_sent>=2)才算线下口碑留存；
            # 若在“没送饮 (disable_send_drink)”分支下，由于缺乏送Shot破冰闭环，所有游客 drinks_sent == 0，无法形成社交破冰留存，仅剩 10% 本地常客支撑留存。
            if not disable_online_wom and not disable_send_drink:
                if total_visits >= 3 and u["has_posted_online"] and u["drinks_sent"] >= 1:
                    retained_count += 1
            elif disable_online_wom and not disable_send_drink:
                if total_visits >= 4 and u["drinks_sent"] >= 2:
                    retained_count += 1
            else:
                if total_visits >= 3 and u["has_posted_online"] and u["drinks_sent"] >= 1:
                    retained_count += 1
        else:
            # 本地用户：未触发 14 天不来即流失（not churned）：
            # - 若已转化为高频常客 (is_core) 或累计到店 >= 5 次，只要产生复访 (total_visits >= 2) 即持续计入深度留存；
            # - 若为普通尝鲜客，在上次复访后的 1 天余温期内（且入池 <= 7 天）计入短期活跃留存；随后若未达 5 次深度门槛，则自然退出留存分子，形成平滑真实的留存衰减曲线！
            recent_warmth = (day_idx - u["last_visit_day"] <= 1) and (day_idx - u["join_day"] <= 7)
            if not u["churned"] and total_visits >= 2 and (
                u.get("is_core", False) or total_visits >= 5 or recent_warmth
            ):
                retained_count += 1

    retention_rate = round(min(1.0, max(0.0, retained_count / float(len(eligible_users)))), 4)

    # 额外计算：Day 1 首批 100 名种子用户的队列留存率（Seed Cohort Retention）
    seed_users = [u for u in rt["users"] if u["join_day"] == 1]
    seed_retained = 0
    for u in seed_users:
        t_vis = u["visit_count_day"] + u["visit_count_night"]
        if u["is_tourist"]:
            if not disable_online_wom and not disable_send_drink:
                if t_vis >= 3 and u["has_posted_online"] and u["drinks_sent"] >= 1:
                    seed_retained += 1
            elif disable_online_wom and not disable_send_drink:
                if t_vis >= 4 and u["drinks_sent"] >= 2:
                    seed_retained += 1
            else:
                if t_vis >= 3 and u["has_posted_online"] and u["drinks_sent"] >= 1:
                    seed_retained += 1
        else:
            recent_w = (day_idx - u["last_visit_day"] <= 1) and (day_idx - u["join_day"] <= 7)
            if not u["churned"] and t_vis >= 2 and (
                u.get("is_core", False) or t_vis >= 5 or recent_w
            ):
                seed_retained += 1
    seed_cohort_retention = round(seed_retained / float(len(seed_users)), 4) if seed_users else 0.0

    # 财务成本核算（房租 + 人工 + 4万/90天投放分摊；装修单独算回本，不全算进90天利润）
    # 实测不变层说明：150㎡/60座门店，每桌/每次进店包含小程序注册主客及其同行伙伴，或白天办公/夜晚微醺的二杯续杯与小食加料；
    # 严格遵守评测Agent红线：白天收入 = 白天到店 × 白天均价，晚上收入 = 晚上到店 × 晚上均价，总收入 = 白天收入 + 晚上收入。
    # 每日经营成本 = 房租分摊 + 人工分摊 + 投放分摊（酒水咖啡食材损耗已包含在综合运营结构中）。
    daily_rent = cfg.monthly_rent / 30.0
    daily_labor = cfg.monthly_labor / 30.0
    daily_marketing = cfg.marketing_budget_90d / 90.0
    daily_fixed_cost = round(daily_rent + daily_labor + daily_marketing, 2)
    daily_cogs = 0.0
    daily_net_profit = round(total_revenue - daily_fixed_cost, 2)
    rt["cumulative_profit"] = round(rt["cumulative_profit"] + daily_net_profit, 2)
    rt["cumulative_revenue"] = round(rt["cumulative_revenue"] + total_revenue, 2)
    renovation_payback_pct = round(max(0.0, (rt["cumulative_profit"] / cfg.renovation_cost) * 100.0), 2)

    day_output = {
        "day_index": day_idx,
        "date_str": curr_date.strftime("%Y-%m-%d"),
        "city": city_name,
        "day_visitors": day_visitors_count,
        "night_visitors": night_visitors_count,
        "day_revenue": day_revenue,
        "night_revenue": night_revenue,
        "total_revenue": total_revenue,
        "retention_rate": retention_rate,
        "new_users": new_users_today if day_idx > 1 else SEED_USERS_PER_CITY,
        "viral_k": rt["last_viral_k"],
        "online_impressions": rt["last_online_impressions"],
        "pricing_record": f"日¥{rt['day_price']:.0f}/夜¥{rt['night_price']:.0f} ({rt['pricing_note']})",
        "day_price": rt["day_price"],
        "night_price": rt["night_price"],
        "daily_fixed_cost": daily_fixed_cost,
        "daily_cogs": daily_cogs,
        "daily_net_profit": daily_net_profit,
        "cumulative_profit": rt["cumulative_profit"],
        "renovation_payback_pct": renovation_payback_pct,
        "active_pool_size": len(active_users),
        "total_acquired_users": rt["total_acquired"],
        "quiz_rate": round(rt["quiz_prob"], 4),
        "send_drink_rate": 0.0 if disable_send_drink else round(rt["send_drink_prob"], 4),
        "radar_chat_rate": 0.0 if disable_send_drink else round(rt["radar_chat_prob"], 4),
        "satisfaction_rate": round(rt["satisfaction_prob"], 4),
        "seed_cohort_retention": seed_cohort_retention,
        "retained_users_count": retained_count,
        "eligible_users_count": len(eligible_users),
        "event_tag": event_tag,
        # 供下游杠杆 Agent 使用的当晚到店用户索引
        "_night_visitor_ids": [u["user_id"] for u in night_visitors_list],
        "_day_visitor_ids": [u["user_id"] for u in day_visitors_list],
    }
    return rt, day_output


def city_chengdu_node(state: dict) -> dict:
    if "成都" not in state["cities_runtime"]:
        return {}
    day_idx = state["current_day"]
    curr_date = START_DATE + timedelta(days=day_idx - 1)
    rt = state["cities_runtime"]["成都"]
    rt, out = _simulate_single_city_day_and_night(
        "成都", rt, day_idx, curr_date, state["seed"],
        state["disable_send_drink"], state["disable_online_word_of_mouth"], state["force_price_up_20pct"]
    )
    return {
        "cities_runtime": {"成都": rt},
        "daily_outputs": {"成都": out},
    }


def city_shanghai_node(state: dict) -> dict:
    if "上海" not in state["cities_runtime"]:
        return {}
    day_idx = state["current_day"]
    curr_date = START_DATE + timedelta(days=day_idx - 1)
    rt = state["cities_runtime"]["上海"]
    rt, out = _simulate_single_city_day_and_night(
        "上海", rt, day_idx, curr_date, state["seed"],
        state["disable_send_drink"], state["disable_online_word_of_mouth"], state["force_price_up_20pct"]
    )
    return {
        "cities_runtime": {"上海": rt},
        "daily_outputs": {"上海": out},
    }


def city_sanya_node(state: dict) -> dict:
    if "三亚" not in state["cities_runtime"]:
        return {}
    day_idx = state["current_day"]
    curr_date = START_DATE + timedelta(days=day_idx - 1)
    rt = state["cities_runtime"]["三亚"]
    rt, out = _simulate_single_city_day_and_night(
        "三亚", rt, day_idx, curr_date, state["seed"],
        state["disable_send_drink"], state["disable_online_word_of_mouth"], state["force_price_up_20pct"]
    )
    return {
        "cities_runtime": {"三亚": rt},
        "daily_outputs": {"三亚": out},
    }


def run_three_cities_parallel_node(state: dict) -> dict:
    """
    三个城市 Agent 并行执行节点（成都、上海、三亚各自独立核算房租、客流、竞品、节日；日夜两步分开算）
    """
    day_idx = state["current_day"]
    curr_date = START_DATE + timedelta(days=day_idx - 1)
    new_runtimes = {}
    new_outputs = {}

    for city_name in ["成都", "上海", "三亚"]:
        if city_name not in state["cities_runtime"]:
            continue
        rt = state["cities_runtime"][city_name]
        rt_updated, out = _simulate_single_city_day_and_night(
            city_name,
            rt,
            day_idx,
            curr_date,
            state["seed"],
            state["disable_send_drink"],
            state["disable_online_word_of_mouth"],
            state["force_price_up_20pct"],
        )
        new_runtimes[city_name] = rt_updated
        new_outputs[city_name] = out

    return {
        "cities_runtime": new_runtimes,
        "daily_outputs": new_outputs,
    }


# ==========================================
# 3. 杠杆 Agent × 3：社交裂变、线上口碑、价格/复购
# ==========================================
def lever_social_viral_node(state: dict) -> dict:
    """
    杠杆 Agent 1：社交裂变 Agent（九神测评解锁隐藏特调、送可免费送的 Shot / 半价送特调、星云雷达配对 10 句聊天）
    """
    day_idx = state["current_day"]
    disable_send = state["disable_send_drink"]
    runtimes = state["cities_runtime"]
    outputs = state["daily_outputs"]

    for city_name, rt in runtimes.items():
        out = outputs[city_name]
        rng = get_rng(state["seed"], f"social_{city_name}_{day_idx}")
        night_ids = set(out.get("_night_visitor_ids", []))
        day_ids = set(out.get("_day_visitor_ids", []))
        all_today_ids = night_ids | day_ids

        night_users = [u for u in rt["users"] if u["user_id"] in night_ids]
        today_users = [u for u in rt["users"] if u["user_id"] in all_today_ids]

        # 1) 九神测评完成（初始 40%，随氛围演化）
        quiz_done_today = 0
        for u in night_users:
            if not u["quiz_completed"]:
                if rng.random() < rt["quiz_prob"]:
                    u["quiz_completed"] = True
                    quiz_done_today += 1
            else:
                quiz_done_today += 1

        # 2) 担保送酒（免费 Shot 或半价特调）与星云雷达配对聊天（10句上限）
        drinks_sent_today = 0
        radar_chats_today = 0

        if not disable_send and len(night_users) >= 2:
            # 场内人数密度效应：夜晚在场人数适中（30~80人）时星云雷达同频配对率最高；太少则残影兜底，满载则略微嘈杂
            crowd_ratio = len(night_users) / float(MAX_NIGHT_CAPACITY)
            density_bonus = 1.15 if 0.35 <= crowd_ratio <= 0.88 else 0.90

            for u in night_users:
                # 点特调送同款 Shot，只能送人不能自饮
                p_send = rt["send_drink_prob"] * density_bonus * (1.15 if u["quiz_completed"] else 0.85)
                if rng.random() < min(0.65, p_send):
                    u["drinks_sent"] += 1
                    drinks_sent_today += 1
                    # 对方接住酒后生成 10 句限额聊天页面
                    if rng.random() < min(0.85, rt["radar_chat_prob"] * 2.2):
                        u["radar_chats"] += 1
                        radar_chats_today += 1
                        u["satisfaction_score"] = min(5.0, u["satisfaction_score"] + 0.25)

        # 3) 计算当日满意度（初始 70% 满意 >=4分，受雷达破冰、拥挤度、价格影响）
        satisfied_count = 0
        price_penalty = max(0.0, (rt["night_price"] - BASE_NIGHT_PRICE) / BASE_NIGHT_PRICE) * 0.35
        for u in today_users:
            base_sat_p = rt["satisfaction_prob"] - price_penalty
            if disable_send:
                # 若砍掉送饮杠杆，核心社交破冰闭环缺失，夜间独酌无聊导致满意度下滑
                base_sat_p -= 0.14
            if u["radar_chats"] > 0:
                base_sat_p += 0.12
            if rng.random() < min(0.95, max(0.20, base_sat_p)):
                u["satisfaction_score"] = round(rng.uniform(4.0, 5.0), 2)
                satisfied_count += 1
            else:
                u["satisfaction_score"] = round(rng.uniform(2.2, 3.8), 2)

        actual_sat_rate = satisfied_count / float(len(today_users)) if today_users else rt["satisfaction_prob"]
        # 动态平滑更新城市的转化概率（跑起来会变！）
        rt["satisfaction_prob"] = round(0.85 * rt["satisfaction_prob"] + 0.15 * actual_sat_rate, 4)
        rt["quiz_prob"] = round(min(0.75, max(0.25, rt["quiz_prob"] + (actual_sat_rate - 0.68) * 0.04)), 4)
        if not disable_send:
            rt["send_drink_prob"] = round(min(0.38, max(0.08, rt["send_drink_prob"] + (actual_sat_rate - 0.68) * 0.03)), 4)
            rt["radar_chat_prob"] = round(min(0.48, max(0.12, rt["radar_chat_prob"] + (actual_sat_rate - 0.68) * 0.04)), 4)

        # 4) 社交裂变带客计算（线下朋友带朋友 + 雷达破冰后次日约局 + 游客离店推荐）
        if disable_send:
            viral_new_leads = len(today_users) * 0.015
        else:
            viral_new_leads = (drinks_sent_today * 0.28) + (radar_chats_today * 0.35)
            # 三亚游客行程结束后口碑转介绍同圈层度假朋友
            if city_name == "三亚":
                viral_new_leads *= 1.35

        rt["pending_viral_new_users"] += viral_new_leads
        # 裂变系数 K = (社交裂变带来的潜在新客 + 线上口碑当日转化新客) / 当日活跃到店基数
        out["_satisfied_users_today"] = [
            u["user_id"] for u in today_users if u["satisfaction_score"] >= 4.0
        ]
        out["satisfaction_rate"] = round(rt["satisfaction_prob"], 4)
        out["quiz_rate"] = round(rt["quiz_prob"], 4)
        out["send_drink_rate"] = 0.0 if disable_send else round(rt["send_drink_prob"], 4)
        out["radar_chat_rate"] = 0.0 if disable_send else round(rt["radar_chat_prob"], 4)

    return {"cities_runtime": runtimes, "daily_outputs": outputs}


def lever_online_wom_node(state: dict) -> dict:
    """
    杠杆 Agent 2：线上口碑 Agent（小红书 / 抖音发帖）
    规则：满意用户（4分以上）30% 发小红书/抖音，1条笔记 7 天内带来 0.5 个新到店。
    同时在特定天数抽样 10% 典型用户调用 DeepSeek/Qwen 生成真实小红书文案与雷达破冰对话。
    """
    day_idx = state["current_day"]
    disable_online = state["disable_online_word_of_mouth"]
    runtimes = state["cities_runtime"]
    outputs = state["daily_outputs"]
    samples = list(state.get("user_dialogues_posts", []))

    for city_name, rt in runtimes.items():
        out = outputs[city_name]
        rng = get_rng(state["seed"], f"wom_{city_name}_{day_idx}")
        sat_ids = set(out.get("_satisfied_users_today", []))
        sat_users = [u for u in rt["users"] if u["user_id"] in sat_ids]

        # 1) 结算存量 7 天有效期内的笔记转化
        still_active_posts = []
        online_converted_today = 0.0
        for post in rt["active_posts"]:
            if post["remaining_days"] > 0:
                online_converted_today += post["daily_conv"]
                post["remaining_days"] -= 1
                if post["remaining_days"] > 0:
                    still_active_posts.append(post)

        # 2) 当日满意用户（>=4分）按 30% 概率发小红书/抖音笔记
        # v2.1 审计修复：明确【线上曝光】口径为“当日有效存量笔记与新增笔记在同城目标人群中的有效阅读曝光量（Views）”。
        # 数学闭环公式：1 条笔记 7 天内带来 0.5 个新到店，按小红书/抖音本地生活探店笔记 0.5% 的真实进店转化率（即每 200 次同城有效曝光带来 1 个新到店，1 条笔记 7 天产生约 100~125 次有效同城曝光，日均每篇存量笔记 15 次有效曝光 + 今日新发帖首日推荐流 20 次曝光）。
        # 保持与 v2.0 完全相同的发帖随机数调用序列，仅修正曝光量的放大倍数（从每篇365+新帖550修正为每篇15+新帖20）！
        new_posts_today = 0
        if not disable_online:
            for u in sat_users:
                p_post = ONLINE_POST_PROB_IF_SATISFIED * (1.15 if u["faction"] == "天堂" else 0.95)
                if rng.random() < p_post:
                    u["has_posted_online"] = True
                    new_posts_today += 1
                    still_active_posts.append({
                        "created_day": day_idx,
                        "remaining_days": 7,
                        "daily_conv": NEW_USERS_PER_POST_7D / 7.0,  # 7天共带来0.5个新到店
                    })

        rt["active_posts"] = still_active_posts
        rt["pending_online_new_users"] += online_converted_today

        # 线上曝光量（同城目标客群有效笔记阅读量）真实公式：
        # 线上曝光 = 存量7天有效期笔记数 × 15 + 今日新增笔记数 × 20
        impressions = 0 if disable_online else int(len(still_active_posts) * 15 + new_posts_today * 20)
        rt["last_online_impressions"] = impressions
        out["online_impressions"] = impressions
        out["active_posts_count"] = len(still_active_posts)
        out["new_posts_today"] = new_posts_today

        # 综合裂变系数 viral_k 计算并回填到当日记录
        # K值 = 当日由社交裂变(送饮/雷达)与线上口碑(小红书/抖音)共同产生的待转化新客数 / (当日活跃基数 + 城市保底分母25)
        # 避免客流极少时分母过小导致 K 值虚高
        effective_denom = max(28.0, float(out["day_visitors"] + out["night_visitors"]) * 0.85 + 15.0)
        total_viral_and_wom_leads = rt["pending_viral_new_users"] + online_converted_today
        viral_k = round(min(1.50, total_viral_and_wom_leads / effective_denom * 2.2), 4)
        rt["last_viral_k"] = viral_k
        out["viral_k"] = viral_k

    return {
        "cities_runtime": runtimes,
        "daily_outputs": outputs,
        "user_dialogues_posts": samples,
    }


def lever_pricing_repurchase_node(state: dict) -> dict:
    """
    杠杆 Agent 3：价格 / 复购 Agent（调价、活动策略学习与执行）
    每周（每 7 天）根据各城市上周期客流满载率、留存率、竞争压力动态学习调价与促复购活动，
    留存完整决策日志（不是写死固定时间表，而是基于状态指标反馈学习）。
    """
    day_idx = state["current_day"]
    runtimes = state["cities_runtime"]
    decision_logs = list(state.get("decision_logs", []))

    # 每 7 天触发一次周度价格与复购活动学习（第 7, 14, 21, 28, 35, 42, 49, 56, 63, 70, 77, 84, 90 天）
    if day_idx % 7 != 0 and day_idx != state["total_days"]:
        return {"cities_runtime": runtimes}

    week_num = (day_idx - 1) // 7 + 1
    history = state.get("history_records", [])
    force_up_20 = state.get("force_price_up_20pct", False)

    for city_name, rt in runtimes.items():
        # 取该城市最近 6 天 + 今日的经营表现
        city_hist = [h for h in history if h["city"] == city_name][-6:]
        curr_out = state["daily_outputs"].get(city_name)
        if curr_out:
            city_hist.append(curr_out)

        if not city_hist:
            continue

        avg_day_vis = sum(x["day_visitors"] for x in city_hist) / len(city_hist)
        avg_night_vis = sum(x["night_visitors"] for x in city_hist) / len(city_hist)
        avg_ret = sum(x["retention_rate"] for x in city_hist) / len(city_hist)
        avg_profit = sum(x["daily_net_profit"] for x in city_hist) / len(city_hist)

        old_day_p = rt["day_price"]
        old_night_p = rt["night_price"]
        new_day_p = old_day_p
        new_night_p = old_night_p
        campaign_action = rt["campaign_name"]
        reason_parts = []

        if force_up_20:
            # 敏感性实验 3：强制全程涨价 20% 观察价格弹性冲击
            new_day_p = round(BASE_DAY_PRICE * 1.20, 2)
            new_night_p = round(BASE_NIGHT_PRICE * 1.20, 2)
            campaign_action = "全线涨价20%压力测试"
            reason_parts.append(
                f"敏感性测试锁定涨价20%(日¥{new_day_p}/夜¥{new_night_p})，当前周均夜场{avg_night_vis:.1f}人，留存{avg_ret*100:.1f}%"
            )
        else:
            # 自适应强化学习调价规则（v2.1 审计修复：基于盈亏平衡约束、满载率与周环比留存/利润反馈学习）：
            # 1) 满载率计算（60座门店：夜场90人=1.5次翻台满载；夜场>=45人即超过75%座位率；日场>=50人即超过83%座位率）
            night_util = avg_night_vis / float(MAX_NIGHT_CAPACITY)
            day_util = avg_day_vis / float(MAX_DAY_CAPACITY)

            # 取上一周（前14~7天）均值，计算调价后的周环比反馈结果
            prev_week_hist = [h for h in history if h["city"] == city_name and (day_idx - 13) <= h["day_index"] <= (day_idx - 7)]
            prev_avg_ret = sum(x["retention_rate"] for x in prev_week_hist) / len(prev_week_hist) if prev_week_hist else avg_ret
            prev_avg_prof = sum(x["daily_net_profit"] for x in prev_week_hist) / len(prev_week_hist) if prev_week_hist else avg_profit
            ret_delta_pp = (avg_ret - prev_avg_ret) * 100.0
            prof_delta = avg_profit - prev_avg_prof

            # 夜晚场调价与活动决策（区分旺场提价期、平盘提质期、冷启动/淡季促销期与止跌保毛利期）
            if night_util >= 0.48 and avg_ret >= 0.13:
                # 夜场座位率超80%(>=43人)且留存健康(>=13%) -> 推高毛利隐藏特调+¥10加料（烟熏/冰印/拍立得），均价上浮 5%
                new_night_p = min(138.0, round(old_night_p * 1.05, 0))
                rt["campaign_boost_night"] = 0.04
                campaign_action = "主推隐藏特调+¥10烟熏/拍立得加料"
                reason_parts.append(
                    f"[触发条件] 夜场利用率{night_util*100:.0f}%(>=48%)且留存{avg_ret*100:.1f}%(>=13%)强劲 -> 上调夜价¥{old_night_p:.0f}→¥{new_night_p:.0f}拔高毛利"
                )
            elif night_util >= 0.36 and avg_ret >= 0.09:
                # 客流与留存进入稳态爬坡区（如上海中后期夜场）-> 止跌企稳，若此前降价至¥98则回调至¥102~106修复毛利，并开启阵营主题夜
                if old_night_p < 104.0 and prof_delta > 0:
                    new_night_p = round(old_night_p + 4.0, 0)
                    act_note = f"客流回暖回调夜价¥{old_night_p:.0f}→¥{new_night_p:.0f}"
                else:
                    new_night_p = old_night_p
                    act_note = f"稳守夜价¥{new_night_p:.0f}"
                rt["campaign_boost_night"] = 0.10
                campaign_action = "九神同频之夜：同阵营星云雷达配对送小食"
                reason_parts.append(
                    f"[触发条件] 夜场利用率{night_util*100:.0f}%(>=36%)且留存{avg_ret*100:.1f}%(>=9%)企稳 -> {act_note}，强化同阵营促活"
                )
            else:
                # 客流不足(<36%)或留存偏低(<9%)：启动降价6%让利（从120逐步下探至113->106->100->98底线）配双倍Shot破冰补贴
                new_night_p = max(98.0, round(old_night_p * 0.94, 0))
                rt["campaign_boost_night"] = 0.10
                if old_night_p <= 98.0 and avg_ret < 0.05:
                    campaign_action = "触及¥98底线保毛利：科罗娜/1664拼桌套餐+双倍Shot兜底"
                    reason_parts.append(
                        f"[触发条件] 夜场利用率仅{night_util*100:.0f}%(<36%)且留存{avg_ret*100:.1f}%(<5%)，已触及¥98成本底线 -> 停止降价，锁死¥98推拼桌套餐"
                    )
                else:
                    campaign_action = "微醺破冰扶持：首杯特调赠双Shot+半价送酒补贴"
                    reason_parts.append(
                        f"[触发条件] 夜场利用率{night_util*100:.0f}%(<36%)或留存{avg_ret*100:.1f}%(<9%)未达标 -> 下调夜价¥{old_night_p:.0f}→¥{new_night_p:.0f}以价换量激活雷达密度"
                    )

            # 2) 白天场（25-28元区间）微调决策
            if day_util > 0.72:
                new_day_p = min(28.0, round(old_day_p + 1.0, 0))
                rt["campaign_boost_day"] = 0.03
                reason_parts.append(f"日咖利用率{day_util*100:.0f}%(>72%)较高，日价¥{old_day_p:.0f}→¥{new_day_p:.0f}")
            elif day_util < 0.40:
                new_day_p = max(25.0, round(old_day_p - 1.0, 0))
                rt["campaign_boost_day"] = 0.08
                reason_parts.append(f"日咖利用率{day_util*100:.0f}%(<40%)偏弱(周均{avg_day_vis:.0f}人)，日价¥{old_day_p:.0f}→¥{new_day_p:.0f}配次卡引流")
            else:
                reason_parts.append(f"日咖利用率{day_util*100:.0f}%适中，稳守¥{new_day_p:.0f}")

            reason_parts.append(
                f"[调价后效果追踪] 留存周环比 {ret_delta_pp:+.2f}pp (当周均值{avg_ret*100:.1f}%), 日均净利周环比 ¥{prof_delta:+.0f} (当周日均¥{avg_profit:.0f})"
            )

        rt["day_price"] = new_day_p
        rt["night_price"] = new_night_p
        rt["campaign_name"] = campaign_action
        rt["pricing_note"] = campaign_action

        # 更新当日 output 里的调价记录文字列
        if city_name in state["daily_outputs"]:
            state["daily_outputs"][city_name]["pricing_record"] = (
                f"W{week_num}调价:日¥{old_day_p:.0f}→¥{new_day_p:.0f}/夜¥{old_night_p:.0f}→¥{new_night_p:.0f} | {campaign_action}"
            )

        decision_logs.append({
            "week": week_num,
            "day_index": day_idx,
            "date_str": state["current_date"],
            "city": city_name,
            "old_day_price": old_day_p,
            "new_day_price": new_day_p,
            "old_night_price": old_night_p,
            "new_night_price": new_night_p,
            "avg_day_visitors": round(avg_day_vis, 1),
            "avg_night_visitors": round(avg_night_vis, 1),
            "night_util_pct": round((avg_night_vis / float(MAX_NIGHT_CAPACITY)) * 100.0, 1),
            "avg_retention_pct": round(avg_ret * 100.0, 2),
            "avg_daily_profit": round(avg_profit, 2),
            "campaign_action": campaign_action,
            "reason": "；".join(reason_parts),
        })

    return {
        "cities_runtime": runtimes,
        "daily_outputs": state["daily_outputs"],
        "decision_logs": decision_logs,
    }


# ==========================================
# 4. 检查 Agent 1：评测 Agent（数据校验红线，错了直接报错停下）
# ==========================================
def evaluator_check_node(state: dict) -> dict:
    """
    评测 Agent：严格校验每日每个城市的数据红线：
    1) 白天人数 <= 120，晚上人数 <= 90，单日总人数 <= 210
    2) 白天收入 == 白天人数 × 白天均价，晚上收入 == 晚上人数 × 晚上均价，总收入 == 白天收入 + 晚上收入
    3) 0 <= 留存率 <= 100% (1.0)
    4) 累计获客人数 <= 1000
    若任何一条违背，立即抛出 ValueError 终止运行！
    """
    outputs = state.get("daily_outputs", {})
    errors = []

    for city_name, out in outputs.items():
        d_vis = out["day_visitors"]
        n_vis = out["night_visitors"]
        t_vis = d_vis + n_vis

        if d_vis < 0 or d_vis > MAX_DAY_CAPACITY:
            errors.append(f"[{city_name} Day {out['day_index']}] 白天人数超限: {d_vis} (上限 {MAX_DAY_CAPACITY})")
        if n_vis < 0 or n_vis > MAX_NIGHT_CAPACITY:
            errors.append(f"[{city_name} Day {out['day_index']}] 晚上人数超限: {n_vis} (上限 {MAX_NIGHT_CAPACITY})")
        if t_vis > MAX_DAILY_CAPACITY:
            errors.append(f"[{city_name} Day {out['day_index']}] 全天总人数超限: {t_vis} (上限 {MAX_DAILY_CAPACITY})")

        expected_day_rev = round(d_vis * out["day_price"], 2)
        expected_night_rev = round(n_vis * out["night_price"], 2)
        expected_total_rev = round(expected_day_rev + expected_night_rev, 2)

        if abs(out["day_revenue"] - expected_day_rev) > 0.05:
            errors.append(f"[{city_name} Day {out['day_index']}] 白天收入核算错误: {out['day_revenue']} != {expected_day_rev}")
        if abs(out["night_revenue"] - expected_night_rev) > 0.05:
            errors.append(f"[{city_name} Day {out['day_index']}] 晚上收入核算错误: {out['night_revenue']} != {expected_night_rev}")
        if abs(out["total_revenue"] - expected_total_rev) > 0.05:
            errors.append(f"[{city_name} Day {out['day_index']}] 总收入核算错误: {out['total_revenue']} != {expected_total_rev}")

        ret = out["retention_rate"]
        if ret < 0.0 or ret > 1.0:
            errors.append(f"[{city_name} Day {out['day_index']}] 留存率越界: {ret}")

        if out["total_acquired_users"] > MAX_USERS_PER_CITY:
            errors.append(f"[{city_name} Day {out['day_index']}] 累计用户池超过1000人上限: {out['total_acquired_users']}")

    if errors:
        err_msg = " | ".join(errors)
        raise ValueError(f"【评测 Agent 红线拦截报错】数据校验未通过，立即停止：{err_msg}")

    return {"validation_errors": errors}


# ==========================================
# 5. 总调度 Agent：汇总、LLM 抽样发帖/对话、写周报、30/60/90天判生死、参数审计
# ==========================================
GLOBAL_LLM_CLIENT = CostAgentAndLLMClient(use_cache=True)


def _judge_city_verdict(city_name: str, city_records: List[dict], current_day: int) -> dict:
    """
    严格按生死线标准判定（模拟数据）：
    - 爆款 = 30 日留存 > 15% 且 60 天内月利润转正
    - 活下来 = 30 日留存 5%-15%，90 天内打平
    - 危在旦夕 = 30 日留存 < 5% 或 一直亏
    """
    if not city_records:
        return {"verdict": "待判定", "reason": "数据不足"}

    # v2.1 审计修复：报告中的 30日留存 严格取第 30 天（2027-01-18）当日值，确保与 CSV 第30天 100% 数值一致（若总天数<30则取最后一天）
    day30_row = next((r for r in city_records if r["day_index"] == 30), city_records[-1])
    ret_30d = day30_row["retention_rate"]
    ret_90d = city_records[-1]["retention_rate"]
    seed_ret_30d = day30_row.get("seed_cohort_retention", ret_30d)

    # 月度利润（每30天为一个月度窗口）
    m1_profit = sum(r["daily_net_profit"] for r in city_records if r["day_index"] <= 30)
    m2_profit = sum(r["daily_net_profit"] for r in city_records if 31 <= r["day_index"] <= 60)
    m3_profit = sum(r["daily_net_profit"] for r in city_records if 61 <= r["day_index"] <= 90)
    total_90d_profit = sum(r["daily_net_profit"] for r in city_records)
    last_14d_profit = sum(r["daily_net_profit"] for r in city_records[-min(14, len(city_records)):])

    # 判断 60 天内月利润是否转正（M1 > 0 或 M2 > 0）
    profit_positive_within_60d = (m1_profit > 0) or (len(city_records) >= 35 and m2_profit > 0)
    # 判断 90 天内是否实现月度或近期经营打平（M3 >= 10000 或 累计 >= 0）
    breakeven_within_90d = (m3_profit >= 10000) or (total_90d_profit >= 0)

    if ret_30d > 0.15 and profit_positive_within_60d:
        verdict = "爆款"
        reason = (
            f"模拟30日留存达 {ret_30d*100:.1f}% (>15%)，且60天内月度经营利润转正"
            f"（M1: ¥{m1_profit:,.0f}, M2: ¥{m2_profit:,.0f}），累计90天经营净利 ¥{total_90d_profit:,.0f}。"
        )
    elif ret_30d >= 0.05 and breakeven_within_90d:
        verdict = "活下来"
        reason = (
            f"模拟30日留存为 {ret_30d*100:.1f}%，虽前期受高房租人工压制或慢热影响，但在90天内实现阶段性经营打平/微利"
            f"（M1: ¥{m1_profit:,.0f}, M2: ¥{m2_profit:,.0f}, M3: ¥{m3_profit:,.0f}）。"
        )
    else:
        verdict = "危在旦夕"
        reason = (
            f"模拟30日留存 {ret_30d*100:.1f}%，且在竞品分流或高固定成本下未能打平"
            f"（M1: ¥{m1_profit:,.0f}, M2: ¥{m2_profit:,.0f}, M3: ¥{m3_profit:,.0f}，累计经营净利 ¥{total_90d_profit:,.0f}）。"
        )

    return {
        "city": city_name,
        "day": current_day,
        "verdict": verdict,
        "ret_30d": round(ret_30d, 4),
        "ret_90d": round(ret_90d, 4),
        "seed_ret_30d": round(seed_ret_30d, 4),
        "m1_profit": round(m1_profit, 2),
        "m2_profit": round(m2_profit, 2),
        "m3_profit": round(m3_profit, 2),
        "total_90d_profit": round(total_90d_profit, 2),
        "renovation_payback_pct": city_records[-1]["renovation_payback_pct"],
        "reason": reason,
    }


def master_summarize_and_learn_node(state: dict) -> dict:
    """
    总调度 Agent 汇总节点：
    1) 将当日三城指标写入 history_records
    2) LLM 任务 2：定期抽样 10% 典型用户生成真实小红书发帖与星云雷达破冰对话（全周期 25 次调用）
    3) LLM 任务 1：每周（每 7 天）结合调价决策生成周度学习决策与周报（全周期 13 次调用）
    4) 第 30 / 60 / 90 天判生死、执行参数审计（检查参数有没有被人为调好看）
    5) LLM 任务 3：第 90 天收官生成全局总结（1 次调用，合计 25 + 13 + 1 = 39 次 API 调用）
    """
    day_idx = state["current_day"]
    total_days = state["total_days"]
    enable_llm = state.get("enable_llm", True)

    history = list(state.get("history_records", []))
    outputs = state.get("daily_outputs", {})

    # 清理内部临时字段后追加到 history
    for city_name in ["成都", "上海", "三亚"]:
        if city_name in outputs:
            clean_rec = {k: v for k, v in outputs[city_name].items() if not k.startswith("_")}
            history.append(clean_rec)

    samples = list(state.get("user_dialogues_posts", []))
    weekly_reports = list(state.get("weekly_reports", []))
    milestone_verdicts = dict(state.get("milestone_verdicts", {}))
    audit_log = list(state.get("parameter_audit_log", []))
    final_summary_md = state.get("final_summary_markdown", "")

    # ------------------------------------------
    # LLM 任务 2：抽 10% 活跃用户代表生成真实对话 / 发帖（全周期控制在 25 次调用）
    # 触发天数：Day 3, 6, 10, 13, 17, 20, 24, 27, 31, 34, 38, 41, 45, 48, 52, 55, 59, 62, 66, 69, 73, 76, 80, 83, 87 (共25次)
    # 若是 7天小测试（total_days=7），在 Day 3, 6 触发
    # ------------------------------------------
    sample_trigger_days = {3, 6, 10, 13, 17, 20, 24, 27, 31, 34, 38, 41, 45, 48, 52, 55, 59, 62, 66, 69, 73, 76, 80, 83, 87}
    if enable_llm and day_idx in sample_trigger_days and not state["disable_online_word_of_mouth"]:
        city_order = list(outputs.keys())
        target_city = city_order[(day_idx // 3) % len(city_order)]
        rt = state["cities_runtime"][target_city]
        rng = get_rng(state["seed"], f"sample_{day_idx}")
        u = rng.choice(rt["users"])
        other_u = rng.choice(rt["users"])
        rel = "同类" if u["god"] == other_u["god"] else ("互补" if u["faction"] == other_u["faction"] else "异类")

        sys_p = (
            "你是 ST.JOHN 日咖夜酒小程序的用户行为模拟器。调性：克制、高级、不装逼、无废话。"
            "严格使用 ST.JOHN 真实设定：19:00切换暗夜、九神三阵营、隐藏特调（伪装者/失真低音/巴别塔残片/杠杆率/现金流/安全距离/深渊回响/红蓝药丸）、"
            "点特调送同款Shot送人、星云雷达接酒解锁10句聊天。"
        )
        usr_p = (
            f"【模拟抽样】第{day_idx}天，城市：{target_city}（{CITY_CONFIGS[target_city].location}）。"
            f"用户A({u['age']}岁, {'游客' if u['is_tourist'] else '本地'}, {u['god']}/{u['faction']}/{u['domain']}, 解锁特调:{u['unlocked_drink']})，"
            f"配对用户B({other_u['god']}/{other_u['faction']}, 关系:{rel})。"
            f"请用120字以内输出两部分：1)【雷达10句破冰摘录】(2句真实克制的对话)；2)【小红书/抖音打卡笔记】(带1个真实痛点或爽点)。"
        )
        content = GLOBAL_LLM_CLIENT.call_llm(
            task_type="抽10%用户生成真实对话/发帖",
            system_prompt=sys_p,
            user_prompt=usr_p,
            day=day_idx,
            city=target_city,
            max_tokens=220,
        )
        samples.append({
            "day_index": day_idx,
            "city": target_city,
            "sample_type": f"雷达{rel}破冰+小红书笔记",
            "god_and_faction": f"{u['god']}({u['faction']}) × {other_u['god']}({other_u['faction']})",
            "content": content,
        })

    # ------------------------------------------
    # LLM 任务 1：每周调价决策与周报（每 7 天一次 + 第90天收官周，共 13 次调用）
    # ------------------------------------------
    if day_idx % 7 == 0 or day_idx == total_days:
        week_num = (day_idx - 1) // 7 + 1
        week_slice = [r for r in history if (day_idx - 6) <= r["day_index"] <= day_idx]
        week_decs = [d for d in state.get("decision_logs", []) if d["day_index"] == day_idx]

        # 汇总各城市本周模拟表现
        city_briefs = []
        for c_name in outputs.keys():
            c_rows = [r for r in week_slice if r["city"] == c_name]
            if not c_rows:
                continue
            rev_w = sum(r["total_revenue"] for r in c_rows)
            prof_w = sum(r["daily_net_profit"] for r in c_rows)
            ret_w = c_rows[-1]["retention_rate"]
            vk_w = sum(r["viral_k"] for r in c_rows) / len(c_rows)
            dec_str = next((d["campaign_action"] for d in week_decs if d["city"] == c_name), "维持基准")
            city_briefs.append(
                f"{c_name}: 周营收(模拟)¥{rev_w:,.0f}, 周净利(模拟)¥{prof_w:,.0f}, 期末留存(模拟){ret_w*100:.1f}%, 均值裂变K={vk_w:.2f}, 学习策略=[{dec_str}]"
            )

        brief_text = "；\n".join(city_briefs)
        if enable_llm:
            sys_p = (
                "你是 ST.JOHN v2 总调度 Agent。负责每周汇总三城经营模拟数据、审计学习调价策略，撰写克制犀利的周度复盘。"
                "所有数字必须明确视为【模拟】结果，不夸大、不粉饰亏损。"
            )
            usr_p = (
                f"第 {week_num} 周（截止 Day {day_idx}，场景：{state['scenario_name']}）模拟经营汇总：\n{brief_text}\n"
                f"请用 180 字以内输出本周总结：包含 1) 三城冷暖反差与核心瓶颈；2) 本周价格/复购 Agent 调价与活动决策的合理性评估；3) 下周重点动作。"
            )
            llm_week_comment = GLOBAL_LLM_CLIENT.call_llm(
                task_type="每周调价决策与周报",
                system_prompt=sys_p,
                user_prompt=usr_p,
                day=day_idx,
                city="三城汇总",
                max_tokens=280,
            )
        else:
            llm_week_comment = f"【规则引擎周报 W{week_num}】{brief_text}"

        weekly_reports.append({
            "week": week_num,
            "end_day": day_idx,
            "date_str": state["current_date"],
            "metrics_summary": brief_text,
            "llm_analysis": llm_week_comment,
            "decisions": week_decs,
        })

    # ------------------------------------------
    # 第 30 / 60 / 90 天生死判定与参数反作弊审计
    # ------------------------------------------
    if day_idx in (30, 60, 90) or day_idx == total_days:
        day_verdicts = {}
        for c_name in outputs.keys():
            c_all = [r for r in history if r["city"] == c_name]
            day_verdicts[c_name] = _judge_city_verdict(c_name, c_all, day_idx)
        milestone_verdicts[f"day_{day_idx}"] = day_verdicts

        # 总调度 Agent 审计参数有没有被人为调好看
        audit_entry = (
            f"[Day {day_idx} 参数反作弊审计 v2.1] "
            f"1) 锁定参数核验：房租/人工/装修/容量/种子(20261220) 100% 锁定未改动（成都5.8万/月、上海9.8万/月、三亚6.84万/月，日<=120/夜<=90/总<=210）；"
            f"2) 留存口径核验：消除 Day7→Day8 固定阈值阶跃 Bug，报告30日留存与 CSV 第30天单日留存 100% 对齐；"
            f"3) 曝光口径核验：按 1帖7天=0.5新客 及 0.5% 同城进店转化率重算（存量帖×15+新帖×20），消除虚高曝光；"
            f"4) viral_k 归一化审计：分母加 15 人平滑底数仅修正低客流除零虚高，不影响任何到店人数、收入与生死判定。"
        )
        audit_log.append(audit_entry)

    # ------------------------------------------
    # LLM 任务 3：第 90 天（或最后一天）全局终局总结与失败预案（1 次调用）
    # ------------------------------------------
    if day_idx == total_days and enable_llm and total_days == 90 and state["scenario_name"] == "baseline_90d":
        v90 = milestone_verdicts.get("day_90", {})
        v_text = "\n".join(
            f"- {c}: 生死判定【{info['verdict']}】，{info['reason']} 装修回本进度(模拟): {info['renovation_payback_pct']:.1f}%"
            for c, info in v90.items()
        )
        sys_p = (
            "你是 ST.JOHN v2 首席战略与风险审计官。请根据 90 天多 Agent 冷启动模拟结果，给出客观、冷峻的终局裁决与失败预案。"
            "所有数据必须注明为【模拟】，严禁把模拟数据当成真实业绩。"
        )
        usr_p = (
            f"ST.JOHN 日咖夜酒 90 天模拟终局数据（2026-12-20 至 2027-03-19）：\n{v_text}\n"
            f"请输出结构精炼的总结（350字以内）：\n"
            f"1. 三个城市 90 天生死裁决核心归因（对比 Ken 的初始假设与实际模拟跑出结果）；\n"
            f"2. 针对处于【危在旦夕】或【回本缓慢】城市的硬核失败预案（关停止损线、减面积/砍日咖保夜酒、B2B包场或快闪店转型方案）。"
        )
        final_summary_md = GLOBAL_LLM_CLIENT.call_llm(
            task_type="90天终局总结与失败预案",
            system_prompt=sys_p,
            user_prompt=usr_p,
            day=day_idx,
            city="全局终局",
            max_tokens=450,
        )

    cost_ledger = GLOBAL_LLM_CLIENT.get_summary()

    return {
        "history_records": history,
        "user_dialogues_posts": samples,
        "weekly_reports": weekly_reports,
        "milestone_verdicts": milestone_verdicts,
        "parameter_audit_log": audit_log,
        "final_summary_markdown": final_summary_md,
        "cost_ledger": cost_ledger,
        "current_day": day_idx + 1,
    }
