"""[최근 대화] 블록 토큰 예산.

예산이 없으면 긴 질문 하나가 이후 모든 요청의 프롬프트를 부풀려, Groq 의 분당 토큰
한도(413)에 막혀 그 대화가 계속 빈 답변을 낸다.
"""
from app.generation import token_estimator
from app.generation.prompt import (
    RECENT_TURN_BUDGET,
    RECENT_TURNS_BUDGET,
    build_user_prompt,
    trim_turns,
)


def test_짧은_턴은_그대로_남는다():
    turns = ["배포 주기를 언제로 바꾸나요?", "2주에서 1주로 바꾸기로 했습니다."]
    assert trim_turns(turns) == turns


def test_한_턴이_너무_길면_잘린다():
    long_turn = "가" * 20_000
    kept = trim_turns([long_turn])

    assert len(kept) == 1
    assert token_estimator.estimate(kept[0]) <= RECENT_TURN_BUDGET
    assert kept[0].endswith("…")


def test_블록_전체가_예산을_넘지_않는다():
    turns = ["나" * 5_000 for _ in range(4)]
    kept = trim_turns(turns)

    total = sum(token_estimator.estimate(t) for t in kept)
    assert total <= RECENT_TURNS_BUDGET


def test_예산이_모자라면_오래된_턴부터_버린다():
    # 각 턴이 한 턴 상한(200토큰)을 꽉 채우게 만든다 → 블록 예산 600 에 세 개만 들어간다.
    turns = [f"{label} " + "다" * 1_000 for label in ("첫째", "둘째", "셋째", "넷째")]
    kept = trim_turns(turns)

    labels = [t.split(" ", 1)[0] for t in kept]
    assert "첫째" not in labels, "예산이 모자라면 가장 오래된 턴부터 버려야 한다"
    assert labels[-1] == "넷째", "직전 턴은 반드시 남아야 한다"
    assert labels == sorted(labels, key=["첫째", "둘째", "셋째", "넷째"].index), "오래된 순을 유지해야 한다"


def test_빈_목록은_블록_자체를_만들지_않는다():
    prompt = build_user_prompt("질문입니다", None, [], [])
    assert "[최근 대화]" not in prompt


def test_긴_턴이_있어도_프롬프트에_그대로_들어가지_않는다():
    prompt = build_user_prompt("질문입니다", None, ["마" * 20_000], [])
    assert "[최근 대화]" in prompt
    assert token_estimator.estimate(prompt) < 1_000
