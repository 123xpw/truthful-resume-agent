from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class Fact:
    id: str
    title: str
    keywords: tuple[str, ...]
    summary: str
    boundaries: tuple[str, ...]
    risk: str


# Facts are loaded from facts.json or facts.example.json. Keep the in-code
# fallback empty so a missing data file fails closed instead of reviving a
# stale duplicate fact bank.
FACTS: tuple[Fact, ...] = ()


NOT_WRITABLE_TECH: dict[str, str] = {
    "RAG": "No fact-bank project shows document parsing, chunking, embedding, retrieval, or vector search implementation.",
    "向量数据库": "No vector database implementation evidence exists in the fact bank.",
    "vector database": "No vector database implementation evidence exists in the fact bank.",
    "LangChain": "No current fact-bank record supports the requested LangChain claim.",
    "LlamaIndex": "No LlamaIndex project evidence exists in the fact bank.",
    "MCP": "No MCP project evidence exists in the fact bank.",
    "A2A": "No A2A protocol project evidence exists in the fact bank.",
    "Redis": "No Redis project evidence exists in the fact bank.",
    "RocketMQ": "No RocketMQ project evidence exists in the fact bank.",
    "Docker": "No Docker project evidence exists in the fact bank.",
    "FastAPI": "No FastAPI project evidence exists in the fact bank.",
    "Go": "No Go project evidence exists in the fact bank.",
    "Kotlin": "No Kotlin/Android project evidence exists in the fact bank.",
    "Android": "No Android project evidence exists in the fact bank.",
    "vLLM": "No inference-serving-framework evidence exists in the fact bank.",
    "Ollama": "No inference-serving-framework evidence exists in the fact bank.",
    "KV cache": "No inference-serving-optimization evidence exists in the fact bank.",
    "Transformer": "No fact-bank record supports a resume-level Transformer implementation or architecture claim.",
    "注意力机制": "No fact-bank record supports a resume-level attention-mechanism implementation claim.",
    "ONNX": "No direct ONNX model conversion, deployment, or optimization evidence exists in the fact bank.",
    "TFLite": "No TensorFlow Lite deployment evidence exists in the fact bank.",
    "NCNN": "No NCNN deployment evidence exists in the fact bank.",
    "MNN": "No MNN deployment evidence exists in the fact bank.",
    "ONNX Runtime": "No direct ONNX Runtime deployment or tuning evidence exists in the fact bank.",
    "端侧模型部署": "No edge-model deployment evidence exists in the fact bank.",
    "模型转换": "No model-conversion implementation evidence exists in the fact bank.",
    "量化压缩": "No model-quantization implementation evidence exists in the fact bank.",
    "推理加速": "No inference-acceleration implementation evidence exists in the fact bank.",
    "裁剪": "No model-pruning implementation evidence exists in the fact bank.",
    "蒸馏": "No knowledge-distillation implementation evidence exists in the fact bank.",
    "IoT": "No IoT-device implementation evidence exists in the fact bank.",
    "可穿戴": "No wearable-device implementation evidence exists in the fact bank.",
    "3D 打印": "No 3D-printing implementation or maker-project evidence exists in the fact bank.",
    "硬件项目": "No hands-on hardware-project evidence exists in the fact bank.",
    "MicroPython": "No MicroPython implementation evidence exists in the fact bank.",
    "MySQL": "No fact-bank project currently supports a resume-level MySQL implementation claim.",
    "分布式": "No distributed-system implementation evidence exists in the fact bank.",
    "distributed systems": "No distributed-system implementation evidence exists in the fact bank.",
    "高并发": "No high-concurrency implementation or load-test evidence exists in the fact bank.",
    "高可用": "No high-availability architecture or failover evidence exists in the fact bank.",
    "Kubernetes": "No Kubernetes deployment or operations evidence exists in the fact bank.",
    "K8s": "No Kubernetes deployment or operations evidence exists in the fact bank.",
    "消息队列": "No message-queue implementation evidence exists in the fact bank.",
    "message queue": "No message-queue implementation evidence exists in the fact bank.",
    "微调": "No model fine-tuning evidence exists in the fact bank.",
    "fine-tuning": "No model fine-tuning evidence exists in the fact bank.",
    "多智能体": "No multi-agent orchestration evidence exists in the fact bank.",
    "multi-agent": "No multi-agent orchestration evidence exists in the fact bank.",
    "SFT": "No supervised fine-tuning evidence exists in the fact bank.",
    "RL": "No reinforcement-learning training evidence exists in the fact bank.",
}


def term_matches(text: str, term: str) -> bool:
    if re.search(r"[一-鿿]", term):
        return term in text
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term.lower())}(?![A-Za-z0-9_])")
    return bool(pattern.search(text.lower()))


def has_fact_evidence(tech: str, facts: Iterable[Fact]) -> bool:
    """Does any fact in the bank actually back this technology term?

    This is what makes NOT_WRITABLE_TECH a real evidence check instead of a
    permanent denylist: a term only stays blocked while the fact bank has
    nothing that supports it. Add a fact with real evidence (e.g. a project
    that genuinely does retrieval-augmented generation), and that term
    should stop being flagged — this function is where that unblocking
    happens.
    """
    return any(term_matches(fact.summary, tech) or any(term_matches(kw, tech) for kw in fact.keywords) for fact in facts)


def find_not_writable(jd_text: str, facts: Iterable[Fact]) -> dict[str, str]:
    """Not-writable technologies mentioned in the JD, filtered to only those
    the fact bank still has no evidence for. `facts` must be passed in
    (not loaded here) so keyword and semantic callers share one fact-bank
    snapshot per analysis instead of re-reading the file per call.
    """
    facts = list(facts)
    unsupported: dict[str, str] = {}
    for tech, reason in NOT_WRITABLE_TECH.items():
        if term_matches(jd_text, tech) and not has_fact_evidence(tech, facts):
            unsupported[tech] = reason
    return unsupported


JOB_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI product / product management", ("产品经理", "产品设计", "用户体验", "产品需求", "原型设计", "产品 Demo")),
    ("AI application / Agent engineering", ("Agent", "大模型", "LLM", "Prompt", "智能对话", "内容生成", "知识问答", "AI应用")),
    ("Data application / data engineering", ("ETL", "SQL", "数据建模", "数据服务", "数据湖", "数据分析", "特征工程")),
    ("Backend / platform engineering", ("后端", "服务端", "高并发", "高可用", "接口", "中间件")),
    ("Algorithm / multimodal research", ("算法", "训练", "模型训练", "计算机视觉", "NLP", "多模态", "论文")),
    ("Product / operations", ("运营", "KOL", "社媒", "内容产出", "用户增长")),
)
