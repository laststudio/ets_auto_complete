from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .audio import synthesize_clone, synthesize_edge, synthesize_http
from .config import CloneTtsConfig, HttpTtsConfig, IseConfig
from .ets import EtsClient
from .interface import InterfaceClient
from .ise import IseEvaluator
from .models import Homework, Question
from .pk import build_pk_detail, build_vocab_detail
from .scoring import ScoreProfile, build_score_detail, convert_score, generate_score_xml

ProgressCallback = Callable[[str, int, str], None]

COMPOSITION_TEXTS = [
    "In today's rapidly changing world, technology plays an increasingly important role in our daily lives. From smartphones to artificial intelligence, we are surrounded by innovations that make our lives more convenient and efficient. However, it is important to remember that technology should serve humanity, not the other way around. We must learn to use technology wisely and maintain a healthy balance between our digital and real-world interactions. Education, in particular, has been transformed by technology, offering new opportunities for learning and growth. Students can now access information from around the world and collaborate with peers across different cultures and backgrounds.",
    "Environmental protection has become one of the most pressing issues of our time. Climate change, pollution, and the loss of biodiversity threaten the future of our planet. Each of us has a responsibility to contribute to a more sustainable world. Simple actions such as reducing waste, conserving energy, and choosing eco-friendly products can make a significant difference. Governments and businesses must also play their part by implementing policies and practices that prioritize environmental sustainability. Together, we can create a greener, healthier planet for future generations.",
    "The importance of reading cannot be overstated. Reading opens doors to new worlds, expands our knowledge, and enhances our understanding of different cultures and perspectives. Whether it is fiction or non-fiction, every book has something valuable to offer. Reading also improves our language skills, critical thinking abilities, and creativity. In an age of digital distractions, making time for reading is more important than ever. Parents and teachers should encourage children to develop a love for reading from an early age, as it is a habit that will benefit them throughout their lives.",
    "Friendship is one of the most precious gifts in life. True friends support us through difficult times and celebrate with us during moments of joy. They accept us for who we are and help us become better versions of ourselves. Building and maintaining strong friendships requires effort, trust, and communication. In today's fast-paced world, it is easy to neglect our relationships, but we should always make time for the people who matter most. A good friend is like a treasure, and we should cherish and appreciate them every day.",
    "Travel broadens the mind and enriches the soul. When we visit new places, we are exposed to different customs, languages, and ways of life. These experiences challenge our assumptions and help us develop a more open and tolerant worldview. Travel also provides opportunities for personal growth, as we learn to navigate unfamiliar situations and become more independent. Whether it is a short trip to a nearby town or a journey to a distant country, every travel experience has the potential to teach us something new about ourselves and the world around us.",
    "Health is the foundation of a happy and productive life. Maintaining good health requires a balanced diet, regular exercise, and adequate rest. It also involves taking care of our mental and emotional well-being. In our busy lives, we often neglect our health until problems arise. However, prevention is always better than cure. By making healthy choices every day, we can reduce the risk of many diseases and improve our overall quality of life. Remember that your health is your most valuable asset, and it deserves your attention and care.",
    "Learning a new language is a challenging but rewarding journey. It opens up opportunities for communication, cultural understanding, and personal development. Research shows that bilingualism can improve cognitive abilities and delay the onset of age-related mental decline. While mastering a new language takes time and dedication, the benefits far outweigh the effort. Modern technology offers many tools and resources to support language learning, from mobile apps to online courses and virtual language exchanges. The key to success is consistency and a willingness to make mistakes and learn from them.",
    "Teamwork is essential in almost every aspect of life. Whether in school, at work, or in sports, working effectively with others can lead to better outcomes than working alone. Good teamwork requires clear communication, mutual respect, and a shared commitment to common goals. Each team member brings unique strengths and perspectives that contribute to the group's success. When conflicts arise, addressing them constructively can strengthen the team rather than weaken it. Learning to collaborate with diverse groups of people is a valuable skill that will serve you well throughout your career and personal life.",
]


