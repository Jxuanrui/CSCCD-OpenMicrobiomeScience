"""Regression tests for classifier/score.py and classifier/evidence.py.

Each test pins down a real edge case discovered via Phase 4 cross-verification
against live papers/artifacts. See design.md's False Positive defense section
and combination (c) rationale for the full narrative behind each case.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from classifier import score
from classifier import evidence as ev
import run_batch as rb


def _readme_evidence(passed, keyword_overlap=0.0, embedding_score=None, substring_match=False):
    detail = f"title substring match={substring_match}; keyword overlap={keyword_overlap:.3f}"
    item = {
        "check": "readme_contains_title",
        "passed": passed,
        "detail": detail,
        "keyword_overlap": keyword_overlap,
    }
    if embedding_score is not None:
        item["score"] = embedding_score
        item["detail"] += f"; embedding cosine similarity={embedding_score:.3f}"
    return item


def test_embedding_only_similarity_cannot_unlock_hard_gate():
    """LiMCA regression: a genuine third-party tool must not be confirmed just
    because its README is topically similar (embedding cosine=0.775) to an
    unrelated paper that happens to cite its DOI/URL. Both are single-cell
    Hi-C tools, so embeddings alone cannot tell them apart; only lexical
    overlap (substring match or real keyword_overlap) may unlock combo (b)/(c).
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": True, "detail": "x"},
        {"check": "owner_matches_author", "passed": False, "detail": "x"},
        _readme_evidence(passed=True, keyword_overlap=0.5, embedding_score=0.775),
        {"check": "dir_high_prob_present", "passed": True, "detail": "x"},
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] != "confirmed"


def test_genuine_lexical_title_match_still_confirms_via_combo_c():
    """A real substring title match plus a direct DOI/URL citation must still
    reach `confirmed` via combination (c), even without owner/author string
    matching (lab/team GitHub accounts are common and should not block this).
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": True, "detail": "x"},
        {"check": "owner_matches_author", "passed": False, "detail": "x"},
        _readme_evidence(passed=True, keyword_overlap=1.0, substring_match=True),
        {"check": "dir_high_prob_present", "passed": True, "detail": "x"},
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] == "confirmed"


def test_owner_match_plus_citation_confirms_via_combo_a():
    """Combination (a): DOI/URL citation + owner matching the paper's authors
    is sufficient on its own, regardless of README content.
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": True, "detail": "x"},
        {"check": "owner_matches_author", "passed": True, "detail": "x"},
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] == "confirmed"


def test_third_party_tool_without_citation_or_title_match_is_not_confirmed():
    """A repo that only hits domain/tool keywords (e.g. mentions Seurat/Scanpy
    heavily) but has no DOI/URL citation, no owner match, and no title overlap
    must never reach `confirmed` -- keyword density alone is not evidence of
    authorship (see design.md's core false-positive defense principle).
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": False, "detail": "x"},
        {"check": "owner_matches_author", "passed": False, "detail": "x"},
        {"check": "domain_keyword_hit", "passed": True, "detail": "x"},
        {"check": "tool_keyword_hit", "passed": True, "detail": "x"},
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] != "confirmed"


def test_same_domain_third_party_workflow_with_high_keyword_overlap_not_confirmed():
    """wf-somatic-variation regression: a genuine third-party workflow
    (epi2me-labs, unrelated owner) that happens to share heavy domain
    vocabulary with the cited paper's own tool (ClairS) reached
    keyword_overlap=0.769 -- above the old 0.75 combo(c) threshold -- purely
    because both describe somatic/tumor/normal/variant/calling pipelines.
    Pure keyword_overlap (no exact substring match) must require a much
    higher score than embedding-based signals to unlock combo (c); the old
    0.75 threshold was crossed by domain-vocabulary overlap alone.
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": True, "detail": "x"},
        {"check": "owner_matches_author", "passed": False, "detail": "x"},
        _readme_evidence(passed=True, keyword_overlap=0.769, embedding_score=0.592),
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] != "confirmed"


