import base64
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from ets_demo.config import IseConfig
from ets_demo.ise import build_auth_url, parse_scores


def test_auth_url_contains_decodable_authorization() -> None:
    config = IseConfig("app", "key", "secret")
    url = build_auth_url(config, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    query = parse_qs(urlparse(url).query)
    auth = base64.b64decode(query["authorization"][0]).decode()
    assert 'username="key"' in auth
    assert 'algorithm="hmac-sha256"' in auth
    assert query["host"] == ["ise-api-dz-ets.xf-yun.com"]


def test_parse_scores() -> None:
    xml = '<root><read_sentence total_score="4.8" fluency_score="4.7" accuracy_score="4.6" integrity_score="5" /></root>'
    scores = parse_scores(xml)
    assert scores["total_score"] == 4.8
    assert scores["integrity_score"] == 5.0
    assert scores["stress_score"] == 0.0