@dataclass(slots=True)
class WorkflowOptions:
    output_dir: Path
    voice_mode: str = "edge"
    evaluator: str = "local"
    voice: str = "en-US-JennyNeural"
    rate: str = "-10%"
    pitch: str = "+0Hz"
    clone_reference: Path | None = None
    clone_config: CloneTtsConfig | None = None
    chinese_tone: bool = False
    noise: float = 0.0
    http_tts_config: HttpTtsConfig | None = None
    ise_config: IseConfig | None = None
    score_profile: ScoreProfile = field(default_factory=ScoreProfile)
    per_question_scores: dict[int, ScoreProfile] = field(default_factory=dict)
    target_total: float | None = None
    use_time: int | None = None
    allow_over_max: bool = False
    session_id: str = "local-session"
    send_interface: bool = False
    ets_client: EtsClient | None = None
    parent_account_id: str = ""
    upload: Callable[[Path, str], str] | None = None


@dataclass(slots=True)
class QuestionResult:
    index: int
    entity_id: str
    category: str
    text: str
    score_profile: dict[str, float]
    awarded: float
    maximum: float
    use_time: int
    audio_path: str = ""
    xml_path: str = ""
    interface_response: dict = field(default_factory=dict)


def question_use_time(question: Question) -> int:
    if question.category == "choose" or question.structure_type == "word_question":
        return 8
    if question.structure_type in {"collector.picture", "collector.repeat_essay"}:
        return 12
    return 10


def recommended_use_time(homework: Homework) -> int:
    """推荐完成用时（秒）：按题目类型累加，并给不同作业类型一个合理下限。"""
    base = sum(question_use_time(item) for item in homework.questions)
    if homework.kind == "mock":
        return max(base, 180)
    if homework.kind in {"pk", "vocab"}:
        return max(base, 30)
    if homework.kind == "composition":
        return max(base, 300)
    return max(base, 60)


