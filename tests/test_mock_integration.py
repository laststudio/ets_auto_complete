import asyncio
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from ets_demo.config import InterfaceConfig
from ets_demo.interface import InterfaceClient
from ets_demo.mock_server import MockState, make_handler
from ets_demo.models import Homework, Question
from ets_demo.workflow import StudyWorkflow, WorkflowOptions


def test_complete_workflow_against_loopback_mock(tmp_path: Path) -> None:
    homework = Homework(
        id="integration-1",
        name="Integration practice",
        total_point=20,
        questions=[
            Question(
                entity_id="choice-1",
                order=0,
                text="First answer",
                category="choose",
                structure_type="collector.choose",
                correct_option="A",
            ),
            Question(
                entity_id="choice-2",
                order=1,
                text="Second answer",
                category="choose",
                structure_type="collector.choose",
                correct_option="B",
            ),
        ],
    )
    state = MockState(homeworks=[homework], pid="test-pid", secret="test-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address
    config = InterfaceConfig(
        api_base_url=f"http://{host}:{port}", pid=state.pid, secret_key=state.secret
    )
    try:
        with InterfaceClient(config) as client:
            report = asyncio.run(
                StudyWorkflow(
                    WorkflowOptions(
                        output_dir=tmp_path,
                        send_interface=True,
                        session_id="integration-session",
                    ),
                    interface=client,
                ).run(homework)
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert report["summary"]["score"] == 20.0
    assert all(item["interface_response"]["body"]["accepted"] for item in report["results"])
    routes = [event["route"] for event in state.events]
    assert routes == [
        "g/homework/checkstart",
        "g/set/ppr-res",
        "m/file/pre-upload",
        "m/file/pre-upload",
        "m/audio/sync-v2",
        "m/file/pre-upload",
        "m/file/pre-upload",
        "m/audio/sync-v2",
        "m/homework/set-use-time",
        "g/homework/report-score",
    ]
