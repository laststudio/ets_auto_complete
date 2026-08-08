import asyncio
from pathlib import Path

from ets_demo.models import Homework, Question
from ets_demo.scoring import ScoreProfile
from ets_demo.workflow import StudyWorkflow, WorkflowOptions


def test_choice_only_workflow_writes_report(tmp_path: Path) -> None:
    homework = Homework(
        id="local-1",
        name="Choice practice",
        total_point=20,
        questions=[
            Question(
                entity_id="q1",
                order=0,
                text="Correct answer",
                category="choose",
                structure_type="collector.choose",
                correct_option="B",
            ),
            Question(
                entity_id="q2",
                order=1,
                text="Another answer",
                category="choose",
                structure_type="collector.choose",
                correct_option="A",
            ),
        ],
    )
    options = WorkflowOptions(
        output_dir=tmp_path,
        score_profile=ScoreProfile(total=4.0, accuracy=4.0, fluency=4.0, integrity=4.0),
    )
    report = asyncio.run(StudyWorkflow(options).run(homework))
    assert report["summary"]["score"] == 16.0
    assert report["summary"]["questions"] == 2
    assert (tmp_path / "report.json").is_file()
    assert len(list((tmp_path / "xml").glob("*.xml"))) == 2
