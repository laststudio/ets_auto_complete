import hashlib

from ets_demo.homework import clean_html, derive_zip_password, extract_questions


def test_clean_html() -> None:
    assert clean_html("<p>Hello<br>world &amp; all</p>") == "Hello world & all"


def test_zip_password_derivation() -> None:
    seed = bytes(range(128))
    footer = bytearray(336)
    footer[0:8] = b"MSTCHINA"
    footer[16:144] = seed
    footer[144:149] = b"EPLAT"
    first = hashlib.md5(seed, usedforsecurity=False).hexdigest().upper()
    second = hashlib.md5(first.encode(), usedforsecurity=False).hexdigest().upper()
    assert derive_zip_password(b"zip" + bytes(footer)) == first + second


def test_extract_choose_questions() -> None:
    content = {
        "structure_type": "collector.choose",
        "info": {
            "xtlist": [
                {
                    "answer": "2",
                    "xxlist": [
                        {"xx_xh": "1", "xx_nr": "Wrong"},
                        {"xx_xh": "2", "xx_nr": "<b>Right</b>"},
                    ],
                }
            ]
        },
    }
    assert extract_questions(content) == [
        {
            "entity_id": "",
            "order": 0,
            "text": "Right",
            "category": "choose",
            "structure_type": "collector.choose",
            "correct_option": "2",
        }
    ]
