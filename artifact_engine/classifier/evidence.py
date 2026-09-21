"""Independent evidence checks for paper-to-artifact classification."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from urllib.parse import urlparse


EMBEDDING_HIGH_SIMILARITY_THRESHOLD = 0.75
HIGH_PROBABILITY_DIRECTORIES = {
    "analysis",
    "scripts",
    "workflow",
    "notebooks",
    "figures",
}
LOW_PROBABILITY_DIRECTORIES = {"src", "package", "tests"}

DOMAIN_KEYWORDS = {
    "single cell": (r"\bsingle[-\s]?cell\b",),
    "RNA-seq": (r"\brna[-\s]?seq(?:uencing)?\b", r"\btranscriptom(?:e|ics)\b"),
    "ATAC-seq": (r"\batac[-\s]?seq\b",),
    "spatial transcriptomics": (r"\bspatial\s+transcriptom(?:e|ics)\b",),
    "bulk RNA-seq": (r"\bbulk\s+rna[-\s]?seq(?:uencing)?\b",),
    "variant calling": (r"\bvariant\s+call(?:ing|er|ers)?\b",),
    "metagenomics": (r"\bmetagenom(?:e|ic|ics)\b",),
}

TOOL_KEYWORDS = {
    "Seurat": (r"\bseurat\b",),
    "Scanpy": (r"\bscanpy\b",),
    "DESeq2": (r"\bdeseq2\b",),
    "CellRanger": (r"\bcell\s*ranger\b",),
    "STAR": (r"\bstar\b",),
    "Salmon": (r"\bsalmon\b",),
    "Nextflow": (r"\bnextflow\b",),
}

TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "via",
    "with",
}


def check_doi_cited_in_text(
    paper_fulltext: str, artifact_metadata: dict
) -> dict:
    """Check whether an artifact DOI or URL is directly present in paper text."""
    if not isinstance(paper_fulltext, str) or not paper_fulltext.strip():
        return _result("doi_cited_in_text", False, "paper full text is empty")

    identifiers = _artifact_identifiers(artifact_metadata)
    if not identifiers:
        return _result(
            "doi_cited_in_text", False, "artifact metadata contains no DOI or URL"
        )

    normalized_text = _normalize_citation_text(paper_fulltext)
    for identifier in identifiers:
        variants = _citation_variants(identifier)
        if any(variant and variant in normalized_text for variant in variants):
            return _result(
                "doi_cited_in_text",
                True,
                f"artifact identifier '{identifier}' is cited in paper text",
                matched_identifier=identifier,
            )

    return _result(
        "doi_cited_in_text",
        False,
        "no artifact DOI or URL was found in paper text",
    )


DEPOSIT_SECTION_PATTERNS = (
    r"code\s+availability",
    r"data\s+and\s+code\s+availability",
    r"software\s+and\s+data\s+availability",
    r"availability\s+and\s+implementation",
)

DEPOSIT_STATEMENT_PATTERNS = (
    r"deposited\s+(?:at|in|on|to)\s+github",
    r"deposited\s+(?:at|in|on|to)\s+zenodo",
    r"(?:code|software|scripts?)\s+(?:is|are|has been|have been)\s+"
    r"(?:publicly\s+)?available\s+(?:at|on|from|via)",
    # Tool-paper "Availability and implementation" boilerplate frequently
    # states "<ToolName> is available at <URL>" -- the subject is the tool's
    # own name, not the literal word "code"/"software" (Phase 4 follow-up:
    # cross-verify disagreements on map3C/PRISM/NestedWGCNA, all pinned at
    # the review_queue-side confidence cap because this check missed that
    # phrasing). Deliberately restricted to "at|on" (never "from|via"):
    # "Data availability ... is publicly available from ENCODE and maxATAC"
    # is a *third-party reused dataset* citation, not the authors depositing
    # their own artifact, and "available from <source>" is exactly that
    # pattern's typical wording (Phase 4 follow-up: PMC12910371 false-positive
    # near-miss on zenodo.6761768). "is/are available at/on <URL>" is safe
    # because it is the standard "here is where MY tool lives" phrasing.
    r"[\w-]+\s+(?:is|are)\s+(?:publicly\s+)?available\s+(?:at|on)\b",
    # Same "at/on"-only safety restriction as above, but tolerating a short
    # intervening clause between "available" and the preposition (Phase 4
    # review_queue manual review follow-up: PMC13308449's PyPeakRankR paper
    # phrases it "is freely available under the MIT license at <URL>" --
    # the original pattern required "available" to be immediately followed
    # by "at/on", missing this common licence-clause insertion).
    r"[\w-]+\s+(?:is|are)\s+(?:freely\s+|publicly\s+)?available"
    r"[\w\s,-]{0,40}\s+(?:at|on)\b",
    r"(?:source\s+code|analysis\s+pipeline|code\s+and\s+data)[\w\s,-]{0,60}"
    r"(?:is|are)\s+(?:publicly\s+)?available\s+(?:at|on|from|via)",
    # Author-controlled software may include an open-source clause before the
    # availability location (Phase 5 batch-010 control: PMC12415849's
    # SomaticCaller statement). Keep the subject restricted to code/software so
    # generic third-party data citations do not gain this path.
    r"(?:code|software|scripts?)[\w\s,-]{0,60}(?:is|are)[\w\s,-]{0,60}"
    r"available\s+(?:at|on)\b",
    # Companion repositories are also described as hosted by GitHub before the
    # final availability clause (Phase 5 batch-010 control: PMC9875471's
    # eoe-meta-analysis_data statement). The explicit code/data subject keeps
    # this from becoming a wildcard for arbitrary resource citations.
    r"(?:data\s+and\s+code|code\s+and\s+data)[\w\s,-]{0,80}"
    r"(?:is|are)[\w\s,-]{0,60}available\s+(?:at|on|from|via)\b",
    r"(?:original\s+)?code\s+(?:has\s+been\s+|is\s+)?deposited",
    r"available\s+as\s+of\s+the\s+date\s+of\s+publication",
    *DEPOSIT_SECTION_PATTERNS,
    r"we\s+provide\s+[\w\s,-]{0,60}\s+at\b",
    # Multi-repository "introduced in this study" list headers use a
    # "Name ( URL )" or "label: URL" citation style rather than an "is
    # available at" sentence (Phase 4 review_queue manual review: neurogenomics
    # paper PMC13411100 lists five of its own repos as "R packages introduced
    # in this study: KGExplorer (URL), HPOExplorer (URL) ... Manuscript
    # analyses and reproducibility code: URL", none of which matched any
    # existing pattern despite being an unambiguous author-authorship
    # statement). These phrases only ever precede the authors' own listed
    # artifacts, not third-party citations, so they are safe without a
    # provenance-exclusion check.
    r"(?:introduced|developed|created)\s+in\s+this\s+study",
    r"reproducibility\s+code",
    r"manuscript\s+analyses",
    # "All code used to generate the website can be found at <URL>" (Phase 4
    # review_queue manual review follow-up: neurogenomics paper PMC13411100's
    # sixth own repo, rare-disease-web-portal, cited via "can be found at"
    # rather than "is/are available at"). Subject restricted to code/software
    # artifact nouns (never a bare dataset noun like "sequence"/"data") so
    # this cannot pick up a third-party reused-dataset citation the way an
    # unrestricted wildcard would.
    r"(?:code|software|scripts?|website|portal|pipeline|repository)"
    r"[\w\s,-]{0,60}can\s+be\s+(?:found|accessed)\s+(?:at|on)\b",
    # "The raw fastq files generated in this study have been deposited in
    # the Zenodo repository" (Phase 4 review_queue manual review follow-up:
    # PMC13034549's PeakPrime paper) -- distinct from combo (d)'s existing
    # "(?:introduced|developed|created) in this study" pattern, which
    # requires the artifact itself to be the grammatical subject of "in this
    # study"; here it is the *data* that was generated in this study and
    # subsequently deposited/made available, so the wording needs to look
    # past that clause to a following deposit/availability verb.
    r"generated\s+in\s+this\s+study[\w\s,.-]{0,60}(?:deposited|available)",
    # "we developed <ToolName>: <description> ( <URL> )" is a self-authorship
    # tool-introduction sentence (Phase 4 review_queue manual review
    # follow-up: PMC13370893's SupeRJump paper). Requires the verb's direct
    # object to be a proper-noun-shaped token (capitalized identifier)
    # immediately followed by ":"/","/"(" -- not a generic noun phrase like
    # "extensive familiarity" or "and validated our workflow" -- so it cannot
    # match sentences that merely mention having worked with a third-party
    # tool.
    r"we\s+(?:developed|created|built|present(?:ed)?|introduce[d]?)\s+"
    r"[A-Za-z][\w-]*\s*[:,(]",
    # "The releases for the publication can be accessed via Zenodo:
    # <name1>: <URL1> and <name2>: <URL2>" (Phase 5 batch review follow-up:
    # PMC13271244's mmContext paper lists two of its own Zenodo releases side
    # by side in one sentence). Restricted to a named archive platform (never
    # a bare "via <anything>") so it cannot pick up the same third-party
    # "available from/via <data source>" wording the at/on-only restriction
    # above already guards against.
    r"(?:can|could)\s+be\s+(?:accessed|obtained|downloaded)\s+via\s+"
    r"(?:zenodo|github|figshare|dryad|code\s*ocean|dataverse)\b",
)
DEPOSIT_STATEMENT_WINDOW_CHARS = 300
# Third-party-dataset-provenance wording ("the X slice was generated by Y and
# is available at <URL>") shares the exact same "is/are available at <URL>"
# surface form as an author depositing their own artifact, but describes
# where a public dataset the paper *reused* came from, not the authors'
# own output (Phase 4 follow-up false positive: PMC13395104's SEDR_analyses
# repo, cited only as the source of a third-party Stereo-seq dataset used for
# benchmarking). A deposit-statement match must be rejected if this wording
# appears in a tight window immediately before the identifier, since that
# clause is what actually supplies the sentence's subject.
THIRD_PARTY_PROVENANCE_PATTERNS = (
    r"(?:was|were)\s+generated\s+by",
    r"(?:the\s+)?dataset\s+(?:was|were)\s+(?:generated|produced|obtained|downloaded)",
    r"(?:publicly\s+)?available\s+from\s+the\s+broad\s+institute",
    # "The <organ/tissue> <assay> dataset is available on/can be accessed via
    # <platform> at <URL>" (Phase 5 batch review follow-up: PMC13355594's
    # SegJointGene paper enumerates several reused third-party benchmark
    # datasets this way -- "the human tonsil proteomics dataset can be
    # accessed via Zenodo at <URL>" -- which the bare "[\w-]+ is/are
    # available at/on" deposit pattern above matches on the literal word
    # "dataset" without checking whether the actual subject is a named
    # dataset citation rather than the authors' own artifact).
    r"dataset\s+(?:is|are|can|could)\s+(?:be\s+)?"
    r"(?:available|accessed|found|hosted|obtained|downloaded)",
    # A marker list or named sample collection can be a borrowed resource even
    # when the paper does not call it a "dataset" (Phase 5 batch-010 review:
    # PMC13459541's singlecell_proteomics marker list and PMC11601167's GIAB
    # samples). Keep the noun set narrow enough that tool names such as HASCAD,
    # SomaticCaller, and CountESS retain their own "is available at" wording.
    r"(?:marker\s+lists?|samples?)\s+(?:is|are|can|could)\s+(?:be\s+)?"
    r"(?:available|accessed|found|hosted|obtained|downloaded)",
    # Reference/benchmark/input resources and raw files are commonly cited as
    # third-party inputs with the same availability verbs (Phase 5 batch-010
    # follow-up). Qualifiers are intentional: bare "data/files are available"
    # remains eligible for an author's own processed output.
    r"(?:reference|public|benchmark|input|raw)\s+(?:data|files?)\s+"
    r"(?:is|are|can|could)\s+(?:be\s+)?"
    r"(?:available|accessed|found|hosted|obtained|downloaded)",
)
THIRD_PARTY_PROVENANCE_WINDOW_CHARS = 80


def _is_enumerated_availability_window(window: str) -> bool:
    """Identify an availability heading followed by a multi-item URL list."""
    if not any(
        re.search(pattern, window, re.IGNORECASE)
        for pattern in DEPOSIT_SECTION_PATTERNS
    ):
        return False

    numbered_entries = re.findall(r"(?:^|[\\s.;])\\d+\\.\\s+", window)
    cited_urls = re.findall(r"(?:https?://|doi\\.org/|10\\.\\d{4,9}/)", window)
    return len(numbered_entries) >= 3 or len(cited_urls) >= 3


def check_explicit_deposit_statement(
    paper_fulltext: str, artifact_metadata: dict
) -> dict:
    """Check for an explicit "code deposited/available at <identifier>" statement.

    Combination (c)'s ``readme_contains_title`` requirement is unreachable for
    research-paper companion-code repos that are just a handful of bare
    analysis scripts with no README/description at all (Phase 4 follow-up
    case: PMC12866134's ASD-microbiome paper, whose repo has zero README but
    whose paper text reads "All original code has been deposited at GitHub
    ... and is publicly available as of the date of publication"). This check
    looks for that class of boilerplate deposit-statement wording within a
    short window around the actual cited identifier, so a strong explicit
    citation sentence can stand in for README-based title matching.
    """
    if not isinstance(paper_fulltext, str) or not paper_fulltext.strip():
        return _result(
            "explicit_deposit_statement", False, "paper full text is empty"
        )

    identifiers = _artifact_identifiers(artifact_metadata)
    if not identifiers:
        return _result(
            "explicit_deposit_statement",
            False,
            "artifact metadata contains no DOI or URL",
        )

    normalized_text = _normalize_citation_text(paper_fulltext)
    for identifier in identifiers:
        variants = _citation_variants(identifier)
        for variant in variants:
            if not variant:
                continue
            # A paper may cite the same artifact identifier more than once
            # (Phase 4 review_queue manual review follow-up: PMC13034549's
            # QSP_nextflow pipeline is first mentioned in passing during the
            # Methods section, and only the *second* mention -- "The pipeline
            # ... can be accessed at <URL>" in the Data availability section
            # -- carries deposit-statement wording). Checking only the first
            # occurrence (the old behaviour) makes any later, stronger
            # citation of the same identifier invisible to this check.
            position = normalized_text.find(variant)
            while position != -1:
                window_start = max(0, position - DEPOSIT_STATEMENT_WINDOW_CHARS)
                window_end = min(
                    len(normalized_text),
                    position + len(variant) + DEPOSIT_STATEMENT_WINDOW_CHARS,
                )
                window = normalized_text[window_start:window_end]
                provenance_window_start = max(
                    0, position - THIRD_PARTY_PROVENANCE_WINDOW_CHARS
                )
                provenance_window = normalized_text[provenance_window_start:position]
                if any(
                    re.search(pattern, provenance_window, re.IGNORECASE)
                    for pattern in THIRD_PARTY_PROVENANCE_PATTERNS
                ):
                    position = normalized_text.find(variant, position + 1)
                    continue
                for pattern in DEPOSIT_STATEMENT_PATTERNS:
                    # A section heading alone is not authorship evidence when
                    # it introduces a numbered list of unrelated tools (Phase 5
                    # batch-010 review: PMC12727693's seven third-party tools).
                    # Other sentence-level patterns in the same window remain
                    # eligible, so explicit self-deposit statements are intact.
                    if (
                        pattern in DEPOSIT_SECTION_PATTERNS
                        and _is_enumerated_availability_window(window)
                    ):
                        continue
                    if re.search(pattern, window, re.IGNORECASE):
                        return _result(
                            "explicit_deposit_statement",
                            True,
                            f"deposit-statement wording found near cited identifier '{identifier}'",
                            matched_identifier=identifier,
                        )
                position = normalized_text.find(variant, position + 1)

    return _result(
        "explicit_deposit_statement",
        False,
        "no explicit deposit-statement wording found near a cited identifier",
    )


def check_owner_matches_author(
    artifact_metadata: dict, paper_authors: list[str]
) -> dict:
    """Check GitHub owner/organization or Zenodo creators against paper authors."""
    artifact_names = _artifact_owner_names(artifact_metadata)
    authors = [str(author).strip() for author in paper_authors or [] if str(author).strip()]

    if not artifact_names:
        return _result(
            "owner_matches_author", False, "artifact owner or creator is unavailable"
        )
    if not authors:
        return _result(
            "owner_matches_author", False, "paper author list is unavailable"
        )

    for artifact_name in artifact_names:
        for author in authors:
            if _names_may_match(artifact_name, author):
                return _result(
                    "owner_matches_author",
                    True,
                    f"artifact owner/creator '{artifact_name}' matches author '{author}'",
                    matched_owner=artifact_name,
                    matched_author=author,
                )

    return _result(
        "owner_matches_author",
        False,
        "artifact owner/creators do not overlap the paper author list",
    )


def check_readme_contains_title(
    readme_or_description: str,
    paper_title: str,
    embedding_model=None,
) -> dict:
    """Check title containment/keyword overlap and optional embedding similarity."""
    text = readme_or_description if isinstance(readme_or_description, str) else ""
    title = paper_title if isinstance(paper_title, str) else ""
    if not text.strip() or not title.strip():
        return _result(
            "readme_contains_title", False, "README/description or paper title is empty"
        )

    normalized_text = _normalize_words(text)
    normalized_title = _normalize_words(title)
    title_words = _informative_title_words(title)
    matched_words = sorted(word for word in title_words if word in normalized_text.split())
    keyword_overlap = len(matched_words) / len(title_words) if title_words else 0.0
    substring_match = bool(normalized_title and normalized_title in normalized_text)
    lexical_passed = substring_match or (
        len(matched_words) >= 3 and keyword_overlap >= 0.6
    )

    score = None
    embedding_passed = False
    embedding_error = None
    if embedding_model is not None:
        embedding_result = check_embedding_similarity(text, title, embedding_model)
        score = embedding_result["score"]
        embedding_passed = score >= EMBEDDING_HIGH_SIMILARITY_THRESHOLD
        if not embedding_result["passed"] and embedding_result.get("error"):
            embedding_error = embedding_result["error"]

    passed = lexical_passed or embedding_passed
    details = [
        f"title substring match={substring_match}",
        f"keyword overlap={keyword_overlap:.3f}",
    ]
    if score is not None:
        details.append(f"embedding cosine similarity={score:.3f}")
    if embedding_error:
        details.append(f"embedding error={embedding_error}")

    extra = {
        "keyword_overlap": round(keyword_overlap, 6),
        "matched_keywords": matched_words,
    }
    if score is not None:
        extra["score"] = score
    return _result("readme_contains_title", passed, "; ".join(details), **extra)


def check_embedding_similarity(
    text_a: str, text_b: str, embedding_model
) -> dict:
    """Compute sentence-transformers cosine similarity in the inclusive 0-1 range."""
    if embedding_model is None:
        return _result(
            "embedding_similarity",
            False,
            "embedding model is unavailable",
            score=0.0,
        )
    if not isinstance(text_a, str) or not text_a.strip():
        return _result(
            "embedding_similarity", False, "first text is empty", score=0.0
        )
    if not isinstance(text_b, str) or not text_b.strip():
        return _result(
            "embedding_similarity", False, "second text is empty", score=0.0
        )

    try:
        embeddings = embedding_model.encode([text_a, text_b])
        vector_a = _to_float_vector(embeddings[0])
        vector_b = _to_float_vector(embeddings[1])
        cosine = _cosine_similarity(vector_a, vector_b)
    except Exception as error:
        return _result(
            "embedding_similarity",
            False,
            f"embedding similarity failed: {type(error).__name__}",
            score=0.0,
            error=str(error),
        )

    score = min(max(cosine, 0.0), 1.0)
    passed = score >= EMBEDDING_HIGH_SIMILARITY_THRESHOLD
    return _result(
        "embedding_similarity",
        passed,
        f"embedding cosine similarity={score:.3f}",
        score=round(score, 6),
    )


def check_dir_structure(top_level_directories: list[str]) -> list[dict]:
    """Return zero or one applicable directory evidence result in a list.

    A high-probability directory produces ``dir_high_prob_present``. Otherwise,
    if at least one low-probability directory is present, the function produces
    ``dir_low_prob_only``. An empty/unrecognized directory list produces ``[]``.
    """
    normalized_directories = {
        _normalize_directory_name(directory)
        for directory in top_level_directories or []
        if isinstance(directory, str) and directory.strip()
    }
    high_hits = sorted(normalized_directories & HIGH_PROBABILITY_DIRECTORIES)
    if high_hits:
        return [
            _result(
                "dir_high_prob_present",
                True,
                f"high-probability directories present: {', '.join(high_hits)}",
                matched_directories=high_hits,
            )
        ]

    low_hits = sorted(normalized_directories & LOW_PROBABILITY_DIRECTORIES)
    if low_hits:
        return [
            _result(
                "dir_low_prob_only",
                True,
                f"only low-probability directory signals present: {', '.join(low_hits)}",
                matched_directories=low_hits,
            )
        ]
    return []


def check_domain_keyword_hit(text: str) -> dict:
    """Check bioinformatics domain keywords used as positive auxiliary evidence."""
    matches = _keyword_matches(text, DOMAIN_KEYWORDS)
    return _result(
        "domain_keyword_hit",
        bool(matches),
        _keyword_detail("domain", matches),
        matches=matches,
        match_count=len(matches),
    )


def check_tool_keyword_hit(text: str) -> dict:
    """Check bioinformatics tool keywords used as positive auxiliary evidence."""
    matches = _keyword_matches(text, TOOL_KEYWORDS)
    return _result(
        "tool_keyword_hit",
        bool(matches),
        _keyword_detail("tool", matches),
        matches=matches,
        match_count=len(matches),
    )


def _result(check: str, passed: bool, detail: str, **extra) -> dict:
    result = {"check": check, "passed": bool(passed), "detail": detail}
    result.update(extra)
    return result


def _artifact_identifiers(metadata: Mapping | None) -> list[str]:
    if not isinstance(metadata, Mapping):
        return []

    identifiers = []
    for key in ("doi", "url", "html_url", "repository_url", "cited_url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            identifiers.append(value.strip())

    # Zenodo's API can report a different version DOI for record.metadata.doi
    # than the one actually cited in the paper (concept vs. version DOI drift).
    # record_id is parsed directly from the citation URL/DOI the paper text
    # matched, so it is always the identifier that was actually cited.
    record_id = metadata.get("record_id")
    if isinstance(record_id, str) and record_id.strip():
        identifiers.append(f"10.5281/zenodo.{record_id.strip()}")

    return list(dict.fromkeys(identifiers))


def _citation_variants(identifier: str) -> set[str]:
    normalized = _normalize_citation_text(identifier)
    variants = {normalized}
    doi_match = re.search(r"10\.\d{4,9}/[^\s]+", normalized, re.IGNORECASE)
    if doi_match:
        doi = doi_match.group(0).rstrip(".,;:!?)]}")
        variants.update({doi, f"doi.org/{doi}", f"https://doi.org/{doi}"})

    parsed = urlparse(identifier if "://" in identifier else f"https://{identifier}")
    if parsed.netloc:
        path = parsed.path.rstrip("/").casefold()
        host_and_path = f"{parsed.netloc.casefold()}{path}"
        variants.update({host_and_path, f"https://{host_and_path}", f"http://{host_and_path}"})
    return {_normalize_citation_text(variant) for variant in variants if variant}


def _normalize_citation_text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().replace("\\/", "/")


def _artifact_owner_names(metadata: Mapping | None) -> list[str]:
    if not isinstance(metadata, Mapping):
        return []

    names = []
    for key in ("owner", "organization", "org"):
        value = metadata.get(key)
        if isinstance(value, Mapping):
            value = value.get("login") or value.get("name")
        if isinstance(value, str) and value.strip():
            names.append(value.strip())

    for creator in metadata.get("creators") or []:
        if isinstance(creator, Mapping):
            value = creator.get("name")
            if not value:
                value = " ".join(
                    str(creator.get(key, "")).strip()
                    for key in ("given_name", "family_name")
                ).strip()
        else:
            value = creator
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    return list(dict.fromkeys(names))


def _names_may_match(artifact_name: str, author_name: str) -> bool:
    artifact_tokens = _name_tokens(artifact_name)
    author_tokens = _name_tokens(author_name)
    if not artifact_tokens or not author_tokens:
        return False

    artifact_compact = "".join(artifact_tokens)
    author_compact = "".join(author_tokens)
    if artifact_compact == author_compact:
        return True
    if len(author_compact) >= 5 and author_compact in artifact_compact:
        return True

    meaningful_author_tokens = {token for token in author_tokens if len(token) >= 3}
    if not meaningful_author_tokens:
        return False
    overlap = {
        token
        for token in meaningful_author_tokens
        if token in artifact_tokens or token in artifact_compact
    }
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1:
        token = next(iter(overlap))
        return len(token) >= 5 or (
            len(token) >= 4
            and (
                any(len(item) == 1 for item in author_tokens)
                or len(author_tokens) == 1
                or token == max(author_tokens, key=len)
            )
        )
    return False


def _name_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    tokens = re.findall(r"[A-Za-z0-9]+", normalized.casefold())
    # Generic institutional/affiliation words. Author strings often carry the
    # full affiliation line (e.g. "... Institute of Basic Medical Science,
    # Cancer Research Center, Shantou University Medical College ..."), and
    # without this list a token like "research" or "center" can spuriously
    # overlap with an unrelated org account name that happens to also contain
    # it (Phase 4 76-artifact manual review: github.com/biomap-research/
    # scFoundation matched a Shantou University author purely via the shared
    # word "research", despite biomap-research being an unrelated commercial
    # AI lab with zero connection to that author).
    ignored = {
        "lab",
        "labs",
        "group",
        "team",
        "org",
        "organization",
        "research",
        "center",
        "centre",
        "institute",
        "institution",
        "university",
        "college",
        "school",
        "department",
        "departments",
        "hospital",
        "medical",
        "science",
        "sciences",
        "national",
        "laboratory",
        "laboratories",
    }
    return [token for token in tokens if token not in ignored]


def _normalize_words(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _informative_title_words(title: str) -> set[str]:
    return {
        word
        for word in _normalize_words(title).split()
        if len(word) >= 4 and word not in TITLE_STOP_WORDS
    }


def _to_float_vector(vector) -> list[float]:
    if hasattr(vector, "detach"):
        vector = vector.detach()
    if hasattr(vector, "cpu"):
        vector = vector.cpu()
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if len(vector_a) != len(vector_b) or not vector_a:
        raise ValueError("embedding vectors must have the same non-zero length")
    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(value * value for value in vector_a))
    norm_b = math.sqrt(sum(value * value for value in vector_b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("embedding vectors must have non-zero magnitude")
    return dot_product / (norm_a * norm_b)


def _normalize_directory_name(directory: str) -> str:
    return directory.strip().strip("/\\").casefold()


def _keyword_matches(text: str, keyword_patterns: Mapping[str, Iterable[str]]) -> list[str]:
    value = text if isinstance(text, str) else ""
    return [
        keyword
        for keyword, patterns in keyword_patterns.items()
        if any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)
    ]


def _keyword_detail(keyword_type: str, matches: list[str]) -> str:
    if not matches:
        return f"no {keyword_type} keywords found"
    return f"{keyword_type} keywords found: {', '.join(matches)}"
