import pytest

from evaluation.golden import validate_gold_rows, vocabulary_violations


def test_gold_contract_distinguishes_media_and_text_answers() -> None:
    rows = [
        {"query_id": "q_text", "expected_action": "return_result", "answer_is_media": False,
         "gold_answer": "The meeting is tomorrow.", "gold_media_ids": []},
        {"query_id": "q_media", "expected_action": "return_result", "answer_is_media": True,
         "gold_answer": "", "gold_media_ids": ["m_1"]},
    ]
    assert validate_gold_rows(rows) == rows
    with pytest.raises(ValueError, match="media answers"):
        validate_gold_rows([{**rows[1], "gold_answer": "incorrect"}])


def test_vocabulary_gate_reads_query_and_gold_answer() -> None:
    violations = vocabulary_violations([
        {"query_id": "q_1", "query": "Show Session 4", "gold_answer": ""},
        {"query_id": "q_2", "query": "What happened?", "gold_answer": "It was in 12.png."},
    ])
    assert set(violations) == {"q_1", "q_2"}