class StudyWorkflow:
    def __init__(
        self,
        options: WorkflowOptions,
        *,
        interface: InterfaceClient | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.options = options
        self.interface = interface
        self.progress = progress or (lambda _step, _percent, _message: None)

    @property
    def _client(self) -> InterfaceClient | EtsClient:
        if self.options.ets_client is not None:
            return self.options.ets_client
        if self.interface is None:
            raise ValueError("workflow requires an interface or ets client when sending")
        return self.interface

    @property
    def _is_real(self) -> bool:
        return self.options.ets_client is not None

    def _call(self, name: str, *args: object) -> dict:
        method = getattr(self._client, name)
        if self._is_real:
            return method(*args)
        return method(*args, self.options.session_id)

    def _pre_upload_id(self) -> str:
        result = self._call("pre_upload")
        if not isinstance(result, dict):
            return ""
        body = result.get("body", result) if isinstance(result.get("body", result), dict) else {}
        return str(body.get("id", ""))

    def _seq_id(self) -> str:
        millis = int(time.time() * 1000)
        return f"{millis}{random.randint(0, 9999):04d}"

    async def run(self, homework: Homework) -> dict:
        if homework.kind in {"pk", "vocab"}:
            return await self._run_pk(homework)
        if homework.kind == "composition":
            return await self._run_composition(homework)
        return await self._run_audio(homework)

    def _resolved_use_time(self, homework: Homework, per_question: int, total: int) -> int:
        """返回最终整份作业用时：自定义 > 推荐。"""
        if self.options.use_time is not None and self.options.use_time > 0:
            return int(self.options.use_time)
        return recommended_use_time(homework)

    async def _run_audio(self, homework: Homework) -> dict:
        output_dir = self.options.output_dir
        audio_dir = output_dir / "audio"
        xml_dir = output_dir / "xml"
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(exist_ok=True)
        xml_dir.mkdir(exist_ok=True)

        self.progress("start", 2, "[0/8] start homework")
        start_response = {}
        if self._should_send:
            start_response = self._call("check_start", homework.id)
            if self._is_real and isinstance(start_response, dict):
                resource_id = str(
                    start_response.get("resource_id") or start_response.get("id") or ""
                )
                if resource_id:
                    homework.resource_id = resource_id

        self.progress("parse", 8, f"[1/8] loaded {len(homework.questions)} questions")
        ppr_response = {}
        if self._should_send and homework.kind != "mock":
            batch = [{"entity_id": item.entity_id, "order": item.order} for item in homework.questions]
            ppr_response = self._call("ppr_resources", batch)

        results: list[QuestionResult] = []
        total_use_time = 0
        maximum_per_question = homework.total_point / len(homework.questions) if homework.questions else 0
        evaluator = IseEvaluator(self.options.ise_config) if self.options.evaluator == "ise" else None

        for index, question in enumerate(homework.questions):
            start = 10 + int(78 * index / max(1, len(homework.questions)))
            self.progress("question", start, f"[3-7] question {index + 1}/{len(homework.questions)}")
            profile = self.options.per_question_scores.get(index, self.options.score_profile)
            if not self.options.allow_over_max:
                profile = profile.clamped()
            audio_path = ""
            xml_path = xml_dir / f"{index + 1:03d}_{question.entity_id}.xml"

            if question.category == "choose" or not question.text:
                xml = generate_score_xml(
                    question.text or question.correct_option,
                    profile,
                    category="read_chapter",
                    seed=index,
                    clamp=not self.options.allow_over_max,
                )
            else:
                generated = audio_dir / f"{index + 1:03d}_{question.entity_id}.mp3"
                if self.options.voice_mode == "clone":
                    if self.options.clone_reference is None or self.options.clone_config is None:
                        raise ValueError("clone mode requires clone_reference and clone_config")
                    await synthesize_clone(
                        question.text,
                        self.options.clone_reference,
                        generated,
                        self.options.clone_config,
                        chinese_tone=self.options.chinese_tone,
                        noise=self.options.noise,
                    )
                elif self.options.voice_mode == "http":
                    if self.options.http_tts_config is None:
                        raise ValueError("http voice mode requires http_tts_config")
                    await synthesize_http(
                        question.text,
                        generated,
                        self.options.http_tts_config,
                    )
                else:
                    await synthesize_edge(
                        question.text,
                        generated,
                        voice=self.options.voice,
                        rate=self.options.rate,
                        pitch=self.options.pitch,
                    )
                audio_path = str(generated.resolve())
                if evaluator is not None:
                    evaluated = await evaluator.evaluate_file(
                        generated, question.text, category=question.category
                    )
                    xml = evaluated.xml
                    profile = ScoreProfile(
                        total=evaluated.scores["total_score"],
                        accuracy=evaluated.scores["accuracy_score"],
                        fluency=evaluated.scores["fluency_score"],
                        integrity=evaluated.scores["integrity_score"],
                        stress=evaluated.scores["stress_score"],
                    ).clamped()
                else:
                    xml = generate_score_xml(
                        question.text,
                        profile,
                        category=question.category,
                        seed=index,
                        clamp=not self.options.allow_over_max,
                    )

            xml_path.write_text(xml, encoding="utf-8")
            use_time = question_use_time(question)
            total_use_time += use_time
            awarded = convert_score(
                profile.total, maximum_per_question, cap=not self.options.allow_over_max
            )
            sync_response = {}
            if self._should_send and homework.kind != "mock":
                upload_file_id = self._pre_upload_id()
                detail_file_id = self._pre_upload_id()
                upload_file_url = ""
                detail_file_url = ""
                if self.options.upload is not None:
                    if audio_path:
                        upload_file_url = self.options.upload(Path(audio_path), "mark_audio")
                    detail_file_url = self.options.upload(xml_path, "mark_xml")
                else:
                    upload_file_url = audio_path
                    detail_file_url = str(xml_path.resolve())
                sync_params: dict = {
                    "homework_id": homework.id,
                    "resource_id": homework.resource_id,
                    "set_id": homework.set_id,
                    "entity_id": question.entity_id,
                    "order": str(question.order + 1),
                    "score": f"{profile.total:.2f}",
                    "correct": 1,
                    "auto_submit": 1,
                    "use_time": use_time,
                    "graduation": 0.125,
                    "upload_file_id": upload_file_id,
                    "upload_file_url": upload_file_url,
                    "detail_file_id": detail_file_id,
                    "detail_file_url": detail_file_url,
                    "score_detail": build_score_detail(
                        profile,
                        category=question.category,
                        choose=question.correct_option,
                        clamp=not self.options.allow_over_max,
                    ),
                }
                if self._is_real:
                    sync_params.update(
                        {
                            "serial_id": "0",
                            "mistake_id": "",
                            "client_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "version": 2,
                            "mark_service": 1,
                            "seq_id": self._seq_id(),
                            "spx_file_url": "",
                            "mark_trace_id": json.dumps({"engine": "cloud"}),
                        }
                    )
                sync_response = self._call("audio_sync_v2", sync_params)
            results.append(
                QuestionResult(
                    index=index,
                    entity_id=question.entity_id,
                    category=question.category,
                    text=question.text,
                    score_profile=asdict(profile),
                    awarded=awarded,
                    maximum=round(maximum_per_question, 2),
                    use_time=use_time,
                    audio_path=audio_path,
                    xml_path=str(xml_path.resolve()),
                    interface_response=sync_response,
                )
            )

        total_use_time = self._resolved_use_time(homework, use_time, total_use_time)

        set_time_response = {}
        report_response = {}
        exam_sync_response = {}
        exam_submit_response = {}
        if self._should_send and homework.kind == "mock":
            combination_id = str(homework.metadata.get("combination_id", ""))
            if not combination_id and self._is_real:
                try:
                    exam_body = self._call("exam_struct", homework.set_id, homework.resource_id, self.options.parent_account_id)
                except Exception:
                    exam_body = None
                if isinstance(exam_body, dict):
                    combinations = exam_body.get("combination", [])
                    if isinstance(combinations, list) and combinations and isinstance(combinations[0], dict):
                        combination_id = str(combinations[0].get("id", ""))
                    if not combination_id:
                        for content in exam_body.get("content", []):
                            if isinstance(content, dict) and content.get("combination_id"):
                                combination_id = str(content.get("combination_id", ""))
                                break
            if not combination_id:
                if self._is_real:
                    print(
                        "无法获取该考试的 combination_id（m/exam/struct 返回 40020："
                        "考试已完成或已过期，服务器拒绝重新提交）。跳过上传，仅保存本地结果。"
                    )
                    report = self._build_report(
                        homework,
                        results,
                        total_use_time,
                        start_response,
                        ppr_response,
                        {},
                        {},
                        {},
                        {},
                    )
                    report_path = self.options.output_dir / "report.json"
                    report_path.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    report["report_path"] = str(report_path.resolve())
                    self.progress("complete", 100, "[8/8] local workflow completed")
                    return report
                combination_id = "local-combination"
            detail = [
                {
                    "combination_id": combination_id,
                    "question_id": item.entity_id,
                    "order": str(item.index + 1),
                    "real_score": str(item.awarded),
                    "answer": json.dumps([{"value": item.text}], ensure_ascii=False),
                    "client_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "delete": "0",
                }
                for item in results
            ]
            exam_sync_response = self._call(
                "exam_sync",
                {
                    "exam_id": homework.set_id if self._is_real else homework.set_id,
                    "resource_id": homework.resource_id,
                    "use_time": total_use_time,
                    "detail": json.dumps(detail, ensure_ascii=False),
                },
            )
            exam_submit_response = self._call(
                "exam_submit",
                {
                    "exam_id": homework.set_id if self._is_real else homework.set_id,
                    "resource_id": homework.resource_id,
                    "use_time": total_use_time,
                    "combination_id": combination_id,
                },
            )
        elif self._should_send:
            score = round(sum(item.awarded for item in results), 2)
            if self.options.target_total is not None:
                if self.options.allow_over_max:
                    score = max(0.0, self.options.target_total)
                else:
                    score = max(0.0, min(homework.total_point, self.options.target_total))
            score_rate = int(round(score / homework.total_point * 1_000_000)) if homework.total_point else 0
            set_time_params: dict = {
                "homework_id": homework.id,
                "resource_id": homework.resource_id,
                "use_time": total_use_time,
                "reset": 0,
                "end": 1,
            }
            report_params: dict = {
                "homework_id": homework.id,
                "homework_score_rate": score_rate,
                "homework_complete_rate": 1_000_000,
                "homework_use_time": total_use_time,
                "is_confirm_commit": 1,
                "resource_id": homework.resource_id,
            }
            if self._is_real:
                set_time_params.update({"correct": 0, "version": 2})
                report_params.update(
                    {
                        "parent_account_id": self.options.parent_account_id,
                        "seq_id": "",
                    }
                )
            set_time_response = self._call("set_use_time", set_time_params)
            report_response = self._call("report_score", report_params)

        report = self._build_report(
            homework,
            results,
            total_use_time,
            start_response,
            ppr_response,
            set_time_response,
            report_response,
            exam_sync_response,
            exam_submit_response,
        )
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(report_path.resolve())
        self.progress("complete", 100, "[8/8] local workflow completed")
        return report

    def _build_report(
        self,
        homework: Homework,
        results: list[QuestionResult],
        total_use_time: int,
        start_response: dict,
        ppr_response: dict,
        set_time_response: dict,
        report_response: dict,
        exam_sync_response: dict,
        exam_submit_response: dict,
    ) -> dict:
        score = round(sum(item.awarded for item in results), 2)
        if self.options.target_total is not None:
            if self.options.allow_over_max:
                score = max(0.0, self.options.target_total)
            else:
                score = max(0.0, min(homework.total_point, self.options.target_total))
        score_rate = int(round(score / homework.total_point * 1_000_000)) if homework.total_point else 0
        return {
            "homework": homework.to_dict(),
            "summary": {
                "score": score,
                "maximum": homework.total_point,
                "score_rate": score_rate,
                "complete_rate": 1_000_000,
                "use_time": total_use_time,
                "questions": len(results),
                "voice_mode": self.options.voice_mode,
                "evaluator": self.options.evaluator,
                "kind": homework.kind,
            },
            "steps": {
                "check_start": start_response,
                "ppr_resources": ppr_response,
                "set_use_time": set_time_response,
                "report_score": report_response,
                "exam_sync": exam_sync_response,
                "exam_submit": exam_submit_response,
            },
            "results": [asdict(item) for item in results],
            "created_at": int(time.time()),
        }

    async def _run_pk(self, homework: Homework) -> dict:
        output_dir = self.options.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self.progress("pk", 5, "[PK 1/7] prepare local vocabulary questions")
        target_percent = (
            self.options.target_total
            if self.options.target_total is not None
            else self.options.score_profile.total / 5.0 * 100.0
        )
        target_percent = max(0.0, min(100.0, target_percent))
        correct_count = round(len(homework.questions) * target_percent / 100.0)
        correct_indexes = set(range(correct_count))
        use_time = self._resolved_use_time(homework, len(homework.questions) * 3, max(10, len(homework.questions) * 3))

        steps: dict[str, dict] = {}
        if self._should_send:
            steps["score_drop"] = self._call("score_drop", homework.id)
            check_body = self._call("check_start", homework.id)
            steps["check_start"] = check_body
            if self._is_real and isinstance(check_body, dict):
                resource_id = str(check_body.get("resource_id") or check_body.get("id") or "")
                if resource_id:
                    homework.resource_id = resource_id
            steps["set_use_time"] = self._call(
                "set_use_time",
                {
                    "homework_id": homework.id,
                    "resource_id": homework.resource_id,
                    "use_time": use_time,
                    "reset": 0,
                    "end": 0,
                },
            )

        self.progress("pk", 35, f"[PK 2-6/7] build {len(homework.questions)} answer records")
        details: list[dict] = []
        if homework.kind == "pk":
            details = [
                build_pk_detail(question, index in correct_indexes)
                for index, question in enumerate(homework.questions)
            ]
            if self._should_send:
                info = self._call("pk_info", homework.id)
                serial_id = str(
                    ((info.get("body") if isinstance(info, dict) else info) or {}).get(
                        "serial_id", ""
                    )
                )
                if not serial_id and self._is_real:
                    serial_id = str(
                        ((info.get("body") if isinstance(info, dict) else info) or {}).get(
                            "serialId", ""
                        )
                    )
                if not serial_id:
                    started = self._call(
                        "start_word_pk",
                        {
                            "unit_id": homework.metadata.get("unit_id", 0),
                            "book_id": homework.metadata.get("book_id", 0),
                            "word_ids": ",".join(str(item.word_id) for item in homework.questions),
                            "question_ids": ",".join(
                                str(item.question_id or item.order + 1) for item in homework.questions
                            ),
                            "name": homework.name,
                            "source": 1,
                            "version": 1,
                            "parent_account_id": self.options.parent_account_id,
                        },
                    )
                    serial_id = str(
                        ((started.get("body") if isinstance(started, dict) else started) or {}).get(
                            "serial_id", ""
                        )
                    )
                    steps["start_word_pk"] = started
                steps["submit_word"] = self._call(
                    "submit_word_pk",
                    {
                        "serial_id": serial_id,
                        "score_detail": details,
                        "use_time": use_time,
                        "source": 1,
                    },
                )
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            details = [
                build_vocab_detail(
                    question,
                    index in correct_indexes,
                    now,
                    version=(
                        self._client.config.client_version if self._is_real else "local"
                    ),
                )
                for index, question in enumerate(homework.questions)
            ]
            if self._should_send:
                steps["score_sync"] = self._call(
                    "score_sync",
                    {
                        "homework_id": homework.id,
                        "resource_id": homework.resource_id,
                        "use_time": use_time,
                        "seq_id": uuid.uuid4().hex,
                        "type": 6,
                        "mark_service": 1,
                        "detail": details,
                    },
                )

        score_rate = int(round(target_percent * 10_000))
        if self._should_send:
            report_params: dict = {
                "homework_id": homework.id,
                "homework_score_rate": score_rate,
                "homework_complete_rate": 1_000_000,
                "homework_use_time": use_time,
                "is_confirm_commit": 1,
                "resource_id": homework.resource_id,
            }
            if self._is_real:
                report_params["parent_account_id"] = self.options.parent_account_id
            steps["report_score"] = self._call("report_score", report_params)
        report = {
            "homework": homework.to_dict(),
            "summary": {
                "score": target_percent,
                "maximum": 100.0,
                "score_rate": score_rate,
                "complete_rate": 1_000_000,
                "use_time": use_time,
                "questions": len(homework.questions),
                "correct": correct_count,
                "kind": homework.kind,
            },
            "steps": steps,
            "results": details,
            "created_at": int(time.time()),
        }
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(report_path.resolve())
        self.progress("complete", 100, "[PK 7/7] local vocabulary workflow completed")
        return report

    async def _run_composition(self, homework: Homework) -> dict:
        output_dir = self.options.output_dir
        drafts_dir = output_dir / "drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        self.progress("composition", 10, "[1/4] prepare local composition drafts")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        compositions = homework.metadata.get("compositions", [])
        details: list[dict] = []
        for index, question in enumerate(homework.questions):
            if self._is_real and isinstance(compositions, list) and compositions:
                text = COMPOSITION_TEXTS[index % len(COMPOSITION_TEXTS)]
                comp = compositions[index] if index < len(compositions) else compositions[0]
                composition_id = str(
                    comp.get("composition_id") or comp.get("id") or question.question_id or question.entity_id
                )
                sequence_id = str(comp.get("sequence_id") or comp.get("seq_id") or uuid.uuid4().hex)
            else:
                text = question.answer or question.text
                composition_id = question.question_id or question.entity_id
                sequence_id = uuid.uuid4().hex
            draft_path = drafts_dir / f"{index + 1:03d}_{question.entity_id}.txt"
            draft_path.write_text(text, encoding="utf-8")
            details.append(
                {
                    "composition_id": composition_id,
                    "text": text,
                    "client_time": now,
                    "sequence_id": sequence_id,
                    "draft_path": str(draft_path.resolve()),
                }
            )

        steps: dict[str, dict] = {}
        if self._should_send:
            composition_time = self._resolved_use_time(
                homework, max(30, len(details) * 60), max(30, len(details) * 60)
            )
            steps["save_draft"] = self._call(
                "save_composition_draft",
                {
                    "resource_id": homework.resource_id,
                    "homework_id": homework.id,
                    "use_time": composition_time,
                    "detail": [
                        {
                            "composition_id": item["composition_id"],
                            "text": item["text"],
                            "client_time": item["client_time"],
                            "seq_id": item["sequence_id"],
                        }
                        for item in details
                    ],
                },
            )
            marks = []
            for item in details:
                marks.append(
                    self._call(
                        "mark_composition",
                        {
                            "resource_id": homework.resource_id,
                            "composition_id": item["composition_id"],
                            "sequence_id": item["sequence_id"],
                            "text": item["text"],
                            "homework_id": homework.id,
                            "version": "1",
                            "need_mark": 1,
                            "model_correction_status": 0,
                        },
                    )
                )
            steps["mark"] = {"items": marks}
            steps["submit"] = self._call(
                "submit_homework",
                {
                    "homework_id": homework.id,
                    "resource_id": homework.resource_id,
                    "seq_id": uuid.uuid4().hex,
                    "use_time": composition_time,
                },
            )

        report = {
            "homework": homework.to_dict(),
            "summary": {
                "kind": "composition",
                "questions": len(details),
                "characters": sum(len(item["text"]) for item in details),
            },
            "steps": steps,
            "results": details,
            "created_at": int(time.time()),
        }
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(report_path.resolve())
        self.progress("complete", 100, "[4/4] local composition workflow completed")
        return report

    @property
    def _should_send(self) -> bool:
        return self.options.ets_client is not None or (
            self.options.send_interface and self.interface is not None
        )
