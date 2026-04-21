"""
Compound Discovery — Bioactive compound identification, molecular analysis,
ADMET predictions, structure-activity relationships, and target identification.

Integrates with existing:
- data/crowelm-biotech/ (biotech platform with drug discovery pipelines)
- data/crowelm-platform/docker/Dockerfile.pipeline (discovery pipeline)
- tools/staging_pipeline.py (data staging for CroweLM)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/compound", tags=["compound-discovery"])


# ── Enums ────────────────────────────────────────────────────────────

class CompoundSource(str, Enum):
    FUNGAL = "fungal"
    PLANT = "plant"
    MARINE = "marine"
    SYNTHETIC = "synthetic"
    SEMI_SYNTHETIC = "semi_synthetic"
    ENDOPHYTIC = "endophytic"


class CompoundClass(str, Enum):
    TERPENE = "terpene"
    POLYSACCHARIDE = "polysaccharide"
    PHENOLIC = "phenolic"
    ALKALOID = "alkaloid"
    PEPTIDE = "peptide"
    POLYKETIDE = "polyketide"
    STEROID = "steroid"
    LIPID = "lipid"
    OTHER = "other"


class BioactivityType(str, Enum):
    ANTIMICROBIAL = "antimicrobial"
    ANTITUMOR = "antitumor"
    ANTI_INFLAMMATORY = "anti_inflammatory"
    ANTIOXIDANT = "antioxidant"
    IMMUNOMODULATORY = "immunomodulatory"
    NEUROPROTECTIVE = "neuroprotective"
    HEPATOPROTECTIVE = "hepatoprotective"
    ANTIVIRAL = "antiviral"
    ANTIDIABETIC = "antidiabetic"
    CARDIOVASCULAR = "cardiovascular"


class DevelopmentStage(str, Enum):
    IDENTIFIED = "identified"
    ISOLATED = "isolated"
    CHARACTERIZED = "characterized"
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    PRECLINICAL = "preclinical"
    CLINICAL = "clinical"


class ADMETCategory(str, Enum):
    FAVORABLE = "favorable"
    MODERATE = "moderate"
    UNFAVORABLE = "unfavorable"
    UNKNOWN = "unknown"


# ── Models ───────────────────────────────────────────────────────────

class Compound(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    iupac_name: Optional[str] = None
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    smiles: Optional[str] = None
    inchi_key: Optional[str] = None
    source_organism: Optional[str] = None
    source_type: CompoundSource = CompoundSource.FUNGAL
    compound_class: CompoundClass = CompoundClass.OTHER
    development_stage: DevelopmentStage = DevelopmentStage.IDENTIFIED
    bioactivities: list[BioactivityType] = Field(default_factory=list)
    cas_number: Optional[str] = None
    pubchem_cid: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BioactivityAssay(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    compound_id: str
    activity_type: BioactivityType
    target: Optional[str] = None
    assay_type: str = "in_vitro"
    ic50_um: Optional[float] = None
    ec50_um: Optional[float] = None
    mic_ug_ml: Optional[float] = None
    inhibition_pct: Optional[float] = None
    concentration_tested: Optional[str] = None
    cell_line: Optional[str] = None
    result_summary: str
    reference_paper_id: Optional[str] = None
    tested_at: datetime = Field(default_factory=datetime.utcnow)


class ADMETPrediction(BaseModel):
    compound_id: str
    absorption: ADMETCategory = ADMETCategory.UNKNOWN
    distribution: ADMETCategory = ADMETCategory.UNKNOWN
    metabolism: ADMETCategory = ADMETCategory.UNKNOWN
    excretion: ADMETCategory = ADMETCategory.UNKNOWN
    toxicity: ADMETCategory = ADMETCategory.UNKNOWN
    oral_bioavailability: Optional[str] = None
    bbb_penetration: Optional[bool] = None
    cyp_inhibition: list[str] = Field(default_factory=list)
    herg_risk: Optional[str] = None
    lipinski_violations: int = 0
    predicted_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None


class TargetInteraction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    compound_id: str
    target_name: str
    target_type: str = "protein"
    uniprot_id: Optional[str] = None
    pdb_id: Optional[str] = None
    interaction_type: str = "inhibitor"
    binding_affinity_nm: Optional[float] = None
    docking_score: Optional[float] = None
    validated: bool = False
    notes: Optional[str] = None


class SARAnalysis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    compound_ids: list[str]
    activity_type: BioactivityType
    scaffold: Optional[str] = None
    key_pharmacophores: list[str] = Field(default_factory=list)
    sar_findings: list[str] = Field(default_factory=list)
    optimization_suggestions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── In-memory store ──────────────────────────────────────────────────

_compounds: dict[str, Compound] = {}
_assays: dict[str, BioactivityAssay] = {}
_admet: dict[str, ADMETPrediction] = {}
_targets: dict[str, TargetInteraction] = {}
_sar: dict[str, SARAnalysis] = {}

# Seed well-known fungal compounds
_SEED_COMPOUNDS = [
    Compound(
        id="cpd-lentinan",
        name="Lentinan",
        formula="(C6H10O5)n",
        source_organism="Lentinula edodes",
        compound_class=CompoundClass.POLYSACCHARIDE,
        development_stage=DevelopmentStage.CLINICAL,
        bioactivities=[BioactivityType.ANTITUMOR, BioactivityType.IMMUNOMODULATORY],
        notes="Beta-1,3-glucan with beta-1,6 branches. Approved in Japan as adjunct cancer therapy.",
    ),
    Compound(
        id="cpd-hericenone-c",
        name="Hericenone C",
        formula="C35H52O5",
        molecular_weight=556.78,
        source_organism="Hericium erinaceus",
        compound_class=CompoundClass.PHENOLIC,
        development_stage=DevelopmentStage.IN_VITRO,
        bioactivities=[BioactivityType.NEUROPROTECTIVE],
        notes="Stimulates NGF synthesis in vitro. Key compound in Lion's Mane research.",
    ),
    Compound(
        id="cpd-erinacine-a",
        name="Erinacine A",
        formula="C25H36O5",
        molecular_weight=416.55,
        source_organism="Hericium erinaceus",
        compound_class=CompoundClass.TERPENE,
        development_stage=DevelopmentStage.IN_VIVO,
        bioactivities=[BioactivityType.NEUROPROTECTIVE],
        notes="Cyathane diterpenoid. Crosses BBB. Promotes NGF synthesis in vivo.",
    ),
    Compound(
        id="cpd-psilocybin",
        name="Psilocybin",
        formula="C12H17N2O4P",
        molecular_weight=284.25,
        smiles="OC1=CC=C2C(CCN(C)C2)=C1OP(O)(O)=O",
        source_organism="Psilocybe cubensis",
        compound_class=CompoundClass.ALKALOID,
        development_stage=DevelopmentStage.CLINICAL,
        bioactivities=[BioactivityType.NEUROPROTECTIVE],
        notes="Serotonin receptor agonist. FDA breakthrough therapy designation for treatment-resistant depression.",
    ),
    Compound(
        id="cpd-ganoderic-acid-a",
        name="Ganoderic Acid A",
        formula="C30H44O7",
        molecular_weight=516.66,
        source_organism="Ganoderma lucidum",
        compound_class=CompoundClass.TERPENE,
        development_stage=DevelopmentStage.IN_VITRO,
        bioactivities=[BioactivityType.ANTI_INFLAMMATORY, BioactivityType.HEPATOPROTECTIVE],
        notes="Lanostane triterpenoid from Reishi. Anti-inflammatory via NF-kB inhibition.",
    ),
    Compound(
        id="cpd-cordycepin",
        name="Cordycepin",
        formula="C10H13N5O3",
        molecular_weight=251.24,
        smiles="NC1=NC=NC2=C1N=CN2[C@@H]1O[C@H](CO)C[C@H]1O",
        source_organism="Cordyceps militaris",
        compound_class=CompoundClass.ALKALOID,
        development_stage=DevelopmentStage.PRECLINICAL,
        bioactivities=[BioactivityType.ANTITUMOR, BioactivityType.ANTI_INFLAMMATORY, BioactivityType.ANTIVIRAL],
        notes="3'-deoxyadenosine. Broad spectrum bioactivity. Adenosine analog.",
    ),
]
for c in _SEED_COMPOUNDS:
    _compounds[c.id] = c


# ── Routes ───────────────────────────────────────────────────────────

# Compounds
@router.post("/compounds", response_model=Compound)
async def create_compound(compound: Compound):
    _compounds[compound.id] = compound
    return compound


@router.get("/compounds", response_model=list[Compound])
async def list_compounds(
    source_type: Optional[CompoundSource] = None,
    compound_class: Optional[CompoundClass] = None,
    bioactivity: Optional[BioactivityType] = None,
    stage: Optional[DevelopmentStage] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    compounds = list(_compounds.values())
    if source_type:
        compounds = [c for c in compounds if c.source_type == source_type]
    if compound_class:
        compounds = [c for c in compounds if c.compound_class == compound_class]
    if bioactivity:
        compounds = [c for c in compounds if bioactivity in c.bioactivities]
    if stage:
        compounds = [c for c in compounds if c.development_stage == stage]
    if search:
        q = search.lower()
        compounds = [
            c for c in compounds
            if q in c.name.lower()
            or q in (c.source_organism or "").lower()
            or q in (c.notes or "").lower()
        ]
    return sorted(compounds, key=lambda c: c.name)[:limit]


@router.get("/compounds/{compound_id}", response_model=Compound)
async def get_compound(compound_id: str):
    if compound_id not in _compounds:
        raise HTTPException(404, "Compound not found")
    return _compounds[compound_id]


# Bioactivity Assays
@router.post("/assays", response_model=BioactivityAssay)
async def record_assay(assay: BioactivityAssay):
    if assay.compound_id not in _compounds:
        raise HTTPException(404, "Compound not found")
    _assays[assay.id] = assay
    return assay


@router.get("/assays", response_model=list[BioactivityAssay])
async def list_assays(
    compound_id: Optional[str] = None,
    activity_type: Optional[BioactivityType] = None,
):
    assays = list(_assays.values())
    if compound_id:
        assays = [a for a in assays if a.compound_id == compound_id]
    if activity_type:
        assays = [a for a in assays if a.activity_type == activity_type]
    return sorted(assays, key=lambda a: a.tested_at, reverse=True)


# ADMET
@router.post("/admet", response_model=ADMETPrediction)
async def predict_admet(prediction: ADMETPrediction):
    if prediction.compound_id not in _compounds:
        raise HTTPException(404, "Compound not found")
    _admet[prediction.compound_id] = prediction
    return prediction


@router.get("/admet/{compound_id}", response_model=ADMETPrediction)
async def get_admet(compound_id: str):
    if compound_id not in _admet:
        raise HTTPException(404, "No ADMET prediction for this compound")
    return _admet[compound_id]


@router.post("/admet/predict-lipinski")
async def check_lipinski(compound_id: str):
    """Check Lipinski's Rule of Five for oral drug-likeness."""
    if compound_id not in _compounds:
        raise HTTPException(404, "Compound not found")
    c = _compounds[compound_id]
    violations = 0
    checks = {}

    if c.molecular_weight:
        checks["mw_under_500"] = c.molecular_weight <= 500
        if not checks["mw_under_500"]:
            violations += 1

    checks["note"] = (
        "Full Lipinski analysis requires logP, HBD, and HBA data. "
        "Provide SMILES for computational analysis."
    )
    checks["violations"] = violations
    checks["drug_like"] = violations <= 1
    return checks


