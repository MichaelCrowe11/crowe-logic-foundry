"""
Research Engine — Literature search, experiment tracking, protocol generation,
knowledge synthesis, and research workflow automation.

Integrates with existing:
- agents/research.yaml (web research specialist)
- tools/search.py (web search)
- tools/browser.py (URL browsing)
- data/crowelm-unified/ (training data)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/research", tags=["research"])


# ── Enums ────────────────────────────────────────────────────────────

class ExperimentStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaperSource(str, Enum):
    PUBMED = "pubmed"
    GOOGLE_SCHOLAR = "google_scholar"
    ARXIV = "arxiv"
    BIORXIV = "biorxiv"
    MYCOLOGICAL_RESEARCH = "mycological_research"
    MANUAL = "manual"


class ResearchDomain(str, Enum):
    MYCOLOGY = "mycology"
    BIOTECH = "biotech"
    COMPOUND_DISCOVERY = "compound_discovery"
    CULTIVATION = "cultivation"
    GENETICS = "genetics"
    ECOLOGY = "ecology"
    MEDICINAL = "medicinal"
    INDUSTRIAL = "industrial"


# ── Models ───────────────────────────────────────────────────────────

class Paper(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    source: PaperSource = PaperSource.MANUAL
    domain: ResearchDomain = ResearchDomain.MYCOLOGY
    publication_date: Optional[date] = None
    journal: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    relevance_score: Optional[float] = None
    added_at: datetime = Field(default_factory=datetime.utcnow)


class Experiment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    hypothesis: str
    methodology: str
    status: ExperimentStatus = ExperimentStatus.PLANNED
    domain: ResearchDomain = ResearchDomain.MYCOLOGY
    variables: dict = Field(default_factory=dict)
    controls: list[str] = Field(default_factory=list)
    expected_outcome: Optional[str] = None
    actual_outcome: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    data_points: list[dict] = Field(default_factory=list)
    related_papers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ResearchNote(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    content: str
    domain: ResearchDomain = ResearchDomain.MYCOLOGY
    related_papers: list[str] = Field(default_factory=list)
    related_experiments: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LiteratureSearchRequest(BaseModel):
    query: str
    domain: Optional[ResearchDomain] = None
    max_results: int = Field(20, ge=1, le=100)
    sources: list[PaperSource] = Field(default_factory=lambda: [PaperSource.PUBMED])


class ProtocolGenerationRequest(BaseModel):
    title: str
    objective: str
    domain: ResearchDomain = ResearchDomain.MYCOLOGY
    materials: list[str] = Field(default_factory=list)
    constraints: Optional[str] = None


# ── In-memory store ──────────────────────────────────────────────────

_papers: dict[str, Paper] = {}
_experiments: dict[str, Experiment] = {}
_notes: dict[str, ResearchNote] = {}


# ── Routes ───────────────────────────────────────────────────────────

# Papers / Literature
@router.post("/papers", response_model=Paper)
async def add_paper(paper: Paper):
    _papers[paper.id] = paper
    return paper


@router.get("/papers", response_model=list[Paper])
async def list_papers(
    domain: Optional[ResearchDomain] = None,
    keyword: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    papers = list(_papers.values())
    if domain:
        papers = [p for p in papers if p.domain == domain]
    if keyword:
        kw = keyword.lower()
        papers = [
            p for p in papers
            if kw in p.title.lower()
            or kw in (p.abstract or "").lower()
            or any(kw in k.lower() for k in p.keywords)
        ]
    return sorted(papers, key=lambda p: p.added_at, reverse=True)[:limit]


@router.get("/papers/{paper_id}", response_model=Paper)
async def get_paper(paper_id: str):
    if paper_id not in _papers:
        raise HTTPException(404, "Paper not found")
    return _papers[paper_id]


@router.post("/literature/search")
async def search_literature(request: LiteratureSearchRequest):
    """Search scientific literature (delegates to research agent tools)."""
    try:
        from tools.search import web_search
        results = json.loads(web_search(
            f"{request.query} site:pubmed.ncbi.nlm.nih.gov OR site:scholar.google.com"
        ))
        return {
            "query": request.query,
            "domain": request.domain,
            "results": results,
            "note": "Live search via web_search tool. Add relevant papers with POST /papers.",
        }
    except (ImportError, Exception) as e:
        return {
            "query": request.query,
            "domain": request.domain,
            "results": [],
            "note": f"Search backend not available: {e}. Add papers manually.",
        }


# Experiments
@router.post("/experiments", response_model=Experiment)
async def create_experiment(experiment: Experiment):
    _experiments[experiment.id] = experiment
    return experiment


@router.get("/experiments", response_model=list[Experiment])
async def list_experiments(
    status: Optional[ExperimentStatus] = None,
    domain: Optional[ResearchDomain] = None,
):
    experiments = list(_experiments.values())
    if status:
        experiments = [e for e in experiments if e.status == status]
    if domain:
        experiments = [e for e in experiments if e.domain == domain]
    return sorted(experiments, key=lambda e: e.created_at, reverse=True)


@router.get("/experiments/{exp_id}", response_model=Experiment)
async def get_experiment(exp_id: str):
    if exp_id not in _experiments:
        raise HTTPException(404, "Experiment not found")
    return _experiments[exp_id]


@router.patch("/experiments/{exp_id}/status")
async def update_experiment_status(exp_id: str, status: ExperimentStatus):
    if exp_id not in _experiments:
        raise HTTPException(404, "Experiment not found")
    exp = _experiments[exp_id]
    exp.status = status
    exp.updated_at = datetime.now(tz=__import__("datetime").timezone.utc)
    if status == ExperimentStatus.IN_PROGRESS and not exp.start_date:
        exp.start_date = date.today()
    elif status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED):
        exp.end_date = date.today()
    return {"id": exp_id, "status": status}


@router.post("/experiments/{exp_id}/data-point")
async def add_data_point(exp_id: str, data_point: dict):
    if exp_id not in _experiments:
        raise HTTPException(404, "Experiment not found")
    _experiments[exp_id].data_points.append({
        "timestamp": datetime.now(tz=__import__("datetime").timezone.utc).isoformat(),
        **data_point,
    })
    _experiments[exp_id].updated_at = datetime.now(tz=__import__("datetime").timezone.utc)
    return {"recorded": True, "total_points": len(_experiments[exp_id].data_points)}


# Research Notes
@router.post("/notes", response_model=ResearchNote)
async def create_note(note: ResearchNote):
    _notes[note.id] = note
    return note


@router.get("/notes", response_model=list[ResearchNote])
async def list_notes(
    domain: Optional[ResearchDomain] = None,
    tag: Optional[str] = None,
):
    notes = list(_notes.values())
    if domain:
        notes = [n for n in notes if n.domain == domain]
    if tag:
        notes = [n for n in notes if tag in n.tags]
    return sorted(notes, key=lambda n: n.created_at, reverse=True)


# Protocol Generation
@router.post("/protocols/generate")
async def generate_protocol(request: ProtocolGenerationRequest):
    """Generate a research protocol template."""
    return {
        "title": request.title,
        "objective": request.objective,
        "domain": request.domain.value,
        "protocol": {
            "1_background": f"Literature review for: {request.objective}",
            "2_hypothesis": "Define testable hypothesis based on background research",
            "3_materials": request.materials or ["List required materials"],
            "4_methodology": [
                "Define experimental groups and controls",
                "Establish measurement parameters and intervals",
                "Set statistical significance thresholds",
                "Document environmental conditions",
            ],
            "5_data_collection": [
                "Create standardized data collection forms",
                "Define measurement timepoints",
                "Establish photo documentation schedule",
            ],
            "6_analysis_plan": [
                "Statistical tests to apply",
                "Expected data visualizations",
                "Success/failure criteria",
            ],
            "7_safety": "Document relevant safety protocols and waste disposal",
        },
        "constraints": request.constraints,
        "note": "Template protocol. Use the research agent for AI-enhanced generation.",
    }


# Analytics
@router.get("/analytics/overview")
async def research_overview():
    return {
        "papers": len(_papers),
        "experiments": {
            "total": len(_experiments),
            "by_status": _count_by_field(_experiments.values(), "status"),
            "by_domain": _count_by_field(_experiments.values(), "domain"),
        },
        "notes": len(_notes),
    }


def _count_by_field(items, field):
    counts = {}
    for item in items:
        val = getattr(item, field, None)
        if val:
            key = val.value if hasattr(val, "value") else str(val)
            counts[key] = counts.get(key, 0) + 1
    return counts


import json  # noqa: E402 (needed for search_literature)
