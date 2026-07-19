from __future__ import annotations

from recall.classify.factory import get_question_classifier
from recall.classify.question_type import KeywordQuestionClassifier, QuestionType


def test_visual_question_detected_for_book_title() -> None:
    clf = KeywordQuestionClassifier()
    result = clf.classify("어제 민수가 보여준 책 제목이 뭐였지?")
    assert result.question_type is QuestionType.VISUAL
    assert "제목" in result.matched_keywords


def test_conversational_question_for_budget() -> None:
    clf = KeywordQuestionClassifier()
    result = clf.classify("어제 민수랑 제주도 여행 얘기했을 때 숙소 예산 얼마로 정했지?")
    assert result.question_type is QuestionType.CONVERSATIONAL
    assert result.matched_keywords == ()


def test_conversational_question_for_task_request() -> None:
    clf = KeywordQuestionClassifier()
    result = clf.classify("아까 민수가 나한테 부탁한 거 뭐였지?")
    assert result.question_type is QuestionType.CONVERSATIONAL


def test_factory_default_provider_is_keyword() -> None:
    clf = get_question_classifier()
    assert isinstance(clf, KeywordQuestionClassifier)


def test_factory_unknown_provider_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        get_question_classifier("no-such-provider")
