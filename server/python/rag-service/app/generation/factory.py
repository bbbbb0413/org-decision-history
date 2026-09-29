"""API 키가 있으면 Groq를, 없으면 근거 발췌 방식을 쓴다.

키가 없다고 질의응답 전체를 막으면 검색과 출처 흐름을 확인할 수 없기 때문이다.
"""
from app.generation.base import AnswerGenerator
from app.generation.extractive import ExtractiveAnswerGenerator
from app.generation.groq_client import GroqAnswerGenerator
from app.settings import Settings


def build_generator(settings: Settings) -> AnswerGenerator:
    if settings.has_groq_key:
        return GroqAnswerGenerator(settings)
    return ExtractiveAnswerGenerator()
