from ets_demo.ise import parse_scores
from ets_demo.scoring import ScoreProfile, build_score_detail, convert_score, generate_score_xml


def test_generated_xml_round_trips_scores() -> None:
    profile = ScoreProfile(total=4.8, accuracy=4.7, fluency=4.6, integrity=4.9, stress=4.5)
    xml = generate_score_xml("Hello world.", profile, seed=1)
    scores = parse_scores(xml)
    assert scores["total_score"] == 4.8
    assert scores["accuracy_score"] == 4.7
    assert scores["stress_score"] == 4.5


def test_score_detail_and_conversion() -> None:
    profile = ScoreProfile(total=4.0, accuracy=4.1, fluency=4.2, integrity=4.3, stress=4.4)
    detail = build_score_detail(profile, category="read_sentence", choose="B")
    assert '"choose":"B"' in detail
    assert convert_score(4.0, 10.0) == 8.0


def test_convert_score_without_cap_allows_over_max() -> None:
    assert convert_score(6.0, 10.0) == 10.0
    assert convert_score(6.0, 10.0, cap=False) == 12.0
