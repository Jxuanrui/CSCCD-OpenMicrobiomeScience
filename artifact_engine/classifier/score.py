"""Artifact confidence scoring with hard gates and optional Gemini refinement."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]


CONFIRMED_THRESHOLD = 0.7
REVIEW_THRESHOLD = 0.4
HIGH_TITLE_SIMILARITY_THRESHOLD = 0.75
# Combo (c) skips owner matching entirely, so a pure keyword_overlap score
# (as opposed to an exact title substring match, which _lexical_title_similarity_score
# maps to 1.0) is a weaker signal here than in combo (b), which still requires
# dir_high_prob_present as an extra check. Phase 4 cross-verification found a
# real false positive at keyword_overlap=0.769 (wf-somatic-variation, a
# third-party workflow that happens to share heavy domain vocabulary with the
# cited paper's own tool). Raised so pure keyword_overlap must be near-exact;
# an exact substring match (score 1.0) still always qualifies.
COMBO_C_LEXICAL_THRESHOLD = 0.85
UNCONFIRMED_CONFIDENCE_CAP = 0.69
GEMINI_REFINEMENT_ENABLED = True
GEMINI_MODEL = "gemini-3.1-pro"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TIMEOUT_SECONDS = 10

EVIDENCE_WEIGHTS = {
    "doi_cited_in_text": 0.25,
    "explicit_deposit_statement": 0.2,
    "owner_matches_author": 0.25,
    "readme_contains_title": 0.15,
    "embedding_similarity": 0.10,
    "dir_high_prob_present": 0.15,
    "dir_low_prob_only": -0.05,
    "domain_keyword_hit": 0.05,
    "tool_keyword_hit": 0.05,
}

ARTIFACT_TYPES = {
    "analysis_code",
    "workflow",
    "software",
    "dataset",
    "notebook",
    "documentation",
}


def classify_artifact(
    paper: dict, artifact_metadata: dict, evidence_list: list[dict]
) -> dict:
    """Classify an artifact using evidence weights plus mandatory strong gates.

    The hard-gate decision is derived from the structured evidence list. Gemini
    may only refine non-strong evidence already inside the review queue range.
    """
    evidence = evidence_list if isinstance(evidence_list, list) else []
    passed_checks = {
        item.get("check")
        for item in evidence
        if isinstance(item, Mapping) and item.get("passed") is True
    }

    title_similarity = _title_similarity_score(evidence)
    strong_combination_a = {
        "doi_cited_in_text",
        "owner_matches_author",
    }.issubset(passed_checks)
    # Hard-gate combinations (b)/(c) may only be unlocked by lexical title overlap,
    # not by embedding similarity alone. Same-domain-different-tool repos can score
    # deceptively high on embeddings (e.g. two unrelated single-cell Hi-C tools both
    # mentioning "single cell"/"data processing" hit cosine=0.775 while being
    # genuinely different artifacts) because embeddings capture topic proximity,
    # not "is this literally the same project." Embedding similarity still feeds
    # into the weighted confidence score below; it just cannot cross a hard gate
    # on its own.
    lexical_title_similarity = _lexical_title_similarity_score(evidence)
    strong_combination_b = (
        "readme_contains_title" in passed_checks
        and lexical_title_similarity >= HIGH_TITLE_SIMILARITY_THRESHOLD
        and "dir_high_prob_present" in passed_checks
    )
    # Combination (c): direct citation + high title overlap, without requiring
    # owner/author string matching. Lab/team GitHub accounts (e.g. "neurogenomics")
    # rather than personal usernames are common in bioinformatics repos, which made
    # combination (a)'s owner_matches_author check fail for the author's own code.
    # Phase 4 small-sample validation (40-artifact cross-check) found this blocked
    # 72% of disagreements between the rule-based and Gemini-independent rounds.
    strong_combination_c = (
        "doi_cited_in_text" in passed_checks
        and "readme_contains_title" in passed_checks
        and lexical_title_similarity >= COMBO_C_LEXICAL_THRESHOLD
    )
    # Combination (d): an explicit "code deposited/available at <identifier>"
    # boilerplate statement near the cited identifier, without requiring
    # README-based title matching. Research-paper companion-code repos are
    # often just a handful of bare analysis scripts with no README/description
    # at all (Phase 4 follow-up: PMC12866134's ASD-microbiome paper), making
    # combo (b)/(c) unreachable even when the paper's citation sentence is
    # unambiguous ("All original code has been deposited at GitHub ... and is
    # publicly available as of the date of publication").
    strong_combination_d = "explicit_deposit_statement" in passed_checks
    strong_evidence = (
        strong_combination_a
        or strong_combination_b
        or strong_combination_c
        or strong_combination_d
    )

    confidence = _weighted_confidence(evidence)
    if strong_combination_a:
        confidence = max(confidence, 0.75)
    if strong_combination_b:
        confidence = max(confidence, 0.72)
    if strong_combination_c:
        confidence = max(confidence, 0.75)
    if strong_combination_d:
        confidence = max(confidence, 0.72)

    if not strong_evidence:
        confidence = min(confidence, UNCONFIRMED_CONFIDENCE_CAP)

    confidence = round(min(max(confidence, 0.0), 1.0), 3)
    gemini_refinement = {
        "enabled": GEMINI_REFINEMENT_ENABLED,
        "eligible": False,
        "attempted": False,
        "success": False,
        "base_confidence": confidence,
        "refined_confidence": confidence,
        "reason": (
            "strong_evidence_hard_gate"
            if strong_evidence
            else "outside_review_queue_refinement_scope"
        ),
    }
    if (
        not strong_evidence
        and REVIEW_THRESHOLD <= confidence < CONFIRMED_THRESHOLD
    ):
        confidence, gemini_refinement = refine_confidence_with_gemini(
            base_confidence=confidence,
            evidence=evidence,
            paper=paper,
            artifact_metadata=artifact_metadata,
            strong_evidence=strong_evidence,
        )

    if confidence >= CONFIRMED_THRESHOLD:
        status = "confirmed"
    elif confidence >= REVIEW_THRESHOLD:
        status = "review_queue"
    else:
        status = "low_confidence_rejected"

    return {
        "confidence": confidence,
        "evidence": evidence,
        "status": status,
        "artifact_type": _artifact_type(artifact_metadata),
        "gemini_refinement": gemini_refinement,
    }


def refine_confidence_with_gemini(
    base_confidence: float,
    evidence: list[dict],
    paper: dict,
    artifact_metadata: dict,
    strong_evidence: bool,
    api_key: str | None = None,
) -> tuple[float, dict]:
    """Refine review-queue confidence without crossing hard gate boundaries."""
    base_confidence = round(_bounded_score(base_confidence), 3)
    info = {
        "enabled": GEMINI_REFINEMENT_ENABLED,
        "eligible": False,
        "attempted": False,
        "success": False,
        "model": GEMINI_MODEL,
        "base_confidence": base_confidence,
        "refined_confidence": base_confidence,
    }

    if not GEMINI_REFINEMENT_ENABLED:
        info["reason"] = "refinement_disabled"
        return base_confidence, info
    if strong_evidence:
        info["reason"] = "strong_evidence_hard_gate"
        return base_confidence, info
    if not REVIEW_THRESHOLD <= base_confidence < CONFIRMED_THRESHOLD:
        info["reason"] = "outside_review_queue_refinement_scope"
        return base_confidence, info

    info["eligible"] = True
    try:
        if api_key is None:
            load_dotenv(PROJECT_ROOT / ".env")
            api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            info["reason"] = "missing_api_key"
            return base_confidence, info
        base_url = os.getenv("GOOGLE_GEMINI_BASE_URL", GEMINI_DEFAULT_BASE_URL).rstrip("/")
        if not base_url.endswith("/v1beta"):
            base_url = f"{base_url}/v1beta"

        prompt_payload = {
            "base_confidence": base_confidence,
            "max_allowed_confidence": UNCONFIRMED_CONFIDENCE_CAP,
            "paper": _paper_prompt_summary(paper),
            "artifact_metadata": _artifact_prompt_summary(artifact_metadata),
            "evidence": evidence if isinstance(evidence, list) else [],
        }
        prompt = (
            "You are refining confidence that a research artifact was published "
            "by the paper's authors. Use only the structured JSON evidence "
            "below. Do not infer facts not present in it. Return JSON only in "
            'the form {"confidence": 0.15, "reason": "brief explanation"}. '
            f"Confidence must not exceed {UNCONFIRMED_CONFIDENCE_CAP} -- it must "
            "not promote the artifact to confirmed status. If the evidence "
            "suggests this is unrelated third-party work rather than the "
            "author's own artifact, return a low confidence well below "
            f"{REVIEW_THRESHOLD}; do not artificially floor it at "
            f"{REVIEW_THRESHOLD}.\n\n"
            + json.dumps(prompt_payload, ensure_ascii=False, default=str)
        )
        request_body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        url = f"{base_url}/models/{GEMINI_MODEL}:generateContent"

        info["attempted"] = True
        response = requests.post(
            url,
            json=request_body,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=GEMINI_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            info["reason"] = f"http_status_{response.status_code}"
            info["raw_response"] = response.text
            return base_confidence, info

        raw_response = response.json()
        info["raw_response"] = raw_response
        response_text = raw_response["candidates"][0]["content"]["parts"][0][
            "text"
        ]
        parsed_output = _parse_gemini_output(response_text)
        refined_confidence = float(parsed_output["confidence"])
        if not math.isfinite(refined_confidence):
            raise ValueError("Gemini confidence is not finite")

        # Only the upper bound is enforced here: refinement must never promote
        # an artifact to `confirmed`. The lower bound is intentionally left
        # open so a genuinely unrelated third-party artifact can be refined
        # down into `low_confidence_rejected` instead of being floored at
        # REVIEW_THRESHOLD (see tests/test_classifier.py history/design.md).
        refined_confidence = round(
            min(max(refined_confidence, 0.0), UNCONFIRMED_CONFIDENCE_CAP),
            3,
        )
        info.update(
            {
                "success": True,
                "reason": parsed_output.get("reason", ""),
                "model_output": parsed_output,
                "refined_confidence": refined_confidence,
            }
        )
        return refined_confidence, info
    except requests.RequestException as exc:
        info["reason"] = f"request_error: {_safe_error_message(exc, api_key)}"
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        info["reason"] = f"response_parse_error: {_safe_error_message(exc, api_key)}"
    except Exception as exc:
        info["reason"] = f"unexpected_error: {_safe_error_message(exc, api_key)}"
    return base_confidence, info


def _paper_prompt_summary(paper: dict) -> dict:
    if not isinstance(paper, Mapping):
        return {}
    abstract = str(paper.get("abstract") or paper.get("abstract_text") or "")
    return {
        "title": paper.get("title"),
        "abstract_excerpt": abstract[:1500],
        "doi": paper.get("doi"),
        "year": paper.get("year"),
        "authors": paper.get("authors"),
    }


def _artifact_prompt_summary(artifact_metadata: dict) -> dict:
    if not isinstance(artifact_metadata, Mapping):
        return {}
    fields = (
        "source",
        "url",
        "owner",
        "name",
        "title",
        "description",
        "doi",
        "language",
        "license",
        "created_date",
        "updated_date",
        "top_level_directories",
    )
    return {field: artifact_metadata.get(field) for field in fields}


def _parse_gemini_output(response_text: str) -> dict:
    if not isinstance(response_text, str):
        raise TypeError("Gemini response text is not a string")
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise TypeError("Gemini response JSON is not an object")
    if "confidence" not in parsed:
        raise KeyError("confidence")
    return parsed


def _safe_error_message(exc: Exception, api_key: str | None) -> str:
    message = str(exc)
    return message.replace(api_key, "***") if api_key else message


def _weighted_confidence(evidence: list[dict]) -> float:
    confidence = 0.15
    for item in evidence:
        if not isinstance(item, Mapping) or item.get("passed") is not True:
            continue
        check_name = item.get("check")
        weight = EVIDENCE_WEIGHTS.get(check_name, 0.0)
        if check_name == "embedding_similarity":
            score = _bounded_score(item.get("score"))
            weight *= score
        elif check_name == "readme_contains_title" and "score" in item:
            weight *= max(_bounded_score(item.get("score")), 0.5)
        confidence += weight
    return confidence


def _title_similarity_score(evidence: list[dict]) -> float:
    """Best available title-similarity signal, embedding included.

    Used for weighted confidence only; hard gates use
    ``_lexical_title_similarity_score`` instead (see its docstring).
    """
    readme_scores = []
    embedding_scores = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        if item.get("check") == "readme_contains_title" and "score" in item:
            readme_scores.append(_bounded_score(item.get("score")))
        elif item.get("check") == "embedding_similarity" and "score" in item:
            embedding_scores.append(_bounded_score(item.get("score")))
    if readme_scores:
        return max(readme_scores)
    return max(embedding_scores, default=0.0)


def _lexical_title_similarity_score(evidence: list[dict]) -> float:
    """Title-similarity signal restricted to lexical evidence (no embeddings).

    Embeddings capture topical proximity, not "is this literally the same
    project" -- two unrelated same-domain tools can score high on cosine
    similarity while being genuinely different artifacts (Phase 4 regression:
    LiMCA vs. map3C, both single-cell Hi-C tools, cosine=0.775). Hard gates
    (b)/(c) must only be unlocked by an exact title substring match or a
    meaningful keyword_overlap ratio, never by embedding score alone.
    """
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        if item.get("check") != "readme_contains_title":
            continue
        if "substring match=True" in str(item.get("detail") or ""):
            return 1.0
        if "keyword_overlap" in item:
            return _bounded_score(item.get("keyword_overlap"))
    return 0.0


def _bounded_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(score, 0.0), 1.0)


def _artifact_type(metadata: dict) -> str:
    if not isinstance(metadata, Mapping):
        return "analysis_code"

    explicit_type = metadata.get("artifact_type") or metadata.get("type")
    if isinstance(explicit_type, str) and explicit_type in ARTIFACT_TYPES:
        return explicit_type

    directories = {
        str(directory).strip().strip("/\\").casefold()
        for directory in metadata.get("top_level_directories") or []
    }
    text = " ".join(
        str(metadata.get(key) or "")
        for key in ("name", "title", "description", "readme")
    ).casefold()

    if "notebooks" in directories or "notebook" in text or ".ipynb" in text:
        return "notebook"
    if "workflow" in directories or "workflows" in directories:
        return "workflow"
    if any(term in text for term in ("dataset", "data release", "data repository")):
        return "dataset"
    if any(term in text for term in ("documentation", "docs", "manual")):
        return "documentation"
    if directories & {"src", "package", "tests"} and not directories & {
        "analysis",
        "scripts",
        "figures",
    }:
        return "software"
    return "analysis_code"
