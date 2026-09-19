import asyncio
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ets_demo.config import InterfaceConfig
from ets_demo.interface import InterfaceClient
from ets_demo.mock_server import MockState, make_handler
from ets_demo.models import Homework, Question
from ets_demo.workflow import StudyWorkflow, WorkflowOptions


def _run_with_mock(homework: Homework, output_dir: Path) -> tuple[dict, list[str]]:
    state = MockState(homeworks=[homework], pid="branch-pid", secret="branch-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with InterfaceClient(
            InterfaceConfig(
                api_base_url=f"http://{host}:{port}", pid=state.pid, secret_key=state.secret
            )
        ) as client:
            report = asyncio.run(
                StudyWorkflow(
                    WorkflowOptions(output_dir=output_dir, send_interface=True),
                    interface=client,
                ).run(homework)
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return report, [event["route"] for event in state.events]


@pytest.mark.parametrize(
    ("kind", "expected_route"),
    (("pk", "g/pk/submit-word"), ("vocab", "g/homework/score-sync")),
)
def test_pk_and_vocab_branches(tmp_path: Path, kind: str, expected_route: str) -> None:
    homework = Homework(
        id=f"{kind}-1",
        name=f"{kind} practice",
        kind=kind,
        questions=[
            Question(
                entity_id="word-1",
                order=0,
                text="apple",
                category="choose",
                structure_type="word_question",
                word_id=1,
                question_id=11,
                question_type=6,
                answer="B",
                options=[
                    {"option_name": "A", "option_value": "banana"},
                    {"option_name": "B", "option_value": "apple"},
                ],
            )
        ],
    )
    report, routes = _run_with_mock(homework, tmp_path / kind)
    assert report["summary"]["kind"] == kind
    assert expected_route in routes
    assert routes[-1] == "g/homework/report-score"


def test_mock_exam_branch(tmp_path: Path) -> None:
    homework = Homework(
        id="mock-1",
        name="Mock exam",
        kind="mock",
        total_point=20,
        set_id="exam-1",
        questions=[
            Question(
                entity_id="choice-1",
                order=0,
                text="Answer",
                category="choose",
                structure_type="collector.choose",
                correct_option="A",
            )
        ],
    )
    report, routes = _run_with_mock(homework, tmp_path / "mock")
    assert report["steps"]["exam_sync"]["body"]["accepted"] is True
    assert routes[-2:] == ["m/exam/sync", "m/exam/submit"]
    assert "m/audio/sync-v2" not in routes
    assert "g/homework/report-score" not in routes


def test_composition_branch(tmp_path: Path) -> None:
    homework = Homework(
        id="composition-1",
        name="Composition",
        kind="composition",
        questions=[
            Question(
                entity_id="essay-1",
                order=0,
                question_id=31,
                text="Prompt",
                answer="Local draft text.",
                category="composition",
                structure_type="composition",
            )
        ],
    )
    report, routes = _run_with_mock(homework, tmp_path / "composition")
    assert report["summary"]["characters"] == len("Local draft text.")
    assert routes == [
        "m/composition/save-draft",
        "m/composition/mark",
        "m/homework/submit",
    ]


def _normal_homework() -> Homework:
    return Homework(
        id="normal-1",
        name="Normal",
        total_point=30,
        questions=[
            Question(entity_id="q1", order=0, text="Hello", category="read_sentence"),
            Question(entity_id="q2", order=1, text="World", category="read_sentence"),
        ],
    )


def test_target_total_above_max_is_clamped_by_default(tmp_path: Path) -> None:
    state = MockState(homeworks=[_normal_homework()], pid="branch-pid", secret="branch-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with InterfaceClient(
            InterfaceConfig(
                api_base_url=f"http://{host}:{port}", pid=state.pid, secret_key=state.secret
            )
        ) as client:
            report = asyncio.run(
                StudyWorkflow(
                    WorkflowOptions(
                        output_dir=tmp_path / "clamped",
                        send_interface=True,
                        target_total=10000,
                    ),
                    interface=client,
                ).run(_normal_homework())
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert report["summary"]["score"] == 30.0


def test_allow_over_max_keeps_large_target_total(tmp_path: Path) -> None:
    state = MockState(homeworks=[_normal_homework()], pid="branch-pid", secret="branch-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with InterfaceClient(
            InterfaceConfig(
                api_base_url=f"http://{host}:{port}", pid=state.pid, secret_key=state.secret
            )
        ) as client:
            report = asyncio.run(
                StudyWorkflow(
                    WorkflowOptions(
                        output_dir=tmp_path / "over",
                        send_interface=True,
                        target_total=10000,
                        allow_over_max=True,
                    ),
                    interface=client,
                ).run(_normal_homework())
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert report["summary"]["score"] == 10000
    assert report["summary"]["maximum"] == 30.0
