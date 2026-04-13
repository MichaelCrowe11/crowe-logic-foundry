"""
Knowledge Plane — Embedding store and semantic search.

Uses pgvector for vector similarity search across all domain entities.
Falls back to keyword search when embeddings are unavailable.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/knowledge", tags=["Knowledge Plane"])


# ─── Models ───────────────────────────────────────────────────────────

class EmbeddingRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"emb-{uuid.uuid4().hex[:12]}")
    source_type: str
    source_id: str
    chunk_index: int = 0
    content: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SearchResult(BaseModel):
    source_type: str
    source_id: str
    content: str
    score: float
    metadata: dict = Field(default_factory=dict)


class KnowledgeEdge(BaseModel):
    id: str = Field(default_factory=lambda: f"edge-{uuid.uuid4().hex[:12]}")
    from_type: str
    from_id: str
    relation: str
    to_type: str
    to_id: str
    weight: float = 1.0
    evidence: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class GraphNeighbor(BaseModel):
    entity_type: str
    entity_id: str
    relation: str
    direction: str  # "outgoing" or "incoming"
    weight: float = 1.0
    evidence: Optional[str] = None


class Species(BaseModel):
    id: str = Field(default_factory=lambda: f"sp-{uuid.uuid4().hex[:12]}")
    scientific_name: str
    common_name: Optional[str] = None
    kingdom: str = "Fungi"
    phylum: Optional[str] = None
    family: Optional[str] = None
    genus: Optional[str] = None
    description: Optional[str] = None
    edibility: Optional[str] = None
    habitat: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class IndexRequest(BaseModel):
    source_type: str
    source_id: str
    content: str
    metadata: dict = Field(default_factory=dict)


class EdgeRequest(BaseModel):
    from_type: str
    from_id: str
    relation: str
    to_type: str
    to_id: str
    weight: float = 1.0
    evidence: Optional[str] = None


# ─── In-memory stores (replaced by Postgres in production) ────────────

_embeddings: dict[str, EmbeddingRecord] = {}
_edges: dict[str, KnowledgeEdge] = {}
_species: dict[str, Species] = {}


def _seed_species():
    """Seed default species taxonomy."""
    defaults = [
        Species(
            id="sp-lentinula-edodes",
            scientific_name="Lentinula edodes",
            common_name="Shiitake",
            genus="Lentinula", family="Omphalotaceae", phylum="Basidiomycota",
            edibility="edible",
            description="Premier edible and medicinal mushroom, source of lentinan",
        ),
        Species(
            id="sp-pleurotus-ostreatus",
            scientific_name="Pleurotus ostreatus",
            common_name="Blue Oyster",
            genus="Pleurotus", family="Pleurotaceae", phylum="Basidiomycota",
            edibility="edible",
            description="Fast-growing oyster mushroom, commercially important",
        ),
        Species(
            id="sp-hericium-erinaceus",
            scientific_name="Hericium erinaceus",
            common_name="Lion's Mane",
            genus="Hericium", family="Hericiaceae", phylum="Basidiomycota",
            edibility="medicinal",
            description="Neuroprotective compounds (hericenones, erinacines)",
        ),
        Species(
            id="sp-ganoderma-lucidum",
            scientific_name="Ganoderma lucidum",
            common_name="Reishi",
            genus="Ganoderma", family="Ganodermataceae", phylum="Basidiomycota",
            edibility="medicinal",
            description="Adaptogenic mushroom, ganoderic acids",
        ),
        Species(
            id="sp-cordyceps-militaris",
            scientific_name="Cordyceps militaris",
            common_name="Cordyceps",
            genus="Cordyceps", family="Cordycipitaceae", phylum="Ascomycota",
            edibility="medicinal",
            description="Source of cordycepin, cultivable entomopathogen",
        ),
        Species(
            id="sp-psilocybe-cubensis",
            scientific_name="Psilocybe cubensis",
            common_name="Golden Teacher",
            genus="Psilocybe", family="Hymenogastraceae", phylum="Basidiomycota",
            edibility="psychoactive",
            description="Source of psilocybin and psilocin",
        ),
        Species(
            id="sp-trametes-versicolor",
            scientific_name="Trametes versicolor",
            common_name="Turkey Tail",
            genus="Trametes", family="Polyporaceae", phylum="Basidiomycota",
            edibility="medicinal",
            description="Source of PSK and PSP, immune modulators",
        ),
        Species(
            id="sp-inonotus-obliquus",
            scientific_name="Inonotus obliquus",
            common_name="Chaga",
            genus="Inonotus", family="Hymenochaetaceae", phylum="Basidiomycota",
            edibility="medicinal",
            description="Birch-parasitic, high antioxidant content",
        ),
    ]
    for sp in defaults:
        _species[sp.id] = sp


def _seed_edges():
    """Seed default knowledge graph relationships."""
    defaults = [
        KnowledgeEdge(id="edge-shiitake-lentinan", from_type="species", from_id="sp-lentinula-edodes",
                       relation="produces", to_type="compound", to_id="cpd-lentinan",
                       evidence="Well-established β-glucan from shiitake"),
        KnowledgeEdge(id="edge-lions-hericenone", from_type="species", from_id="sp-hericium-erinaceus",
                       relation="produces", to_type="compound", to_id="cpd-hericenone-c",
                       evidence="Isolated from fruiting body"),
        KnowledgeEdge(id="edge-lions-erinacine", from_type="species", from_id="sp-hericium-erinaceus",
                       relation="produces", to_type="compound", to_id="cpd-erinacine-a",
                       evidence="Isolated from mycelium"),
        KnowledgeEdge(id="edge-psilocybe-psilocybin", from_type="species", from_id="sp-psilocybe-cubensis",
                       relation="produces", to_type="compound", to_id="cpd-psilocybin",
                       evidence="Primary psychoactive constituent"),
        KnowledgeEdge(id="edge-reishi-ganoderic", from_type="species", from_id="sp-ganoderma-lucidum",
                       relation="produces", to_type="compound", to_id="cpd-ganoderic-a",
                       evidence="Major triterpenoid from reishi"),
        KnowledgeEdge(id="edge-cordyceps-cordycepin", from_type="species", from_id="sp-cordyceps-militaris",
                       relation="produces", to_type="compound", to_id="cpd-cordycepin",
                       evidence="Adenosine analog from cordyceps"),
    ]
    for e in defaults:
        _edges[e.id] = e


_seed_species()
_seed_edges()


# ─── Embedding helpers ────────────────────────────────────────────────

def _simple_embed(text: str) -> list[float]:
    """Deterministic pseudo-embedding for testing (content-hash based).
    In production, replaced by OpenAI text-embedding-3-small calls."""
    h = hashlib.sha256(text.encode()).digest()
    return [((b - 128) / 128.0) for b in h[:32]]  # 32-dim for testing


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(x ** 2 for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ─── Embedding endpoints ─────────────────────────────────────────────

@router.post("/embeddings/index", response_model=EmbeddingRecord)
def index_content(req: IndexRequest):
    """Index a piece of content for semantic search."""
    rec = EmbeddingRecord(
        source_type=req.source_type,
        source_id=req.source_id,
        content=req.content,
        metadata=req.metadata,
    )
    _embeddings[rec.id] = rec
    return rec


@router.get("/embeddings/search", response_model=list[SearchResult])
def semantic_search(
    query: str = Query(..., description="Natural language search query"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    limit: int = Query(10, ge=1, le=100),
):
    """Semantic search across all indexed content."""
    query_vec = _simple_embed(query.lower())
    scored: list[tuple[float, EmbeddingRecord]] = []

    for rec in _embeddings.values():
        if source_type and rec.source_type != source_type:
            continue
        rec_vec = _simple_embed(rec.content.lower())
        score = _cosine_similarity(query_vec, rec_vec)
        scored.append((score, rec))

    # Also do keyword fallback for better results with small embedding dim
    query_terms = set(query.lower().split())
    for rec in _embeddings.values():
        if source_type and rec.source_type != source_type:
            continue
        content_terms = set(rec.content.lower().split())
        overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
        if overlap > 0:
            # Boost keyword matches
            existing = next((s for s in scored if s[1].id == rec.id), None)
            if existing:
                idx = scored.index(existing)
                scored[idx] = (existing[0] + overlap * 0.5, existing[1])

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        SearchResult(
            source_type=r.source_type,
            source_id=r.source_id,
            content=r.content[:500],
            score=round(s, 4),
            metadata=r.metadata,
        )
        for s, r in scored[:limit]
    ]


@router.get("/embeddings/stats")
def embedding_stats():
    """Get embedding store statistics."""
    by_type: dict[str, int] = {}
    for rec in _embeddings.values():
        by_type[rec.source_type] = by_type.get(rec.source_type, 0) + 1
    return {
        "total_embeddings": len(_embeddings),
        "by_source_type": by_type,
    }


# ─── Knowledge Graph endpoints ────────────────────────────────────────

@router.post("/graph/edges", response_model=KnowledgeEdge)
def add_edge(req: EdgeRequest):
    """Add a relationship to the knowledge graph."""
    key = f"{req.from_type}:{req.from_id}:{req.relation}:{req.to_type}:{req.to_id}"
    existing = next((e for e in _edges.values()
                     if e.from_type == req.from_type and e.from_id == req.from_id
                     and e.relation == req.relation
                     and e.to_type == req.to_type and e.to_id == req.to_id), None)
    if existing:
        existing.weight = req.weight
        if req.evidence:
            existing.evidence = req.evidence
        return existing

    edge = KnowledgeEdge(
        from_type=req.from_type, from_id=req.from_id,
        relation=req.relation,
        to_type=req.to_type, to_id=req.to_id,
        weight=req.weight, evidence=req.evidence,
    )
    _edges[edge.id] = edge
    return edge


@router.get("/graph/neighbors", response_model=list[GraphNeighbor])
def get_neighbors(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    relation: Optional[str] = Query(None),
    max_depth: int = Query(1, ge=1, le=3),
):
    """Get neighbors of an entity in the knowledge graph."""
    visited: set[tuple[str, str]] = set()
    results: list[GraphNeighbor] = []

    def _traverse(etype: str, eid: str, depth: int):
        if depth > max_depth or (etype, eid) in visited:
            return
        visited.add((etype, eid))

        for edge in _edges.values():
            # Outgoing edges
            if edge.from_type == etype and edge.from_id == eid:
                if relation and edge.relation != relation:
                    continue
                results.append(GraphNeighbor(
                    entity_type=edge.to_type, entity_id=edge.to_id,
                    relation=edge.relation, direction="outgoing",
                    weight=edge.weight, evidence=edge.evidence,
                ))
                if depth < max_depth:
                    _traverse(edge.to_type, edge.to_id, depth + 1)
            # Incoming edges
            if edge.to_type == etype and edge.to_id == eid:
                if relation and edge.relation != relation:
                    continue
                results.append(GraphNeighbor(
                    entity_type=edge.from_type, entity_id=edge.from_id,
                    relation=edge.relation, direction="incoming",
                    weight=edge.weight, evidence=edge.evidence,
                ))
                if depth < max_depth:
                    _traverse(edge.from_type, edge.from_id, depth + 1)

    _traverse(entity_type, entity_id, 1)
    return results


@router.get("/graph/edges")
def list_edges(
    from_type: Optional[str] = None,
    relation: Optional[str] = None,
    to_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    """List knowledge graph edges with optional filters."""
    results = []
    for edge in _edges.values():
        if from_type and edge.from_type != from_type:
            continue
        if relation and edge.relation != relation:
            continue
        if to_type and edge.to_type != to_type:
            continue
        results.append(edge)
        if len(results) >= limit:
            break
    return results


@router.get("/graph/stats")
def graph_stats():
    """Get knowledge graph statistics."""
    nodes: set[tuple[str, str]] = set()
    relations: dict[str, int] = {}
    for edge in _edges.values():
        nodes.add((edge.from_type, edge.from_id))
        nodes.add((edge.to_type, edge.to_id))
        relations[edge.relation] = relations.get(edge.relation, 0) + 1

    node_types: dict[str, int] = {}
    for ntype, _ in nodes:
        node_types[ntype] = node_types.get(ntype, 0) + 1

    return {
        "total_nodes": len(nodes),
        "total_edges": len(_edges),
        "node_types": node_types,
        "relation_types": relations,
    }


# ─── Species taxonomy endpoints ───────────────────────────────────────

@router.get("/taxonomy/species", response_model=list[Species])
def list_species(
    genus: Optional[str] = None,
    edibility: Optional[str] = None,
    search: Optional[str] = None,
):
    """List species in the taxonomy."""
    results = list(_species.values())
    if genus:
        results = [s for s in results if s.genus and genus.lower() in s.genus.lower()]
    if edibility:
        results = [s for s in results if s.edibility == edibility]
    if search:
        q = search.lower()
        results = [s for s in results
                   if q in s.scientific_name.lower()
                   or (s.common_name and q in s.common_name.lower())
                   or (s.description and q in s.description.lower())]
    return results


@router.get("/taxonomy/species/{species_id}", response_model=Species)
def get_species(species_id: str):
    """Get a species by ID."""
    sp = _species.get(species_id)
    if not sp:
        raise HTTPException(404, "Species not found")
    return sp


@router.post("/taxonomy/species", response_model=Species)
def create_species(sp: Species):
    """Add a species to the taxonomy."""
    existing = next((s for s in _species.values()
                     if s.scientific_name == sp.scientific_name), None)
    if existing:
        raise HTTPException(409, f"Species '{sp.scientific_name}' already exists")
    _species[sp.id] = sp
    return sp


@router.get("/taxonomy/species/{species_id}/compounds")
def species_compounds(species_id: str):
    """Get all compounds produced by a species (via knowledge graph)."""
    if species_id not in _species:
        raise HTTPException(404, "Species not found")

    compounds = []
    for edge in _edges.values():
        if (edge.from_type == "species" and edge.from_id == species_id
                and edge.relation == "produces" and edge.to_type == "compound"):
            compounds.append({
                "compound_id": edge.to_id,
                "relation": edge.relation,
                "evidence": edge.evidence,
                "weight": edge.weight,
            })
    return {
        "species_id": species_id,
        "species_name": _species[species_id].scientific_name,
        "compounds": compounds,
        "total": len(compounds),
    }
