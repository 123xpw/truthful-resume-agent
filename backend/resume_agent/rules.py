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


FACTS: tuple[Fact, ...] = (
    Fact(
        id="intern_optimization_ai_coding",
        title="Optimization internship: AI Coding for complex codebase analysis",
        keywords=(
            "Claude Code",
            "Codex",
            "AI Coding",
            "AI programming tools",
            "complex codebase",
            "documentation",
            "verification",
            "requirement analysis",
        ),
        summary=(
            "Used Claude Code and Codex to assist complex codebase search and documentation drafts; "
            "manually verified formula meaning, variable names, and source-code locations."
        ),
        boundaries=(
            "AI tools assisted retrieval and drafting; final verification was manual.",
            "Do not claim independent implementation of all mathematical constraints.",
        ),
        risk="medium",
    ),
    Fact(
        id="intern_data_automation",
        title="Data automation internship: REST API and Excel workflow",
        keywords=("Python", "REST API", "ETL", "Excel", "multi-sheet", "cloud schedule", "data processing"),
        summary=(
            "Built a Python data automation workflow using REST API, token-based login, selected index data retrieval, "
            "average calculation, multi-sheet Excel filling, and cloud scheduled execution."
        ),
        boundaries=(
            "No independent alerting system.",
            "No pagination or rate-limit handling.",
            "No quantified efficiency improvement unless measured later.",
        ),
        risk="medium",
    ),
    Fact(
        id="intern_csharp_ai_mvp",
        title="C# internship: multimodal warehouse recommendation MVP",
        keywords=("C#", "Selenium", "multimodal", "Prompt", "JSON", "business rules", "MVP"),
        summary=(
            "Built a C# MVP that uses Selenium/browser automation to submit item images to a multimodal model page, "
            "constrains JSON output with prompts, and applies business rules for storage-type recommendation."
        ),
        boundaries=(
            "Did not train a vision model.",
            "Do not describe this as official model API integration unless code confirms it.",
            "MVP was usable for demo and small tests, not proven production-grade.",
        ),
        risk="medium",
    ),
    Fact(
        id="project_chinese_learning_mvp",
        title="AI Chinese learning mini program MVP",
        keywords=(
            "Cursor",
            "AI programming tools",
            "mini-program",
            "MVP",
            "AI application",
            "user-facing",
            "interface",
            "database fields",
            "Coze",
            "Agent",
            "intelligent dialogue",
            "Prompt",
        ),
        summary=(
            "Used Cursor to convert UI designs into mini-program demo pages; understood basic login/registration "
            "interfaces and database fields; used Claude-generated prompts and Coze for demo agents."
        ),
        boundaries=(
            "Later completion involved external development support.",
            "Do not claim full production system ownership.",
            "Do not claim complex custom Agent architecture.",
        ),
        risk="medium",
    ),
    Fact(
        id="project_emotion_pixel_eval",
        title="Emotion pixel visualization: LLM pipeline and CLIP evaluation",
        keywords=(
            "DeepSeek API",
            "model API",
            "Prompt Engineering",
            "JSON",
            "content generation",
            "effect evaluation",
            "CLIP",
            "evaluation",
            "ablation",
        ),
        summary=(
            "Built a pipeline from Chinese text to emotion JSON to visual generation conditions; designed prompt "
            "compression; evaluated image-text alignment with CLIP ViT-B/32 across Baseline, Partial, and Full settings."
        ),
        boundaries=(
            "Application/evaluation project, not large-scale model training.",
            "Evaluation rigor depends on sample size and experiment details.",
        ),
        risk="high",
    ),
    Fact(
        id="project_dl_learning_lab",
        title="DL-Learning-Lab: deep learning and visual generation learning project",
        keywords=("PyTorch", "DDPM", "CFG", "Stable Diffusion LoRA", "SegFormer", "algorithm reproduction"),
        summary=(
            "Completed PyTorch experiments covering DDPM/CFG, Stable Diffusion LoRA, SegFormer, and related notes."
        ),
        boundaries=(
            "Learning/open-source practice, not a research publication or enterprise production result.",
        ),
        risk="high",
    ),
)


NOT_WRITABLE_TECH: dict[str, str] = {
    "RAG": "No fact-bank project shows document parsing, chunking, embedding, retrieval, or vector search implementation.",
    "向量数据库": "No vector database implementation evidence exists in the fact bank.",
    "vector database": "No vector database implementation evidence exists in the fact bank.",
    "LangChain": "No LangChain project evidence exists in the fact bank.",
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
    ("AI application / Agent engineering", ("Agent", "大模型", "LLM", "Prompt", "智能对话", "内容生成", "知识问答", "AI应用")),
    ("Data application / data engineering", ("ETL", "SQL", "数据建模", "数据服务", "数据湖", "数据分析", "特征工程")),
    ("Backend / platform engineering", ("后端", "服务端", "高并发", "高可用", "接口", "中间件")),
    ("Algorithm / multimodal research", ("算法", "训练", "模型训练", "计算机视觉", "NLP", "多模态", "论文")),
    ("Product / operations", ("运营", "KOL", "社媒", "内容产出", "用户增长")),
)
