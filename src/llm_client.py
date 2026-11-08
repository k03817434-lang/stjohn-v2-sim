"""
ST.JOHN v2 - LLM 网关与成本 Agent (Cost Agent)
规则引擎跑 90% 日常行为，LLM 只干三件事：
1. 每周调价与运营决策（13次）
2. 抽 10% 用户生成真实对话 / 发帖（每周2次 × 12.5 ≈ 25次）
3. 最后写总结（1次）
总计约 39 次调用，成本不到 0.1 元人民币，全部由成本 Agent 记录到 SQLite 与内存账本。
同时配备基于 Prompt Hash 的确定性本地缓存，保证相同随机种子重跑两次结果 100% 一致，且不重复扣费。
"""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "llm_cache.json"


class CostAgentAndLLMClient:
    """
    检查 Agent 之二：成本 Agent + DeepSeek / Qwen 统一调用客户端
    """
    # DeepSeek V3 / Qwen-Turbo/Plus 价格标准（元 / 百万 Token）
    # DeepSeek-chat: 输入 1~2 元/M tokens (缓存命中0.5元/M)，输出 2~8 元/M tokens
    # 按标准均价折算：Input 0.001 元 / 1K tokens, Output 0.002 元 / 1K tokens
    PRICE_PER_1K_INPUT_RMB = 0.001
    PRICE_PER_1K_OUTPUT_RMB = 0.002

    def __init__(self, use_cache: bool = True):
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        self.qwen_key = os.getenv("QWEN_API_KEY", "")
        self.qwen_base = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.qwen_model = os.getenv("QWEN_MODEL", "qwen-plus")

        self.use_cache = use_cache
        self.cache: Dict[str, dict] = self._load_cache()
        self.call_logs: List[dict] = []

    def _load_cache(self) -> Dict[str, dict]:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def reset_ledger(self):
        self.call_logs = []

    def call_llm(
        self,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        day: int,
        city: str = "全局",
        max_tokens: int = 350,
        temperature: float = 0.0,
    ) -> str:
        """
        调用 DeepSeek（主）或 Qwen（备），并由成本 Agent 记账。
        """
        cache_key = hashlib.sha256(f"{task_type}|{system_prompt}|{user_prompt}".encode("utf-8")).hexdigest()

        if self.use_cache and cache_key in self.cache:
            cached = self.cache[cache_key]
            record = {
                "call_index": len(self.call_logs) + 1,
                "day": day,
                "city": city,
                "task_type": task_type,
                "provider": cached.get("provider", "deepseek-chat (cached)"),
                "prompt_tokens": cached["prompt_tokens"],
                "completion_tokens": cached["completion_tokens"],
                "total_tokens": cached["total_tokens"],
                "cost_rmb": cached["cost_rmb"],
                "cached": True,
                "latency_ms": 1,
            }
            self.call_logs.append(record)
            return cached["content"]

        # 优先调用 DeepSeek API，若失败则自动切换 Qwen API
        providers = []
        if self.deepseek_key:
            providers.append(("DeepSeek", self.deepseek_key, self.deepseek_base, self.deepseek_model))
        if self.qwen_key:
            providers.append(("Qwen", self.qwen_key, self.qwen_base, self.qwen_model))

        last_err = None
        for provider_name, api_key, base_url, model_name in providers:
            try:
                t0 = time.time()
                client = OpenAI(api_key=api_key, base_url=base_url, timeout=25.0)
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                latency_ms = int((time.time() - t0) * 1000)
                content = resp.choices[0].message.content.strip()
                p_tokens = getattr(resp.usage, "prompt_tokens", 180) or 180
                c_tokens = getattr(resp.usage, "completion_tokens", 120) or 120
                t_tokens = p_tokens + c_tokens
                cost_rmb = round(
                    (p_tokens / 1000.0) * self.PRICE_PER_1K_INPUT_RMB
                    + (c_tokens / 1000.0) * self.PRICE_PER_1K_OUTPUT_RMB,
                    6,
                )

                entry = {
                    "provider": f"{provider_name}/{model_name}",
                    "content": content,
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": t_tokens,
                    "cost_rmb": cost_rmb,
                }
                self.cache[cache_key] = entry
                self._save_cache()

                record = {
                    "call_index": len(self.call_logs) + 1,
                    "day": day,
                    "city": city,
                    "task_type": task_type,
                    "provider": f"{provider_name}/{model_name}",
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": t_tokens,
                    "cost_rmb": cost_rmb,
                    "cached": False,
                    "latency_ms": latency_ms,
                }
                self.call_logs.append(record)
                return content
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(f"LLM API 调用失败（DeepSeek/Qwen 均不可用）: {last_err}")

    def get_summary(self) -> dict:
        total_calls = len(self.call_logs)
        total_prompt = sum(x["prompt_tokens"] for x in self.call_logs)
        total_completion = sum(x["completion_tokens"] for x in self.call_logs)
        total_tokens = sum(x["total_tokens"] for x in self.call_logs)
        total_cost = round(sum(x["cost_rmb"] for x in self.call_logs), 5)
        by_task = {}
        for x in self.call_logs:
            t = x["task_type"]
            if t not in by_task:
                by_task[t] = {"calls": 0, "tokens": 0, "cost_rmb": 0.0}
            by_task[t]["calls"] += 1
            by_task[t]["tokens"] += x["total_tokens"]
            by_task[t]["cost_rmb"] = round(by_task[t]["cost_rmb"] + x["cost_rmb"], 5)

        return {
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "total_cost_rmb": total_cost,
            "within_budget_0_1_rmb": total_cost < 0.10,
            "by_task": by_task,
            "logs": self.call_logs,
        }
