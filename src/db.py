"""
ST.JOHN v2 - SQLite 存储层
存储模拟每日指标、每周总结、价格/活动学习决策日志、抽样用户生成内容、LLM API 成本账本。
"""
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "stjohn_v2_sim.db"


class SimulationDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_tables(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario TEXT,
                    day_index INTEGER,
                    date_str TEXT,
                    city TEXT,
                    day_visitors INTEGER,
                    night_visitors INTEGER,
                    day_revenue REAL,
                    night_revenue REAL,
                    total_revenue REAL,
                    retention_rate REAL,
                    new_users INTEGER,
                    viral_k REAL,
                    online_impressions INTEGER,
                    pricing_record TEXT,
                    day_price REAL,
                    night_price REAL,
                    daily_fixed_cost REAL,
                    daily_cogs REAL,
                    daily_net_profit REAL,
                    cumulative_profit REAL,
                    renovation_payback_pct REAL,
                    active_pool_size INTEGER,
                    total_acquired_users INTEGER,
                    quiz_rate REAL,
                    send_drink_rate REAL,
                    radar_chat_rate REAL,
                    satisfaction_rate REAL,
                    event_tag TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS decision_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario TEXT,
                    week INTEGER,
                    day_index INTEGER,
                    city TEXT,
                    old_day_price REAL,
                    new_day_price REAL,
                    old_night_price REAL,
                    new_night_price REAL,
                    campaign_action TEXT,
                    reason TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario TEXT,
                    day_index INTEGER,
                    city TEXT,
                    sample_type TEXT,
                    god_and_faction TEXT,
                    content TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS llm_cost_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario TEXT,
                    call_index INTEGER,
                    day_index INTEGER,
                    city TEXT,
                    task_type TEXT,
                    provider TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    cost_rmb REAL,
                    cached INTEGER,
                    latency_ms INTEGER
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scenario_verdicts (
                    scenario TEXT PRIMARY KEY,
                    verdict_json TEXT,
                    weekly_reports_json TEXT,
                    cost_summary_json TEXT,
                    audit_log_json TEXT
                )
            """)
            conn.commit()

    def clear_scenario(self, scenario: str):
        with self._get_conn() as conn:
            cur = conn.cursor()
            for table in ["daily_metrics", "decision_logs", "user_samples", "llm_cost_logs", "scenario_verdicts"]:
                cur.execute(f"DELETE FROM {table} WHERE scenario = ?", (scenario,))
            conn.commit()

    def save_simulation_run(
        self,
        scenario: str,
        history_records: List[dict],
        decision_logs: List[dict],
        user_samples: List[dict],
        cost_summary: dict,
        milestone_verdicts: dict,
        weekly_reports: List[dict],
        audit_log: List[str],
    ):
        self.clear_scenario(scenario)
        with self._get_conn() as conn:
            cur = conn.cursor()
            for r in history_records:
                cur.execute("""
                    INSERT INTO daily_metrics (
                        scenario, day_index, date_str, city, day_visitors, night_visitors,
                        day_revenue, night_revenue, total_revenue, retention_rate, new_users,
                        viral_k, online_impressions, pricing_record, day_price, night_price,
                        daily_fixed_cost, daily_cogs, daily_net_profit, cumulative_profit,
                        renovation_payback_pct, active_pool_size, total_acquired_users,
                        quiz_rate, send_drink_rate, radar_chat_rate, satisfaction_rate, event_tag
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scenario, r["day_index"], r["date_str"], r["city"],
                    r["day_visitors"], r["night_visitors"],
                    r["day_revenue"], r["night_revenue"], r["total_revenue"],
                    r["retention_rate"], r["new_users"], r["viral_k"],
                    r["online_impressions"], r["pricing_record"],
                    r["day_price"], r["night_price"],
                    r["daily_fixed_cost"], r["daily_cogs"],
                    r["daily_net_profit"], r["cumulative_profit"],
                    r["renovation_payback_pct"], r["active_pool_size"],
                    r["total_acquired_users"], r["quiz_rate"],
                    r["send_drink_rate"], r["radar_chat_rate"],
                    r["satisfaction_rate"], r["event_tag"]
                ))

            for d in decision_logs:
                cur.execute("""
                    INSERT INTO decision_logs (
                        scenario, week, day_index, city, old_day_price, new_day_price,
                        old_night_price, new_night_price, campaign_action, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scenario, d["week"], d["day_index"], d["city"],
                    d["old_day_price"], d["new_day_price"],
                    d["old_night_price"], d["new_night_price"],
                    d["campaign_action"], d["reason"]
                ))

            for u in user_samples:
                cur.execute("""
                    INSERT INTO user_samples (
                        scenario, day_index, city, sample_type, god_and_faction, content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    scenario, u["day_index"], u["city"],
                    u["sample_type"], u["god_and_faction"], u["content"]
                ))

            for c in cost_summary.get("logs", []):
                cur.execute("""
                    INSERT INTO llm_cost_logs (
                        scenario, call_index, day_index, city, task_type, provider,
                        prompt_tokens, completion_tokens, total_tokens, cost_rmb, cached, latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scenario, c["call_index"], c["day"], c["city"], c["task_type"],
                    c["provider"], c["prompt_tokens"], c["completion_tokens"],
                    c["total_tokens"], c["cost_rmb"], 1 if c["cached"] else 0, c["latency_ms"]
                ))

            cur.execute("""
                INSERT INTO scenario_verdicts (
                    scenario, verdict_json, weekly_reports_json, cost_summary_json, audit_log_json
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                scenario,
                json.dumps(milestone_verdicts, ensure_ascii=False),
                json.dumps(weekly_reports, ensure_ascii=False),
                json.dumps({k: v for k, v in cost_summary.items() if k != "logs"}, ensure_ascii=False),
                json.dumps(audit_log, ensure_ascii=False),
            ))
            conn.commit()
