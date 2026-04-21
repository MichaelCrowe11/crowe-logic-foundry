"""
Mycology Operations — Strain tracking, cultivation protocols, grow logs,
contamination detection, harvest scheduling, and SOP generation.

Integrates with existing:
- agents/cultivation.yaml (CroweLM cultivation specialist)
- tools/crowelm.py (dataset management)
- data/crowelm-unified/ (training data)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mycology", tags=["mycology"])


# ── Enums ────────────────────────────────────────────────────────────

class GrowthPhase(str, Enum):
    INOCULATION = "inoculation"
    COLONIZATION = "colonization"
    PRIMORDIA = "primordia"
    FRUITING = "fruiting"
    HARVEST = "harvest"
    REST = "rest"


class ContaminationType(str, Enum):
    TRICHODERMA = "trichoderma"
    COBWEB = "cobweb"
    BLACK_MOLD = "black_mold"
    BACTERIAL = "bacterial"
    LIPSTICK = "lipstick"
    NONE = "none"
    UNKNOWN = "unknown"


class SubstrateType(str, Enum):
    HARDWOOD_SAWDUST = "hardwood_sawdust"
    STRAW = "straw"
    GRAIN = "grain"
    SUPPLEMENTED_SAWDUST = "supplemented_sawdust"
    MASTERS_MIX = "masters_mix"
    LOG = "log"
    AGAR = "agar"
    LIQUID_CULTURE = "liquid_culture"
    CUSTOM = "custom"


# ── Models ───────────────────────────────────────────────────────────

class Strain(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    species: str
    variety: Optional[str] = None
    source: Optional[str] = None
    isolation_date: Optional[date] = None
    generation: int = 0
    notes: Optional[str] = None
    optimal_temp_c: Optional[float] = None
    optimal_humidity_pct: Optional[float] = None
    colonization_days: Optional[int] = None
    fruiting_days: Optional[int] = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GrowLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strain_id: str
    batch_code: str
    substrate: SubstrateType
    phase: GrowthPhase = GrowthPhase.INOCULATION
    inoculation_date: date
    spawn_weight_g: Optional[float] = None
    substrate_weight_g: Optional[float] = None
    temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    co2_ppm: Optional[int] = None
    contamination: ContaminationType = ContaminationType.NONE
    harvest_weight_g: Optional[float] = None
    biological_efficiency_pct: Optional[float] = None
    notes: Optional[str] = None
    images: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CultivationProtocol(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    species: str
    substrate: SubstrateType
    spawn_rate_pct: float = 10.0
    colonization_temp_c: float = 24.0
    colonization_humidity_pct: float = 95.0
    fruiting_temp_c: float = 18.0
    fruiting_humidity_pct: float = 90.0
    fruiting_co2_ppm: int = 800
    light_hours: float = 12.0
    fae_exchanges_per_hour: int = 4
    expected_colonization_days: int = 14
    expected_fruiting_days: int = 7
    expected_be_pct: float = 100.0
    steps: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class EnvironmentReading(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    grow_log_id: str
    temp_c: float
    humidity_pct: float
    co2_ppm: Optional[int] = None
    light_lux: Optional[int] = None
    vpd_kpa: Optional[float] = None


class HarvestRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    grow_log_id: str
    flush_number: int = 1
    harvest_date: date
    wet_weight_g: float
    dry_weight_g: Optional[float] = None
    grade: Optional[str] = None
    notes: Optional[str] = None


class SOPRequest(BaseModel):
    species: str
    substrate: SubstrateType
    scale: str = "small"  # small, commercial, industrial
    include_contamination_protocol: bool = True


# ── In-memory store (replaced by DB in production) ───────────────────

_strains: dict[str, Strain] = {}
_grow_logs: dict[str, GrowLog] = {}
_protocols: dict[str, CultivationProtocol] = {}
_harvests: dict[str, HarvestRecord] = {}
_env_readings: list[EnvironmentReading] = []

# Seed default protocols
_DEFAULT_PROTOCOLS = [
    CultivationProtocol(
        id="proto-shiitake-sawdust",
        name="Shiitake on Supplemented Sawdust",
        species="Lentinula edodes",
        substrate=SubstrateType.SUPPLEMENTED_SAWDUST,
        spawn_rate_pct=12.0,
        colonization_temp_c=21.0,
        fruiting_temp_c=16.0,
        fruiting_humidity_pct=85.0,
        expected_colonization_days=60,
        expected_fruiting_days=10,
        expected_be_pct=120.0,
        steps=[
            "Prepare supplemented sawdust (80% hardwood, 20% soy hull) at 60-65% moisture",
            "Sterilize at 15 PSI for 2.5 hours",
            "Cool to room temperature in flow hood",
            "Inoculate with grain spawn at 12% rate",
            "Seal bags with 0.5 micron filter patch",
            "Incubate at 21C / 95% RH in dark for 60 days",
            "Cold shock at 10C for 24 hours to initiate pinning",
            "Move to fruiting at 16C / 85% RH / 800 ppm CO2 / 12h light",
            "Harvest when caps are 70-80% open before spore drop",
        ],
    ),
    CultivationProtocol(
        id="proto-oyster-straw",
        name="Blue Oyster on Pasteurized Straw",
        species="Pleurotus ostreatus var. columbinus",
        substrate=SubstrateType.STRAW,
        spawn_rate_pct=10.0,
        colonization_temp_c=24.0,
        fruiting_temp_c=18.0,
        fruiting_humidity_pct=90.0,
        expected_colonization_days=14,
        expected_fruiting_days=5,
        expected_be_pct=150.0,
        steps=[
            "Chop wheat straw to 2-4 inch lengths",
            "Pasteurize at 70-80C for 1 hour (hot water bath)",
            "Drain and cool to below 27C",
            "Mix grain spawn at 10% wet weight ratio",
            "Pack into perforated polyethylene bags (6-8 holes per bag)",
            "Incubate at 24C / 95% RH in dark for 14 days",
            "Expose to fruiting conditions: 18C / 90% RH / 600 ppm CO2",
            "Mist 2x daily, maintain strong FAE (4+ exchanges/hr)",
            "Harvest when cap edges begin to flatten",
        ],
    ),
    CultivationProtocol(
        id="proto-lionsmane-sawdust",
        name="Lion's Mane on Masters Mix",
        species="Hericium erinaceus",
        substrate=SubstrateType.MASTERS_MIX,
        spawn_rate_pct=15.0,
        colonization_temp_c=22.0,
        fruiting_temp_c=18.0,
        fruiting_humidity_pct=95.0,
        expected_colonization_days=21,
        expected_fruiting_days=10,
        expected_be_pct=100.0,
        steps=[
            "Prepare Masters Mix (50% hardwood pellets, 50% soy hull pellets)",
            "Hydrate to 60% moisture, mix thoroughly",
            "Sterilize at 15 PSI for 2.5 hours",
            "Cool and inoculate with grain spawn at 15% rate",
            "Seal bags with filter patch and rubber band",
            "Incubate at 22C in dark for 21 days until full colonization",
            "Cut X-shaped slits in bags where primordia form",
            "Fruit at 18C / 95% RH / 500 ppm CO2 / 12h indirect light",
            "Harvest when spines are 1-2cm long before yellowing",
        ],
    ),
]
for p in _DEFAULT_PROTOCOLS:
    _protocols[p.id] = p


# ── Routes ───────────────────────────────────────────────────────────

# Strains
@router.post("/strains", response_model=Strain)
async def create_strain(strain: Strain):
    _strains[strain.id] = strain
    return strain


@router.get("/strains", response_model=list[Strain])
async def list_strains(active_only: bool = True):
    strains = list(_strains.values())
    if active_only:
        strains = [s for s in strains if s.active]
    return sorted(strains, key=lambda s: s.name)


@router.get("/strains/{strain_id}", response_model=Strain)
async def get_strain(strain_id: str):
    if strain_id not in _strains:
        raise HTTPException(404, "Strain not found")
    return _strains[strain_id]


# Grow Logs
@router.post("/grow-logs", response_model=GrowLog)
async def create_grow_log(log: GrowLog):
    _grow_logs[log.id] = log
    return log


@router.get("/grow-logs", response_model=list[GrowLog])
async def list_grow_logs(
    strain_id: Optional[str] = None,
    phase: Optional[GrowthPhase] = None,
    contaminated: Optional[bool] = None,
):
    logs = list(_grow_logs.values())
    if strain_id:
        logs = [l for l in logs if l.strain_id == strain_id]
    if phase:
        logs = [l for l in logs if l.phase == phase]
    if contaminated is not None:
        if contaminated:
            logs = [l for l in logs if l.contamination != ContaminationType.NONE]
        else:
            logs = [l for l in logs if l.contamination == ContaminationType.NONE]
    return sorted(logs, key=lambda l: l.created_at, reverse=True)


@router.get("/grow-logs/{log_id}", response_model=GrowLog)
async def get_grow_log(log_id: str):
    if log_id not in _grow_logs:
        raise HTTPException(404, "Grow log not found")
    return _grow_logs[log_id]


@router.patch("/grow-logs/{log_id}/phase")
async def update_phase(log_id: str, phase: GrowthPhase):
    if log_id not in _grow_logs:
        raise HTTPException(404, "Grow log not found")
    _grow_logs[log_id].phase = phase
    _grow_logs[log_id].updated_at = datetime.now(tz=__import__("datetime").timezone.utc)
    return {"id": log_id, "phase": phase}


# Protocols
@router.get("/protocols", response_model=list[CultivationProtocol])
async def list_protocols(species: Optional[str] = None):
    protocols = list(_protocols.values())
    if species:
        protocols = [p for p in protocols if species.lower() in p.species.lower()]
    return protocols


@router.get("/protocols/{protocol_id}", response_model=CultivationProtocol)
async def get_protocol(protocol_id: str):
    if protocol_id not in _protocols:
        raise HTTPException(404, "Protocol not found")
    return _protocols[protocol_id]


@router.post("/protocols", response_model=CultivationProtocol)
async def create_protocol(protocol: CultivationProtocol):
    _protocols[protocol.id] = protocol
    return protocol


# Harvests
@router.post("/harvests", response_model=HarvestRecord)
async def record_harvest(harvest: HarvestRecord):
    if harvest.grow_log_id not in _grow_logs:
        raise HTTPException(404, "Grow log not found")
    _harvests[harvest.id] = harvest
    log = _grow_logs[harvest.grow_log_id]
    log.harvest_weight_g = (log.harvest_weight_g or 0) + harvest.wet_weight_g
    if log.substrate_weight_g and log.substrate_weight_g > 0:
        log.biological_efficiency_pct = (log.harvest_weight_g / log.substrate_weight_g) * 100
    log.updated_at = datetime.now(tz=__import__("datetime").timezone.utc)
    return harvest


@router.get("/harvests", response_model=list[HarvestRecord])
async def list_harvests(grow_log_id: Optional[str] = None):
    harvests = list(_harvests.values())
    if grow_log_id:
        harvests = [h for h in harvests if h.grow_log_id == grow_log_id]
    return sorted(harvests, key=lambda h: h.harvest_date, reverse=True)


# Environment
@router.post("/environment")
async def record_environment(reading: EnvironmentReading):
    _env_readings.append(reading)
    return {"recorded": True, "total_readings": len(_env_readings)}


@router.get("/environment/{grow_log_id}")
async def get_environment_history(grow_log_id: str, limit: int = Query(100, ge=1, le=1000)):
    readings = [r for r in _env_readings if r.grow_log_id == grow_log_id]
    return readings[-limit:]


# Analytics
@router.get("/analytics/contamination-rate")
async def contamination_rate():
    if not _grow_logs:
        return {"total": 0, "contaminated": 0, "rate": 0.0}
    total = len(_grow_logs)
    contaminated = sum(1 for l in _grow_logs.values() if l.contamination != ContaminationType.NONE)
    by_type = {}
    for l in _grow_logs.values():
        if l.contamination != ContaminationType.NONE:
            by_type[l.contamination.value] = by_type.get(l.contamination.value, 0) + 1
    return {
        "total": total,
        "contaminated": contaminated,
        "rate": round(contaminated / total * 100, 1),
        "by_type": by_type,
    }


@router.get("/analytics/yield-summary")
async def yield_summary():
    harvested = [l for l in _grow_logs.values() if l.harvest_weight_g and l.harvest_weight_g > 0]
    if not harvested:
        return {"total_harvested_g": 0, "avg_be_pct": 0, "batches": 0}
    total_weight = sum(l.harvest_weight_g for l in harvested)
    be_values = [l.biological_efficiency_pct for l in harvested if l.biological_efficiency_pct]
    avg_be = sum(be_values) / len(be_values) if be_values else 0
    return {
        "total_harvested_g": round(total_weight, 1),
        "avg_be_pct": round(avg_be, 1),
        "batches": len(harvested),
    }


# SOP Generation
@router.post("/sop/generate")
async def generate_sop(request: SOPRequest):
    """Generate a Standard Operating Procedure based on species and substrate."""
    matching = [
        p for p in _protocols.values()
        if request.species.lower() in p.species.lower()
        and p.substrate == request.substrate
    ]
    if matching:
        proto = matching[0]
        return {
            "based_on": proto.id,
            "species": proto.species,
            "substrate": proto.substrate.value,
            "scale": request.scale,
            "steps": proto.steps,
            "parameters": {
                "spawn_rate_pct": proto.spawn_rate_pct,
                "colonization_temp_c": proto.colonization_temp_c,
                "colonization_humidity_pct": proto.colonization_humidity_pct,
                "fruiting_temp_c": proto.fruiting_temp_c,
                "fruiting_humidity_pct": proto.fruiting_humidity_pct,
                "fruiting_co2_ppm": proto.fruiting_co2_ppm,
            },
            "note": "Generated from protocol library. For AI-enhanced SOP, use the cultivation agent.",
        }
    return {
        "species": request.species,
        "substrate": request.substrate.value,
        "scale": request.scale,
        "steps": [
            f"Prepare {request.substrate.value} substrate",
            "Sterilize or pasteurize as appropriate for substrate type",
            f"Inoculate with {request.species} spawn",
            "Monitor colonization progress",
            "Initiate fruiting conditions when fully colonized",
            "Harvest at optimal maturity",
        ],
        "note": "Generic protocol. Add species-specific parameters or use the cultivation agent for detail.",
    }
