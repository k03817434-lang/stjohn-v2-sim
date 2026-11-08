"""
ST.JOHN v2 - 多城市 90 天冷启动模拟系统配置与参数不变层
所有模拟数字均标注“模拟”，严格区分【实测 / 估算 / 假设】三层参数来源。
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Tuple

# ==========================================
# 1. 全局时间与随机种子配置
# ==========================================
GLOBAL_SEED = 20261220
START_DATE = date(2026, 12, 20)
END_DATE = date(2027, 3, 19)  # 共 90 天
TOTAL_DAYS = 90

# 节日与特殊时期定义（2026-12-20 ~ 2027-03-19）
# 2027年春节除夕为 2027-02-05，春节初一为 2027-02-06，假期 2027-02-05 ~ 2027-02-11
FESTIVALS = {
    date(2026, 12, 24): ("平安夜", 1.25),
    date(2026, 12, 25): ("圣诞节", 1.35),
    date(2026, 12, 31): ("跨年夜", 1.45),
    date(2027, 1, 1): ("元旦", 1.35),
    date(2027, 2, 5): ("除夕", 1.15),
    date(2027, 2, 6): ("春节初一", 1.30),
    date(2027, 2, 7): ("春节初二", 1.35),
    date(2027, 2, 8): ("春节初三", 1.35),
    date(2027, 2, 9): ("春节初四", 1.30),
    date(2027, 2, 10): ("春节初五", 1.25),
    date(2027, 2, 11): ("春节初六", 1.20),
    date(2027, 2, 14): ("情人节", 1.40),
}

# 冷周：1月中下旬（取 2027-01-18 至 2027-01-24，共7天，客流 -20%）
COLD_WEEK_START = date(2027, 1, 18)
COLD_WEEK_END = date(2027, 1, 24)
COLD_WEEK_MULTIPLIER = 0.80

# ==========================================
# 2. 容量与菜单定价配置（实测层）
# ==========================================
AREA_SQM = 150
SEATS = 60
DAY_TURNOVER_MAX = 2.0
NIGHT_TURNOVER_MAX = 1.5
MAX_DAY_CAPACITY = int(SEATS * DAY_TURNOVER_MAX)      # 120 人
MAX_NIGHT_CAPACITY = int(SEATS * NIGHT_TURNOVER_MAX)  # 90 人
MAX_DAILY_CAPACITY = MAX_DAY_CAPACITY + MAX_NIGHT_CAPACITY  # 210 人

BASE_DAY_PRICE = 26.0    # 白天均价 26 元（25-28）
BASE_NIGHT_PRICE = 120.0 # 晚上均价 120 元

# 九神与三阵营（来自 02-ST.JOHN-知识库.md）
GODS_BY_FACTION = {
    "天堂": [
        {"god": "维纳斯", "domain": "精神审美", "trait": "美"},
        {"god": "阿波罗", "domain": "精神审美", "trait": "光"},
        {"god": "雅典娜", "domain": "精神审美", "trait": "智"},
    ],
    "人间": [
        {"god": "狄俄尼索斯", "domain": "肉身与关系", "trait": "酒"},
        {"god": "得墨忒耳", "domain": "肉身与关系", "trait": "食"},
        {"god": "赫斯提亚", "domain": "肉身与关系", "trait": "家火"},
    ],
    "地狱": [
        {"god": "哈迪斯", "domain": "现实杠杆", "trait": "财"},
        {"god": "赫尔墨斯", "domain": "现实杠杆", "trait": "商"},
        {"god": "普罗米修斯", "domain": "现实杠杆", "trait": "盗火"},
    ],
}

NIGHT_PUBLIC_DRINKS = ["冷钢 ¥78", "余烬 ¥88", "夜行者 ¥98", "深渊凝视 ¥118", "科罗娜 ¥38", "一六六四白啤 ¥45", "健力士黑啤 ¥48"]
NIGHT_HIDDEN_DRINKS = ["伪装者", "失真低音", "巴别塔残片", "杠杆率", "现金流", "安全距离", "深渊回响", "红蓝药丸"]
NIGHT_FOODS = ["黑蒜松露炸薯条 ¥48", "暗流脆片炸鸡 ¥58", "深渊和牛塔可 ¥68", "碳化迷你汉堡 ¥68", "黑胶唱片薄底披萨 ¥78"]

# ==========================================
# 3. 城市配置（估算层 + 实测层）
# ==========================================
@dataclass
class CityConfig:
    name: str
    location: str
    monthly_rent: float        # 元/月
    monthly_labor: float       # 元/月（已减薪 20%）
    renovation_cost: float     # 元（单独算回本，不全算进 90 天利润）
    marketing_budget_90d: float # 40000 元 / 90天
    factions: Dict[str, float] # 天堂 / 人间 / 地狱 比例
    local_ratio: float         # 本地人占比
    tourist_ratio: float       # 游客占比
    tourist_stay_days: int     # 游客平均停留天数
    competitor_pressure: float # 竞品分流压力系数（城市特征）
    night_affinity: float      # 夜间社交活跃度修正
    day_affinity: float        # 日间咖啡刚需度修正
    price_elasticity: float    # 价格敏感度（越高对涨价越敏感）

CITY_CONFIGS: Dict[str, CityConfig] = {
    "成都": CityConfig(
        name="成都",
        location="东门大桥",
        monthly_rent=22000.0,
        monthly_labor=36000.0,
        renovation_cost=500000.0,
        marketing_budget_90d=40000.0,
        factions={"天堂": 0.30, "人间": 0.45, "地狱": 0.25},
        local_ratio=0.90,
        tourist_ratio=0.10,
        tourist_stay_days=4,
        competitor_pressure=0.22,  # 东门大桥/太古里周边独立咖啡与小酒馆极度内卷，人间派重性价比但竞品极多
        night_affinity=0.95,
        day_affinity=0.90,
        price_elasticity=1.35,     # 对溢价较敏感，容易因审美疲劳或竞品低价流失
    ),
    "上海": CityConfig(
        name="上海",
        location="INS 附近",
        monthly_rent=50000.0,
        monthly_labor=48000.0,
        renovation_cost=800000.0,
        marketing_budget_90d=40000.0,
        factions={"天堂": 0.25, "人间": 0.35, "地狱": 0.40},
        local_ratio=0.88,
        tourist_ratio=0.12,
        tourist_stay_days=3,
        competitor_pressure=0.16,  # INS 周边夜生活密集，地狱派（搞钱/现实杠杆）对垂直社交破冰有明确需求
        night_affinity=1.08,
        day_affinity=1.12,         # 咖啡高频刚需
        price_elasticity=0.90,     # 对 26/120 定价耐受度高，但高昂房租人工压制利润空间
    ),
    "三亚": CityConfig(
        name="三亚",
        location="亚龙湾",
        monthly_rent=30000.0,
        monthly_labor=38400.0,
        renovation_cost=600000.0,
        marketing_budget_90d=40000.0,
        factions={"天堂": 0.50, "人间": 0.35, "地狱": 0.15},
        local_ratio=0.10,
        tourist_ratio=0.90,
        tourist_stay_days=4,
        competitor_pressure=0.08,  # 亚龙湾夜间高品质神秘感社交酒馆稀缺，12月-3月正值三亚冬季旺季
        night_affinity=1.25,       # 度假人群夜晚破冰/微醺意愿高
        day_affinity=1.05,
        price_elasticity=0.75,     # 度假客群对价格不敏感，更看重出片与体验
    ),
}

# ==========================================
# 4. 用户与行为初始参数（假设层）
# ==========================================
MAX_USERS_PER_CITY = 1000
SEED_USERS_PER_CITY = 100
AGE_18_35_RATIO = 0.92         # 18-35岁占 90% 以上

# 到店频次基准：白天一周 3 次（3/7 ≈ 0.4286），晚上 2 次（2/7 ≈ 0.2857）
BASE_DAY_VISIT_PROB = 3.0 / 7.0
BASE_NIGHT_VISIT_PROB = 2.0 / 7.0

# 初始行为转化率（跑起来会随满意度和杠杆动态变化）
INIT_QUIZ_COMPLETION_PROB = 0.40   # 测评完成 40%
INIT_SEND_DRINK_PROB = 0.15        # 送饮 15%
INIT_RADAR_CHAT_PROB = 0.25        # 雷达聊天 25%
INIT_SATISFACTION_PROB = 0.70      # 满意（>=4分）70%

CHURN_INACTIVE_DAYS = 14           # 本地用户 14 天不来 = 流失
ONLINE_POST_PROB_IF_SATISFIED = 0.30  # 满意用户（4分以上）30% 发小红书/抖音
NEW_USERS_PER_POST_7D = 0.50       # 1 条笔记 7 天内带来 0.5 个新到店（即每天约 0.5/7 个）

# 生死线判定标准
VERDICT_RULES = {
    "爆款": "30日留存 > 15% 且 60天内月利润转正",
    "活下来": "30日留存 5%~15% 且 90天内月度/单日经营打平",
    "危在旦夕": "30日留存 < 5% 或 持续亏损无法打平",
}

# 参数可信度分层字典（用于审计与 Dashboard 透明展示）
PARAMETER_CREDIBILITY = [
    {"类别": "实测", "参数名": "日夜切换时间与菜单", "数值": "19:00切换；日间22款明牌（均价26元），夜间12款明牌+8款隐藏（均价120元）", "来源": "ST.JOHN v1 真机原型 & 02知识库"},
    {"类别": "实测", "参数名": "送饮与雷达规则", "数值": "点特调送同款Shot（仅限送人，05:00过期），付费送特调半价半量；接酒解锁10句聊天", "来源": "02-ST.JOHN-知识库.md"},
    {"类别": "实测", "参数名": "九神三阵营比例", "数值": "成都30/45/25；上海25/35/40；三亚50/35/15（天堂/人间/地狱）", "来源": "v1 测评模型与城市人群画像"},
    {"类别": "估算", "参数名": "成都（东门大桥）成本", "数值": "房租2.2万/月，人工3.6万/月（已减薪20%），装修50万，投放4万/90天", "来源": "商业地产与餐饮行业估算"},
    {"类别": "估算", "参数名": "上海（INS附近）成本", "数值": "房租5.0万/月，人工4.8万/月（已减薪20%），装修80万，投放4万/90天", "来源": "商业地产与餐饮行业估算"},
    {"类别": "估算", "参数名": "三亚（亚龙湾）成本", "数值": "房租3.0万/月，人工3.84万/月（已减薪20%），装修60万，投放4万/90天", "来源": "商业地产与餐饮行业估算"},
    {"类别": "估算", "参数名": "门店物理容量上限", "数值": "150㎡ / 60座；白天翻台2次上限120人，晚上翻台1.5次上限90人，单日上限210人", "来源": "实体店空间测算"},
    {"类别": "假设", "参数名": "初始互动与满意率", "数值": "测评40%、送饮15%、雷达25%、满意70%（随运营与拥挤度动态演化）", "来源": "冷启动业务假设"},
    {"类别": "假设", "参数名": "线上口碑与裂变转化", "数值": "满意用户30%发帖，1条笔记7天内带来0.5个新客；14天未到店算流失（游客按4天行程算）", "来源": "增长模型假设"},
    {"类别": "假设", "参数名": "节日与冷周冲击", "数值": "圣诞/元旦/春节/情人节脉冲加成；1月中下旬7天冷周客流 -20%", "来源": "季节性波动假设"},
]
