"""답변 프롬프트를 만든다.

토큰 예산을 지킨다. 예산은 시스템 400, 대화 요약 300, 최근 대화 600, 청크 2,500,
질문 100, 답변 여유 1,500으로 합계 5,400을 넘지 않는다. 예산을 넘으면 청크를
뒤에서부터 덜어낸다 — 순위가 낮은 근거를 먼저 버리는 것이다.
"""
from app.generation import token_estimator
from app.search.models import SearchHit

SYSTEM_BUDGET = 400
SUMMARY_BUDGET = 300
EVIDENCE_BUDGET = 2500
QUESTION_BUDGET = 100
# [최근 대화] 블록 전체 예산.
#
# 예산이 없으면 직전 턴의 본문이 통째로 들어가, 긴 질문 하나가 이후 모든 요청을
# Groq 의 분당 토큰 한도(413) 밖으로 밀어낸다. 질문 길이는 AskRequest 에서도 막지만
# (@Size 2000), 요약·과거 데이터가 길 수 있으므로 여기서도 상한을 둔다.
RECENT_TURNS_BUDGET = 600
# 한 턴이 블록 전체를 먹지 않게 한다. 오래된 턴이 통째로 밀려나는 것보다
# 각 턴의 앞부분이라도 남는 쪽이 맥락 유지에 낫다.
RECENT_TURN_BUDGET = 200
# 기본으로 상위 5개 청크를 쓴다
DEFAULT_EVIDENCE_COUNT = 5

SYSTEM_PROMPT = """너는 사내 문서를 근거로 답하는 조수다. 아래 규칙을 지킨다.

1. 제공된 근거에 있는 내용만 답한다. 근거에 없으면 모른다고 말한다.
2. 사실과 추론을 구분한다. 추론이면 "근거상 추정하면" 처럼 밝힌다.
3. 각 문장 끝에 [1] [2] 처럼 근거 번호를 붙인다.
4. 근거끼리 어긋나면 어느 쪽이 맞다고 단정하지 말고 둘 다 제시한다.
5. 최신 문서를 무조건 정답으로 보지 않는다. 작성일과 결정 상태를 함께 말한다.
6. 한국어로 답한다. 서술어로 문장을 끝맺는다.
"""


def trim_evidence(hits: list[SearchHit]) -> list[SearchHit]:
    """예산 안에 들어가는 근거만 남긴다."""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits[:DEFAULT_EVIDENCE_COUNT]:
        tokens = token_estimator.estimate(hit.content)
        if kept and used + tokens > EVIDENCE_BUDGET:
            break
        kept.append(hit)
        used += tokens
    return kept


def trim_turns(recent_turns: list[str]) -> list[str]:
    """[최근 대화] 블록을 예산 안으로 줄인다.

    입력은 오래된 순이다(db.recent_turns). 예산이 모자라면 오래된 턴부터 버린다 —
    직전 턴이 대화 맥락에 가장 중요하기 때문이다. 반환값도 오래된 순을 유지한다.
    """
    kept: list[str] = []
    used = 0
    for turn in reversed(recent_turns):
        clipped = _clip(turn, RECENT_TURN_BUDGET)
        if not clipped:
            continue
        tokens = token_estimator.estimate(clipped)
        if kept and used + tokens > RECENT_TURNS_BUDGET:
            break
        kept.append(clipped)
        used += tokens
    kept.reverse()
    return kept


def build_user_prompt(
    question: str, summary: str | None, recent_turns: list[str], evidence: list[SearchHit]
) -> str:
    parts = []
    if summary and summary.strip():
        parts.append(f"[이전 대화 요약]\n{_clip(summary, SUMMARY_BUDGET)}\n")
    kept_turns = trim_turns(recent_turns)
    if kept_turns:
        turns_text = "\n".join(f"- {t}" for t in kept_turns)
        parts.append(f"[최근 대화]\n{turns_text}\n")

    evidence_lines = [f"[{i + 1}] {hit.content.strip()}\n" for i, hit in enumerate(evidence)]
    parts.append("[근거]\n" + "\n".join(evidence_lines))
    parts.append(f"[질문]\n{_clip(question, QUESTION_BUDGET)}")
    return "\n".join(parts)


def _clip(text: str | None, budget_tokens: int) -> str:
    if not text:
        return ""
    if token_estimator.estimate(text) <= budget_tokens:
        return text
    approx_chars = int(budget_tokens / 0.7)
    return text[:approx_chars] + "…" if len(text) > approx_chars else text
