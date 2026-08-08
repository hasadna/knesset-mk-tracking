from __future__ import annotations

import json
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .io import read_json, write_json_atomic
from .models import NONE_STANCE, Opinion, PoliticianAnalysis

SYSTEM_INSTRUCTION = """\
אתה מנתח ראיות פוליטיות בעברית. מותר להסיק אך ורק מהציוצים שסופקו.
אין להשתמש בידע חיצוני, שיוך מפלגתי, ביוגרפיה או תפקיד.
חוסר ראיות פירושו none, ולא עמדה ניטרלית או מתנגדת.
כל טענה חייבת לצטט רק מפתחות source שסופקו.
strong דורש לפחות שני ציוצים עצמאיים, ישירים ועקביים על שאלת הליבה.
partial מתאים לראיה ישירה יחידה או לראיות רלוונטיות אך מוגבלות.
נושא סמוך אינו ראיה: אם הציוצים אינם עונים ישירות על שאלת הליבה, החזר none.
לכל נושא בחר לכל היותר 8 מקורות ישירים שמוסיפים ראיות נבדלות.
כתוב stance קצר של עד שני משפטים. כתוב extended_stance רק כאשר קיימים
פרטים רלוונטיים נוספים שמרחיבים את הסיכום; אחרת החזר מחרוזת ריקה.
הימנע ממילות העצמה שאינן מופיעות במפורש בראיות.
כתוב limitations שמבהיר מה לא ניתן להסיק.
לכל נושא עם status של strong או partial יש להחזיר rating שלם בין 1 ל־5,
אך ורק לפי ratingScale של אותו נושא. עבור status של none יש להחזיר rating=null.
"""


class Analyzer(Protocol):
    def analyze(self, prompt: str) -> PoliticianAnalysis: ...


