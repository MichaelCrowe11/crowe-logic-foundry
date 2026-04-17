"""
Mycelium Vision — Computer vision pipeline for mycelium growth analysis,
contamination detection, fruiting body classification, and growth tracking.

Integrates with existing:
- tools/vision.py (multi-backend image analysis)
- agents/cultivation.yaml (crowe_vision tool reference)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

router = APIRouter(prefix="/vision", tags=["vision"])


# ── Enums ────────────────────────────────────────────────────────────

class AnalysisType(str, Enum):
    CONTAMINATION = "contamination"
    GROWTH_STAGE = "growth_stage"
    SPECIES_ID = "species_id"
    MORPHOLOGY = "morphology"
    GENERAL = "general"


class GrowthStage(str, Enum):
    NO_GROWTH = "no_growth"
    EARLY_COLONIZATION = "early_colonization"
    MID_COLONIZATION = "mid_colonization"
    FULL_COLONIZATION = "full_colonization"
    PRIMORDIA = "primordia"
    PIN_SET = "pin_set"
    YOUNG_FRUITING = "young_fruiting"
    MATURE_FRUITING = "mature_fruiting"
    SPORULATING = "sporulating"


class ContaminationSeverity(str, Enum):
    NONE = "none"
    SUSPECTED = "suspected"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


# ── Models ───────────────────────────────────────────────────────────

class VisionAnalysis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    analysis_type: AnalysisType
    image_hash: Optional[str] = None
    backend: str = "unknown"
    model: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    results: dict = Field(default_factory=dict)
    confidence: Optional[float] = None
    grow_log_id: Optional[str] = None
    notes: Optional[str] = None


class ContaminationResult(BaseModel):
    detected: bool
    severity: ContaminationSeverity
    contaminant_type: Optional[str] = None
    confidence: float
    affected_area_pct: Optional[float] = None
    recommendation: str
    analysis_details: str


class GrowthStageResult(BaseModel):
    stage: GrowthStage
    confidence: float
    colonization_pct: Optional[float] = None
    days_estimate: Optional[str] = None
    health_score: float = 0.0
    observations: list[str] = Field(default_factory=list)


class SpeciesIdResult(BaseModel):
    top_match: str
    confidence: float
    alternatives: list[dict] = Field(default_factory=list)
    morphological_features: list[str] = Field(default_factory=list)


class TimelapseFrame(BaseModel):
    frame_index: int
    timestamp: datetime
    analysis: dict


class VisionUrlRequest(BaseModel):
    image_url: str
    analysis_type: AnalysisType = AnalysisType.GENERAL
    grow_log_id: Optional[str] = None
    context: Optional[str] = None


class ContaminationUrlRequest(BaseModel):
    image_url: str
    grow_log_id: Optional[str] = None


# ── Analysis history ─────────────────────────────────────────────────

_analyses: dict[str, VisionAnalysis] = {}


# ── Vision backend integration ───────────────────────────────────────

def _get_vision_backend():
    """Import and return the tools.vision module for backend calls."""
    try:
        from tools.vision import analyze_image as _analyze
        return _analyze
    except ImportError:
        return None


def _build_domain_prompt(analysis_type: AnalysisType, context: Optional[str] = None) -> str:
    """Build mycology-specific vision prompts."""
    prompts = {
        AnalysisType.CONTAMINATION: (
            "Analyze this image for signs of contamination in a mushroom cultivation context. "
            "Look for: Trichoderma (green mold), cobweb mold (grey wispy growth), "
            "bacterial blotch (slimy patches), black mold, lipstick mold (pink/orange). "
            "Report: contamination detected (yes/no), type, severity (none/suspected/mild/moderate/severe), "
            "estimated affected area percentage, and recommended action."
        ),
        AnalysisType.GROWTH_STAGE: (
            "Analyze this image of a mushroom cultivation substrate or fruiting body. "
            "Determine the growth stage: no_growth, early_colonization, mid_colonization, "
            "full_colonization, primordia, pin_set, young_fruiting, mature_fruiting, sporulating. "
            "Estimate colonization percentage if applicable. "
            "Rate overall health 0-10. List specific observations."
        ),
        AnalysisType.SPECIES_ID: (
            "Identify the mushroom species in this image. "
            "Provide top match with confidence, up to 3 alternatives, "
            "and key morphological features observed (cap shape, gill attachment, "
            "spore print color, stipe characteristics, habitat context)."
        ),
        AnalysisType.MORPHOLOGY: (
            "Analyze the morphological features of the mushroom or mycelium in this image. "
            "Describe: growth pattern, color, texture, density, rhizomorphic vs tomentose, "
            "any abnormalities, and overall vigor assessment."
        ),
        AnalysisType.GENERAL: (
            "Analyze this image in the context of mushroom cultivation and mycology. "
            "Provide a detailed description of what you observe."
        ),
    }
    prompt = prompts.get(analysis_type, prompts[AnalysisType.GENERAL])
    if context:
        prompt += f"\n\nAdditional context: {context}"
    return prompt


async def _fetch_image_url(image_url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Unable to fetch image URL: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="image_url must resolve to an image resource")

    return response.content


def _run_analysis(
    contents: bytes,
    analysis_type: AnalysisType,
    grow_log_id: Optional[str] = None,
    context: Optional[str] = None,
) -> VisionAnalysis:
    prompt = _build_domain_prompt(analysis_type, context)

    backend_fn = _get_vision_backend()
    result_data = {}
    backend_name = "mock"
    model_name = None

    if backend_fn:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        try:
            raw = json.loads(backend_fn(tmp_path, prompt))
            result_data = raw
            backend_name = raw.get("backend", "unknown")
            model_name = raw.get("model")
        finally:
            os.unlink(tmp_path)
    else:
        result_data = {
            "analysis": f"Vision analysis ({analysis_type.value}) — backend not available. "
            "Configure OPENROUTER_API_KEY or CROWE_LOGIC_URL for live analysis.",
            "mock": True,
        }

    analysis = VisionAnalysis(
        analysis_type=analysis_type,
        backend=backend_name,
        model=model_name,
        results=result_data,
        grow_log_id=grow_log_id,
    )
    _analyses[analysis.id] = analysis
    return analysis


def _run_contamination_check(contents: bytes) -> ContaminationResult:
    prompt = _build_domain_prompt(AnalysisType.CONTAMINATION)

    backend_fn = _get_vision_backend()
    if backend_fn:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        try:
            raw = json.loads(backend_fn(tmp_path, prompt))
            analysis_text = raw.get("analysis", "")
        finally:
            os.unlink(tmp_path)
    else:
        analysis_text = "Backend not available for live analysis."

    is_contaminated = any(
        keyword in analysis_text.lower()
        for keyword in ["contamination", "trichoderma", "mold", "bacterial", "cobweb"]
    )

    return ContaminationResult(
        detected=is_contaminated,
        severity=ContaminationSeverity.SUSPECTED if is_contaminated else ContaminationSeverity.NONE,
        confidence=0.7 if backend_fn else 0.0,
        recommendation=(
            "Isolate affected units immediately. Do not open in clean areas."
            if is_contaminated
            else "No contamination detected. Continue monitoring."
        ),
        analysis_details=analysis_text,
    )


# ── Routes ───────────────────────────────────────────────────────────

@router.post("/analyze", response_model=VisionAnalysis)
async def analyze_image(
    file: UploadFile = File(...),
    analysis_type: AnalysisType = Form(AnalysisType.GENERAL),
    grow_log_id: Optional[str] = Form(None),
    context: Optional[str] = Form(None),
):
    """Analyze an uploaded image using the vision pipeline."""
    contents = await file.read()
    return _run_analysis(contents, analysis_type, grow_log_id=grow_log_id, context=context)


@router.post("/analyze-url", response_model=VisionAnalysis)
async def analyze_image_url(request: VisionUrlRequest):
    """Analyze an image fetched from a remote URL."""
    contents = await _fetch_image_url(request.image_url)
    return _run_analysis(
        contents,
        request.analysis_type,
        grow_log_id=request.grow_log_id,
        context=request.context,
    )


@router.post("/contamination-check", response_model=ContaminationResult)
async def check_contamination(
    file: UploadFile = File(...),
    grow_log_id: Optional[str] = Form(None),
):
    """Specialized contamination detection endpoint."""
    contents = await file.read()
    return _run_contamination_check(contents)


@router.post("/contamination-check-url", response_model=ContaminationResult)
async def check_contamination_url(request: ContaminationUrlRequest):
    """Run contamination detection against an image URL."""
    contents = await _fetch_image_url(request.image_url)
    return _run_contamination_check(contents)


@router.get("/analyses", response_model=list[VisionAnalysis])
async def list_analyses(
    grow_log_id: Optional[str] = None,
    analysis_type: Optional[AnalysisType] = None,
    limit: int = 50,
):
    analyses = list(_analyses.values())
    if grow_log_id:
        analyses = [a for a in analyses if a.grow_log_id == grow_log_id]
    if analysis_type:
        analyses = [a for a in analyses if a.analysis_type == analysis_type]
    return sorted(analyses, key=lambda a: a.timestamp, reverse=True)[:limit]


@router.get("/analyses/{analysis_id}", response_model=VisionAnalysis)
async def get_analysis(analysis_id: str):
    if analysis_id not in _analyses:
        raise HTTPException(404, "Analysis not found")
    return _analyses[analysis_id]


@router.get("/health")
async def vision_health():
    backend_fn = _get_vision_backend()
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    has_crowe = bool(os.environ.get("CROWE_LOGIC_URL"))
    return {
        "status": "ok",
        "backend_available": backend_fn is not None,
        "openrouter_configured": has_openrouter,
        "crowe_vision_configured": has_crowe,
        "analyses_count": len(_analyses),
    }
