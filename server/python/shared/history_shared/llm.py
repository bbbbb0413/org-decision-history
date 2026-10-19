"""LLM 호출 공통 예외.

LLM 이 일시적으로 응답할 수 없는 상황(요청 한도 429, 서버 오류 5xx, 타임아웃·연결 실패)은
"결과 없음"과 다르다. 이걸 폴백이나 빈 결과로 삼키면 상충을 조용히 놓치거나 엉뚱한 조항을 판정한다.
호출부는 이 예외를 잡지 말고 올려서, 스트림 재전달(재시도)에 맡긴다.
"""
import httpx


class LlmUnavailableError(Exception):
    """LLM 이 일시적으로 응답할 수 없다. 잠시 뒤 다시 시도하면 될 수 있다."""


def is_transient(error: BaseException) -> bool:
    """다시 시도하면 될 수 있는 LLM 호출 실패인지. 요청 자체가 잘못된 4xx(429 제외)는 아니다."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError))
