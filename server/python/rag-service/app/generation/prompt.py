"""답변 프롬프트를 만든다.

토큰 예산을 지킨다. 기본 예산은 시스템 400, 대화 요약 300, 최근 대화 600, 청크 2,500,
질문 100이고 답변 여유(max_output_tokens) 1,500을 더해 5,400이다. 예산을 넘으면 청크를
뒤에서부터 덜어낸다 — 순위가 낮은 근거를 먼저 버리는 것이다. 예산 값은 설정(Settings.prompt_budget)
에서 오고, 합계가 요청 한도를 넘으면 기동 시 실패한다.
"""
from dataclasses import dataclass

from app.generation import token_estimator
from app.search.models import SearchHit
from app.prompt_files import prompt


@dataclass(frozen=True)
class PromptBudget:
    system: int = 400
    summary: int = 300
    # [최근 대화] 블록 전체 예산.
    #
    # 이 블록에 예산이 없으면 직전 턴의 본문이 통째로 들어간다. 2만자짜리 질문 하나가 들어간
    # 대화에서 이후 모든 요청이 21,000 토큰을 넘겨 Groq 의 8,000 TPM 한도에 413 으로 막히고,
    # 그 대화는 계속 빈 답변만 낸다. 질문 길이는 AskRequest 에서도 막지만(@Size 2000),
    # 요약·과거 데이터가 길 수 있으므로 여기서도 상한을 둔다.
    recent_turns: int = 600
    # 한 턴이 블록 전체를 먹지 않게 한다. 오래된 턴이 통째로 밀려나는 것보다
    # 각 턴의 앞부분이라도 남는 쪽이 맥락 유지에 낫다.
    recent_turn: int = 200
    evidence: int = 2500
    question: int = 100
    # 프롬프트에 넣는 근거 최대 수. 리랭커가 남기는 수와 같은 값이다(Settings.evidence_count)
    evidence_count: int = 5

    def input_total(self) -> int:
        return self.system + self.summary + self.recent_turns + self.evidence + self.question


DEFAULT_BUDGET = PromptBudget()

# 본문은 prompts/answer_system.md
SYSTEM_PROMPT = prompt("answer_system")


def trim_evidence(hits: list[SearchHit], budget: PromptBudget = DEFAULT_BUDGET) -> list[SearchHit]:
    """예산 안에 들어가는 근거만 남긴다."""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits[:budget.evidence_count]:
        tokens = token_estimator.estimate(hit.content)
        if kept and used + tokens > budget.evidence:
            break
        kept.append(hit)
        used += tokens
    return kept


def trim_turns(recent_turns: list[str], budget: PromptBudget = DEFAULT_BUDGET) -> list[str]:
    """[최근 대화] 블록을 예산 안으로 줄인다.

    입력은 오래된 순이다(db.recent_turns). 예산이 모자라면 오래된 턴부터 버린다 —
    직전 턴이 대화 맥락에 가장 중요하기 때문이다. 반환값도 오래된 순을 유지한다.
    """
    kept: list[str] = []
    used = 0
    for turn in reversed(recent_turns):
        clipped = _clip(turn, budget.recent_turn)
        if not clipped:
            continue
        tokens = token_estimator.estimate(clipped)
        if kept and used + tokens > budget.recent_turns:
            break
        kept.append(clipped)
        used += tokens
    kept.reverse()
    return kept


def build_user_prompt(
    question: str,
    summary: str | None,
    recent_turns: list[str],
    evidence: list[SearchHit],
    budget: PromptBudget = DEFAULT_BUDGET,
) -> str:
    parts = []
    if summary and summary.strip():
        parts.append(f"[이전 대화 요약]\n{_clip(summary, budget.summary)}\n")
    kept_turns = trim_turns(recent_turns, budget)
    if kept_turns:
        turns_text = "\n".join(f"- {t}" for t in kept_turns)
        parts.append(f"[최근 대화]\n{turns_text}\n")

    evidence_lines = [f"[{i + 1}] {hit.content.strip()}\n" for i, hit in enumerate(evidence)]
    parts.append("[근거]\n" + "\n".join(evidence_lines))
    parts.append(f"[질문]\n{_clip(question, budget.question)}")
    return "\n".join(parts)


def _clip(text: str | None, budget_tokens: int) -> str:
    if not text:
        return ""
    if token_estimator.estimate(text) <= budget_tokens:
        return text
    approx_chars = int(budget_tokens / 0.7)
    return text[:approx_chars] + "…" if len(text) > approx_chars else text