def test_explicit_deposit_statement_confirms_readmeless_companion_code():
    """ASD-microbiome regression (PMC12866134): a research-paper companion-code
    repo can be nothing but a handful of bare analysis scripts with zero
    README/description, making combo (b)/(c) unreachable even when the paper's
    citation sentence is unambiguous ("All original code has been deposited at
    GitHub ... and is publicly available as of the date of publication").
    Combination (d) must confirm on that explicit deposit-statement wording
    alone, without requiring owner match or README title overlap.
    """
    evidence = [
        {"check": "doi_cited_in_text", "passed": True, "detail": "x"},
        {"check": "explicit_deposit_statement", "passed": True, "detail": "x"},
        {"check": "owner_matches_author", "passed": False, "detail": "x"},
        {"check": "readme_contains_title", "passed": False, "detail": "x"},
    ]
    result = score.classify_artifact({}, {}, evidence)
    assert result["status"] == "confirmed"


def test_deposit_statement_recognizes_availability_and_implementation_boilerplate():
    """Cross-verify follow-up (map3C/PRISM/NestedWGCNA): tool-paper 'Availability
    and implementation' sections phrase the citation as '<ToolName> is
    available at <URL>', not literally 'code is deposited/available'. The
    original combo (d) patterns missed this and left these under-confident at
    the review_queue confidence cap; the wording must be recognized too.
    """
    metadata = {"url": "https://github.com/luogenomics/map3C"}
    paper_fulltext = (
        "Availability and implementation map3C is available at "
        "https://github.com/luogenomics/map3C and is archived at "
        "https://doi.org/10.5281/zenodo.20724719 ."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is True


def test_deposit_statement_recognizes_we_provide_phrasing():
    """Cross-verify follow-up (nekrut/LLM-eval-paper): 'we provide ... at <URL>'
    is a common artifact-citation phrasing that the original narrow pattern
    set (literally 'code'/'software' as the subject) missed.
    """
    metadata = {"url": "https://github.com/nekrut/LLM-eval-paper"}
    paper_fulltext = (
        "we provide the plans, harness, scoring code, and per-cell artifacts "
        "at https://github.com/nekrut/LLM-eval-paper as a framework for "
        "re-evaluating future models."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is True


def test_deposit_statement_rejects_third_party_data_availability_wording():
    """Near-miss found during the broadened-pattern validation (PMC12910371):
    'Data availability ... is publicly available from ENCODE and maxATAC' is
    a citation of *reused third-party* data, not the authors depositing their
    own artifact. 'available from/via <source>' must not qualify; only the
    'here is where MY tool/data lives' phrasing ('available at/on <URL>')
    should.
    """
    metadata = {"doi": "10.5281/zenodo.6761768"}
    paper_fulltext = (
        "Data availability All data used in this work is publicly available "
        "from ENCODE and maxATAC (DOI: https://doi.org/10.5281/zenodo.6761768 )."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is False


def test_deposit_statement_rejects_third_party_provenance_wording():
    """76-artifact manual review follow-up (PMC13395104's SEDR_analyses repo):
    'the mouse olfactory bulb slice was generated by Stereo-seq and is
    available at <URL>' shares the exact 'is available at <URL>' surface form
    as an author depositing their own artifact, but the "was generated by"
    clause immediately before the URL identifies it as one of several
    third-party benchmark datasets the paper reused, not the authors' own
    output. This is a deeper false-positive mode than the maxATAC
    from/via-preposition case: here the preposition is "at" (normally safe),
    so it must be caught by inspecting the clause right before the identifier
    instead.
    """
    metadata = {"url": "https://github.com/JinmiaoChenLab/SEDR_analyses"}
    paper_fulltext = (
        "the mouse olfactory bulb slice was generated by Stereo-seq and is "
        "available at https://github.com/JinmiaoChenLab/SEDR_analyses ; and "
        "(7) the Slide-seqV2 dataset is available from the Broad Institute."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is False


def test_deposit_statement_recognizes_accessed_via_platform_phrasing():
    """Phase 5 batch-002 review follow-up (PMC13271244's mmContext paper):
    'The releases for the publication can be accessed via Zenodo:
    adata_hf_datasets: <URL1> and mmContext: <URL2>' lists two of the
    authors' own Zenodo releases side by side in one sentence. Neither the
    old patterns nor the at/on-only restriction on the bare '<name> is/are
    available at/on' pattern matched this 'can be accessed via <platform>'
    phrasing, so the second-listed release (mmContext) fell through to
    review_queue while the first (adata_hf_datasets) only passed by
    incidentally sitting inside an unrelated neighbour sentence's 300-char
    window ('... are available on Hugging Face ...').
    """
    metadata = {"url": "https://doi.org/10.5281/zenodo.19185493"}
    paper_fulltext = (
        "The releases for the publication can be accessed via Zenodo: "
        "adata_hf_datasets: doi.org/10.5281/zenodo.19185217 and mmContext: "
        "doi.org/10.5281/zenodo.19185493"
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is True


def test_deposit_statement_rejects_dataset_is_available_wording():
    """Phase 5 batch-001 review follow-up (PMC13355594's SegJointGene paper):
    'The mouse hippocampus dataset is available on Figshare at <URL1>. ...
    The human tonsil proteomics dataset can be accessed via Zenodo at
    <URL2>.' enumerates several *reused third-party benchmark datasets*.
    The bare '<name> is/are available at/on' pattern's [\\w-]+ subject
    matched the literal word 'dataset' with no check that the actual
    subject is a named dataset citation rather than the authors' own
    artifact -- letting the tonsil-dataset identifier incidentally borrow
    the neighbouring hippocampus-dataset sentence's 'is available on
    Figshare at' wording within its 300-char window and get misclassified
    as the authors' own deposit.
    """
    metadata = {"url": "https://doi.org/10.5281/zenodo.10982119"}
    paper_fulltext = (
        "The mouse hippocampus dataset is available on Figshare at "
        "https://doi.org/10.6084/m9.figshare.7150760 . The whole mouse "
        "brain dataset is hosted on the Brain Knowledge Platform at "
        "https://knowledge.brain-map.org/data/LVDBJAW8BI5YSS1QUBG . The "
        "human tonsil proteomics dataset can be accessed via Zenodo at "
        "https://doi.org/10.5281/zenodo.10982119 ."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is False


def test_deposit_statement_rejects_enumerated_third_party_tool_list():
    """Phase 5 batch-010 regression (PMC12727693): a Code availability
    heading can introduce a numbered list of unrelated tools. The heading must
    not independently confirm the third-party cutadapt repository.
    """
    paper_fulltext = (
        "Code availability 1. Time Analysis software: https://example.org/time . "
        "2. Bcl2fastq Conversion: https://example.org/bcl2fastq . "
        "3. Cutadapt, https://github.com/marcelm/cutadapt/releases/tag/v1.18 . "
        "4. Fastx_clean software: https://example.org/fastx . "
        "5. FASTX-Toolkit: https://example.org/fastx-toolkit . "
        "6. SortMeRNA v2.1: https://example.org/sortmerna . "
        "7. fastx_estimate_duplicate software: https://example.org/duplicate ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext,
        {"url": "https://github.com/marcelm/cutadapt/releases/tag/v1.18"},
    )
    assert result["passed"] is False


def test_deposit_statement_rejects_borrowed_marker_list_availability():
    """Phase 5 batch-010 regression (PMC13459541): a marker list borrowed
    from another study must not be treated as the authors' own repository.
    """
    paper_fulltext = (
        "The cell-cycle-specific marker list was obtained from the original "
        "study's Supporting Information. The cell cycle marker list is available "
        "at https://github.com/theislab/singlecell_proteomics ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext,
        {"url": "https://github.com/theislab/singlecell_proteomics"},
    )
    assert result["passed"] is False


def test_deposit_statement_rejects_reference_samples_availability():
    """Phase 5 batch-010 regression (PMC11601167): public reference samples
    such as GIAB samples are third-party inputs, not author-produced artifacts.
    """
    paper_fulltext = (
        "HPRC samples can be found at https://example.org/hprc . GIAB samples "
        "can be found at https://github.com/genome-in-a-bottle/giab_data_indexes ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext,
        {"url": "https://github.com/genome-in-a-bottle/giab_data_indexes"},
    )
    assert result["passed"] is False


def test_deposit_statement_keeps_author_tool_availability_regressions():
    """Phase 5 batch-010 controls: explicit tool-name availability statements
    remain positive even after third-party noun provenance is broadened.
    """
    cases = (
        (
            "The model of HASCAD is available at https://github.com/holiday01/HASCAD .",
            "https://github.com/holiday01/HASCAD",
        ),
        (
            "The software is open source and freely available at "
            "https://github.com/SomaticCaller/SomaticCaller .",
            "https://github.com/SomaticCaller/SomaticCaller",
        ),
        (
            "CountESS is freely available at https://github.com/CountESS-Project/CountESS "
            "and countess-demo is freely available at "
            "https://github.com/CountESS-Project/countess-demo .",
            "https://github.com/CountESS-Project/CountESS",
        ),
        (
            "CountESS is freely available at https://github.com/CountESS-Project/CountESS "
            "and countess-demo is freely available at "
            "https://github.com/CountESS-Project/countess-demo .",
            "https://github.com/CountESS-Project/countess-demo",
        ),
        (
            "Availability and implementation D2H2 is available at: "
            "https://github.com/MaayanLab/D2H2-site .",
            "https://github.com/MaayanLab/D2H2-site",
        ),
        (
            "Processed data and code are hosted by GitHub and are available at "
            "https://github.com/DU-omics/eoe-meta-analysis_data .",
            "https://github.com/DU-omics/eoe-meta-analysis_data",
        ),
    )
    for paper_fulltext, url in cases:
        result = ev.check_explicit_deposit_statement(paper_fulltext, {"url": url})
        assert result["passed"] is True, url


def test_deposit_statement_requires_proximity_to_cited_identifier():
    """A generic 'code availability' boilerplate elsewhere in the paper (e.g.
    describing a different, unrelated tool) must not trigger combo (d) for an
    artifact whose own citation is far away and unrelated to that wording.
    """
    metadata = {"url": "https://github.com/someone/unrelated-repo"}
    paper_fulltext = (
        "Data and code availability: the RNA-seq pipeline used here is "
        "described elsewhere. " + ("filler text " * 200) +
        "A separate exploratory script is hosted at "
        "https://github.com/someone/unrelated-repo for reference only."
    )
    result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert result["passed"] is False


def test_deposit_statement_recognizes_multi_repo_list_citation_style():
    """review_queue manual review follow-up (PMC13411100, neurogenomics): a
    paper introducing several of its own R packages cites them as a list
    under "Software and data availability" -- "R packages introduced in this
    study: KGExplorer (URL), HPOExplorer (URL) ... Manuscript analyses and
    reproducibility code: URL" -- rather than an "is available at" sentence.
    This is an unambiguous author-authorship statement (there is no
    third-party-citation reading of "introduced in this study" or
    "reproducibility code"), but none of the sentence-style patterns matched
    it, leaving five of the paper's own repos stuck at the review_queue
    confidence cap.
    """
    paper_fulltext = (
        "Software and data availability Interactive web portal: "
        "https://neurogenomics-ukdri.dsi.ic.ac.uk/ . R packages introduced in "
        "this study: KGExplorer ( https://github.com/neurogenomics/KGExplorer "
        "), HPOExplorer ( https://github.com/neurogenomics/HPOExplorer ), and "
        "MSTExplorer ( https://github.com/neurogenomics/MSTExplorer ). "
        "Manuscript analyses and reproducibility code: "
        "https://github.com/neurogenomics/rare_disease_celltyping ."
    )
    for url in (
        "https://github.com/neurogenomics/KGExplorer",
        "https://github.com/neurogenomics/rare_disease_celltyping",
    ):
        result = ev.check_explicit_deposit_statement(paper_fulltext, {"url": url})
        assert result["passed"] is True


def test_deposit_statement_recognizes_can_be_found_at_phrasing():
    """review_queue manual review follow-up (PMC13411100, neurogenomics): the
    paper's sixth own repo, rare-disease-web-portal, is cited via "All code
    used to generate the website can be found at <URL>" -- a phrasing none of
    the existing "is/are available"-style patterns matched. Restricted to
    code/software-artifact nouns (not a bare dataset noun) so it cannot pick
    up a third-party reused-dataset "can be found at" citation.
    """
    paper_fulltext = (
        "All code used to generate the website can be found at "
        "https://github.com/neurogenomics/Rare-Disease-Web-Portal ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext,
        {"url": "https://github.com/neurogenomics/rare-disease-web-portal"},
    )
    assert result["passed"] is True


def test_deposit_statement_can_be_found_at_excludes_dataset_nouns():
    """The 'can be found at' phrasing must not fire for a bare dataset/sequence
    noun, which is the typical shape of a third-party-data citation rather
    than an author depositing their own code/software artifact.
    """
    paper_fulltext = (
        "The reference genome sequence can be found at "
        "https://example.com/genome-repo ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext, {"url": "https://example.com/genome-repo"}
    )
    assert result["passed"] is False


def test_deposit_statement_recognizes_we_developed_toolname_phrasing():
    """review_queue manual review follow-up (PMC13370893, SupeRJump): "we
    developed SupeRJump: a jump-drift-diffusion based supervised cell-fate
    model ( <URL> )" is an unambiguous self-authorship tool-introduction
    sentence, but none of the existing "is/are available" patterns matched
    it since there is no "available" wording at all -- the citation is a
    parenthetical right after the tool's own name.
    """
    paper_fulltext = (
        "we developed SupeRJump: a jump-drift-diffusion based supervised "
        "cell-fate model ( https://github.com/namwob44/SupeRJump/ )."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext, {"url": "https://github.com/namwob44/superjump"}
    )
    assert result["passed"] is True


def test_deposit_statement_we_developed_excludes_generic_noun_phrases():
    """The "we developed/created/built/presented X" pattern must require a
    proper-noun-shaped direct object immediately followed by ":"/","/"(" so
    it cannot match a sentence merely mentioning familiarity with, or having
    benchmarked against, a third-party tool.
    """
    paper_fulltext = (
        "Other tools such as Seurat, which we developed extensive "
        "familiarity with during preliminary work, and its variants "
        "(https://github.com/satijalab/seurat) were benchmarked."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext, {"url": "https://github.com/satijalab/seurat"}
    )
    assert result["passed"] is False


def test_deposit_statement_recognizes_license_clause_between_available_and_preposition():
    """review_queue manual review follow-up (PMC13308449, PyPeakRankR): "X is
    freely available under the MIT license at <URL>" inserts a licence
    clause between "available" and "at", which the original wildcard
    pattern -- requiring "available" to be immediately followed by "at/on"
    -- missed.
    """
    paper_fulltext = (
        "PyPeakRankR is freely available under the MIT license at "
        "https://github.com/AllenInstitute/PeakRankR/tree/python-package ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext, {"url": "https://github.com/AllenInstitute/PeakRankR"}
    )
    assert result["passed"] is True


def test_deposit_statement_checks_every_occurrence_of_a_repeated_citation():
    """review_queue manual review follow-up (PMC13034549, QSP_nextflow): a
    paper can cite the same artifact identifier more than once, with only a
    later occurrence carrying deposit-statement wording ("Sequencing reads
    were processed using a pipeline ... available at <URL>" in Methods,
    followed much later by "The pipeline to process raw fastq files can be
    accessed at <URL>" in Data availability). The check must not stop after
    inspecting only the first occurrence of a cited identifier.
    """
    paper_fulltext = (
        "Sequencing reads were processed using a pipeline implemented in "
        "Nextflow v25.04 available at https://github.com/OncoRNALab/QSP_nextflow.git . "
        + ("Unrelated methods text discussing read trimming and alignment. " * 30)
        + "The pipeline to process raw fastq files can be accessed at "
        "https://github.com/OncoRNALab/QSP_nextflow.git ."
    )
    result = ev.check_explicit_deposit_statement(
        paper_fulltext, {"url": "https://github.com/OncoRNALab/QSP_nextflow"}
    )
    assert result["passed"] is True


def test_extract_fulltext_context_keeps_authors_with_long_affiliation_text():
    """Phase 5 batch-005 review follow-up (PMC13069690's SpNeigh paper): a
    JATS <contrib> node's itertext() naturally concatenates the ORCID ID,
    name, and full multi-line institute address, routinely landing at
    200-600+ characters -- all three of this paper's authors did. The old
    len(candidate) < 200 cutoff silently dropped every one of them, leaving
    owner_matches_author permanently stuck on "paper author list is
    unavailable" even though one author's literal name (Cheng Jinming)
    matches the artifact owner's GitHub username (jinming-cheng) verbatim.
    """
    fulltext = (
        "<article><contrib-group>"
        "<contrib contrib-type=\"author\">"
        "<contrib-id>https://orcid.org/0000-0003-3806-4694</contrib-id>"
        "<name><surname>Cheng</surname><given-names>Jinming</given-names></name>"
        "<aff>Centre for Biomedical Data Science, Duke-NUS Medical School, "
        "Singapore 169857, Singapore</aff>"
        "<aff>Duke-NUS AI + Medical Sciences Initiative, Duke-NUS Medical "
        "School, Singapore 169857, Singapore</aff>"
        "</contrib>"
        "</contrib-group></article>"
    )
    context = rb._extract_fulltext_context(fulltext)
    assert len(context["authors"]) == 1
    assert "Cheng" in context["authors"][0]
    assert "Jinming" in context["authors"][0]

    result = ev.check_owner_matches_author(
        {"owner": "jinming-cheng"}, context["authors"]
    )
    assert result["passed"] is True


def test_cited_url_recovers_doi_cited_in_text_despite_github_ownership_transfer():
    """Phase 5 batch-004 review follow-up (PMC11058068's pyaging paper): the
    repo was renamed/transferred from rsinghlab/pyaging (the URL actually
    cited in the paper text) to lucascamillomd/pyaging (the author's personal
    account, the strongest possible owner_matches_author signal) between
    publication and pipeline run time. github_provider.fetch_metadata always
    reports the *current* canonical html_url, so without run_batch.py's
    cited_url passthrough the paper-cited identifier is lost entirely and
    doi_cited_in_text/explicit_deposit_statement become silently unreachable
    even though the citation is right there in the text. Same class of
    provider-current-state-vs-paper-cited-state drift as Zenodo's
    record_id-based fix, just on the GitHub side.
    """
    metadata = {
        "url": "https://github.com/lucascamillomd/pyaging",
        "cited_url": "https://github.com/rsinghlab/pyaging",
    }
    paper_fulltext = (
        "Availability and implementation: pyaging is accessible on GitHub, "
        "at https://github.com/rsinghlab/pyaging , and the distribution is "
        "available on PyPI."
    )
    result = ev.check_doi_cited_in_text(paper_fulltext, metadata)
    assert result["passed"] is True

    deposit_result = ev.check_explicit_deposit_statement(paper_fulltext, metadata)
    assert deposit_result["passed"] is True


def test_owner_matches_author_ignores_generic_institutional_words():
    """76-artifact manual review follow-up (biomap-research/scFoundation, cited
    by an unrelated Shantou University paper): author strings often carry the
    full affiliation line, and a generic word like "research"/"center" can
    spuriously overlap with an org account name that happens to contain the
    same word, with zero actual connection between the two. Institutional
    boilerplate words must be ignored by the token-overlap heuristic, the same
    way "lab"/"team"/"group" already are.
    """
    result = ev.check_owner_matches_author(
        {"owner": "biomap-research"},
        [
            "Long Lin Institute of Basic Medical Science, Cancer Research "
            "Center, Shantou University Medical College, Shantou, Guangdong "
            "515041, China"
        ],
    )
    assert result["passed"] is False


def test_zenodo_record_id_recovers_doi_cited_in_text_despite_api_version_drift():
    """Zenodo's records API can report a different version DOI in
    metadata.doi than the one actually cited in the paper text (concept vs.
    version DOI drift). The record_id parsed directly from the cited
    URL/DOI -- which is always the identifier that was actually cited --
    must still be recognized so doi_cited_in_text does not false-negative.
    """
    metadata = {
        "doi": "10.5281/zenodo.20724720",  # what Zenodo's API reports (drifted)
        "url": "https://zenodo.org/records/20724720",
        "record_id": "20724719",  # what was actually cited in the paper
    }
    paper_fulltext = (
        "map3C is available at https://github.com/luogenomics/map3C and is "
        "archived at https://doi.org/10.5281/zenodo.20724719 ."
    )
    result = ev.check_doi_cited_in_text(paper_fulltext, metadata)
    assert result["passed"] is True
    assert result["matched_identifier"] == "10.5281/zenodo.20724719"