def load_inputs(root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    topics = read_json(root / "assets/topics.json")
    roster = read_json(root / "assets/roster.json")
    posts = read_json(root / "data/tweets.normalized.json")
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics must be a non-empty array")
    if not isinstance(roster, list) or not roster:
        raise ValueError("roster must be a non-empty array")
    if not isinstance(posts, list):
        raise TypeError("posts must be an array")
    return topics, roster, posts


def analyzable_members(roster: list[dict], posts: list[dict]) -> list[dict]:
    accounts_with_posts = {post.get("account") for post in posts}
    return [
        member
        for member in roster
        if member.get("account") and member["account"] in accounts_with_posts
    ]


def build_prompt(member: dict, topics: list[dict], posts: list[dict]) -> str:
    compact_topics = [
        {
            "id": topic["id"],
            "title": topic["title"],
            "tags": topic.get("tags", []),
            "description": topic.get("description", ""),
            "analysisPrompt": topic.get("analysisPrompt", ""),
            "ratingScale": topic.get("ratingScale"),
            "contrast": topic.get("contrast", ""),
            "axis": topic.get("axis"),
        }
        for topic in topics
    ]
    compact_posts = [
        {
            "source": post["key"],
            "date": post.get("date"),
            "text": post.get("text", ""),
            "referencedTweets": post.get("referencedTweets", []),
            "retrievedForIssues": post.get("retrievedForIssues", []),
        }
        for post in posts
    ]
    payload = {
        "politician": member["name"],
        "account": member["account"],
        "topics": compact_topics,
        "tweets": compact_posts,
    }
    return (
        "נתח את הציוצים עבור כל אחד מהנושאים שסופקו. "
        "החזר רשומה אחת בדיוק לכל topic_id.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def normalize_analysis(
    result: PoliticianAnalysis,
    member: dict,
    topics: list[dict],
    posts: list[dict],
) -> PoliticianAnalysis:
    if result.politician != member["name"]:
        raise ValueError(
            f"model returned politician {result.politician!r}, expected {member['name']!r}"
        )
    topic_ids = [topic["id"] for topic in topics]
    valid_sources = {post["key"] for post in posts}
    seen: set[str] = set()
    by_topic: dict[str, Opinion] = {}
    for opinion in result.opinions:
        if opinion.topic_id not in topic_ids:
            raise ValueError(f"unknown topic {opinion.topic_id}")
        if opinion.topic_id in seen:
            raise ValueError(f"duplicate topic {opinion.topic_id}")
        seen.add(opinion.topic_id)
        unknown = set(opinion.sources) - valid_sources
        if unknown:
            raise ValueError(
                f"{opinion.topic_id} cites unavailable sources: {sorted(unknown)}"
            )
        if len(opinion.sources) != len(set(opinion.sources)):
            opinion = opinion.model_copy(
                update={"sources": list(dict.fromkeys(opinion.sources))}
            )
            if opinion.status == "strong" and len(opinion.sources) < 2:
                opinion = opinion.model_copy(update={"status": "partial"})
        if opinion.status == "none":
            opinion = opinion.model_copy(
                update={
                    "stance": NONE_STANCE,
                    "extended_stance": "",
                    "sources": [],
                    "rating": None,
                }
            )
        by_topic[opinion.topic_id] = opinion

    for topic_id in topic_ids:
        by_topic.setdefault(
            topic_id,
            Opinion(
                topic_id=topic_id,
                status="none",
                stance=NONE_STANCE,
                rating=None,
                sources=[],
                extended_stance="",
                limitations="",
            ),
        )
    return PoliticianAnalysis(
        politician=member["name"], opinions=[by_topic[key] for key in topic_ids]
    )


def assemble(topics: list[dict], results: list[PoliticianAnalysis]) -> list[dict]:
    output: list[dict] = []
    for topic in topics:
        item = {key: value for key, value in topic.items()}
        axis = item.get("axis")
        if isinstance(axis, dict):
            item["axis"] = {k: v for k, v in axis.items() if k != "positions"}
        members: dict[str, dict] = {}
        for result in results:
            opinion = next(x for x in result.opinions if x.topic_id == topic["id"])
            members[result.politician] = {
                "status": opinion.status,
                "rating": opinion.rating,
                "stance": opinion.stance,
                "extendedStance": opinion.extended_stance,
                "sources": opinion.sources,
                "limitations": opinion.limitations,
            }
        item["members"] = members
        output.append(item)
    return output


def validate_artifact(analysis: list[dict], topics: list[dict], posts: list[dict]) -> None:
    if [item.get("id") for item in analysis] != [item.get("id") for item in topics]:
        raise ValueError("analysis topic order does not match taxonomy")
    post_keys = {post.get("key") for post in posts}
    for topic in analysis:
        if not isinstance(topic.get("members"), dict):
            raise TypeError(f"{topic.get('id')} has no members map")
        for name, value in topic["members"].items():
            try:
                model_value = dict(value)
                if "extendedStance" in model_value:
                    model_value["extended_stance"] = model_value.pop("extendedStance")
                opinion = Opinion(topic_id=topic["id"], **model_value)
            except Exception as error:
                raise ValueError(f"invalid {topic['id']}/{name}: {error}") from error
            unknown = set(opinion.sources) - post_keys
            if unknown:
                raise ValueError(f"{topic['id']}/{name} has unknown sources: {unknown}")
            if any(not source.startswith("x:") for source in opinion.sources):
                raise ValueError(f"{topic['id']}/{name} has a non-X source")


def validate_complete(
    analysis: list[dict], topics: list[dict], roster: list[dict], posts: list[dict]
) -> None:
    validate_artifact(analysis, topics, posts)
    expected = {member["name"] for member in analyzable_members(roster, posts)}
    for topic in analysis:
        actual = set(topic["members"])
        if not expected.issubset(actual):
            raise ValueError(
                f"{topic['id']} is missing politicians with tweets: "
                f"{sorted(expected - actual)}"
            )


def generate(
    root: Path,
    analyzer: Analyzer,
    output: Path,
    checkpoint_dir: Path,
    selected: set[str] | None = None,
) -> list[dict]:
    topics, roster, posts = load_inputs(root)
    return generate_from_inputs(
        topics, roster, posts, analyzer, output, checkpoint_dir, selected
    )


def generate_from_inputs(
    topics: list[dict],
    roster: list[dict],
    posts: list[dict],
    analyzer: Analyzer,
    output: Path,
    checkpoint_dir: Path,
    selected: set[str] | None = None,
) -> list[dict]:
    posts_by_account: dict[str, list[dict]] = defaultdict(list)
    for post in posts:
        posts_by_account[post.get("account", "")].append(post)
    members = analyzable_members(roster, posts)
    if selected:
        members = [
            member
            for member in members
            if member["key"] in selected
            or member["name"] in selected
            or member["account"] in selected
        ]
        if not members:
            raise ValueError("selection did not match any politician with tweets")

    results: list[PoliticianAnalysis] = []
    timings: list[dict[str, Any]] = []
    timing_path = output.with_name(f"{output.stem}.timings.json")
    previous_generation_times: dict[str, float] = {}
    if timing_path.exists():
        previous_timing = read_json(timing_path)
        for item in previous_timing.get("politicians", []):
            generation_time = item.get("generationElapsedSeconds")
            if generation_time is None and not item.get("cached", False):
                generation_time = item.get("elapsedSeconds")
            if generation_time is not None:
                previous_generation_times[item.get("account", "")] = generation_time
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for member in members:
        checkpoint = checkpoint_dir / f"{member['key']}.json"
        politician_posts = posts_by_account[member["account"]]
        if checkpoint.exists():
            started = time.perf_counter()
            try:
                raw = PoliticianAnalysis.model_validate(read_json(checkpoint))
                cached = True
            except ValidationError:
                raw = analyzer.analyze(build_prompt(member, topics, politician_posts))
                cached = False
            elapsed = time.perf_counter() - started
        else:
            started = time.perf_counter()
            raw = analyzer.analyze(build_prompt(member, topics, politician_posts))
            elapsed = time.perf_counter() - started
            cached = False
        normalized = normalize_analysis(raw, member, topics, politician_posts)
        write_json_atomic(checkpoint, normalized.model_dump(mode="json", by_alias=True))
        timings.append(
            {
                "politician": member["name"],
                "account": member["account"],
                "elapsedSeconds": round(elapsed, 3),
                "generationElapsedSeconds": (
                    previous_generation_times.get(member["account"])
                    if cached
                    else round(elapsed, 3)
                ),
                "cached": cached,
            }
        )
        results.append(normalized)

    artifact = assemble(topics, results)
    validate_artifact(artifact, topics, posts)
    write_json_atomic(output, artifact)
    write_json_atomic(
        timing_path,
        {
            "politicians": timings,
            "totalElapsedSeconds": round(sum(x["elapsedSeconds"] for x in timings), 3),
            "totalGenerationElapsedSeconds": round(
                sum(x["generationElapsedSeconds"] or 0 for x in timings), 3
            ),
        },
    )
    return artifact


def publish(candidate: Path, root: Path) -> Path:
    topics, roster, posts = load_inputs(root)
    artifact = read_json(candidate)
    validate_complete(artifact, topics, roster, posts)
    destination = root / "data/analysis.json"
    backup = destination.with_suffix(".json.backup")
    if destination.exists():
        shutil.copy2(destination, backup)
    write_json_atomic(destination, artifact)
    return destination