# Targets
@router.post("/targets", response_model=TargetInteraction)
async def add_target(target: TargetInteraction):
    if target.compound_id not in _compounds:
        raise HTTPException(404, "Compound not found")
    _targets[target.id] = target
    return target


@router.get("/targets", response_model=list[TargetInteraction])
async def list_targets(compound_id: Optional[str] = None):
    targets = list(_targets.values())
    if compound_id:
        targets = [t for t in targets if t.compound_id == compound_id]
    return targets


# SAR Analysis
@router.post("/sar", response_model=SARAnalysis)
async def create_sar_analysis(analysis: SARAnalysis):
    for cid in analysis.compound_ids:
        if cid not in _compounds:
            raise HTTPException(404, f"Compound {cid} not found")
    _sar[analysis.id] = analysis
    return analysis


@router.get("/sar", response_model=list[SARAnalysis])
async def list_sar_analyses(activity_type: Optional[BioactivityType] = None):
    analyses = list(_sar.values())
    if activity_type:
        analyses = [a for a in analyses if a.activity_type == activity_type]
    return sorted(analyses, key=lambda a: a.created_at, reverse=True)


# Analytics / Dashboard
@router.get("/analytics/pipeline")
async def compound_pipeline():
    """Overview of compound discovery pipeline."""
    by_stage = {}
    by_class = {}
    by_activity = {}
    for c in _compounds.values():
        stage = c.development_stage.value
        by_stage[stage] = by_stage.get(stage, 0) + 1
        cls = c.compound_class.value
        by_class[cls] = by_class.get(cls, 0) + 1
        for act in c.bioactivities:
            by_activity[act.value] = by_activity.get(act.value, 0) + 1

    return {
        "total_compounds": len(_compounds),
        "total_assays": len(_assays),
        "total_targets": len(_targets),
        "by_development_stage": by_stage,
        "by_compound_class": by_class,
        "by_bioactivity": by_activity,
        "compounds_with_admet": len(_admet),
    }


@router.get("/analytics/organisms")
async def organisms_summary():
    """Summary of source organisms in the compound library."""
    organisms = {}
    for c in _compounds.values():
        org = c.source_organism or "Unknown"
        if org not in organisms:
            organisms[org] = {"count": 0, "compounds": [], "activities": set()}
        organisms[org]["count"] += 1
        organisms[org]["compounds"].append(c.name)
        organisms[org]["activities"].update(a.value for a in c.bioactivities)
    # Convert sets to lists for JSON
    for org in organisms.values():
        org["activities"] = sorted(org["activities"])
    return organisms
