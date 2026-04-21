"""Tests for knowledge plane — embeddings, graph, taxonomy."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from fastapi import FastAPI
    from knowledge.search import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestTaxonomy:
    def test_list_species(self, client):
        resp = client.get("/knowledge/taxonomy/species")
        assert resp.status_code == 200
        species = resp.json()
        assert len(species) >= 8
        names = [s["scientific_name"] for s in species]
        assert "Lentinula edodes" in names
        assert "Hericium erinaceus" in names

    def test_filter_by_genus(self, client):
        resp = client.get("/knowledge/taxonomy/species?genus=Pleurotus")
        assert resp.status_code == 200
        assert all("Pleurotus" in s["genus"] for s in resp.json())

    def test_filter_by_edibility(self, client):
        resp = client.get("/knowledge/taxonomy/species?edibility=medicinal")
        assert resp.status_code == 200
        medicinal = resp.json()
        assert len(medicinal) >= 4  # Reishi, Lion's Mane, Cordyceps, Turkey Tail, Chaga

    def test_search_species(self, client):
        resp = client.get("/knowledge/taxonomy/species?search=neuroprotective")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_get_species_by_id(self, client):
        resp = client.get("/knowledge/taxonomy/species/sp-ganoderma-lucidum")
        assert resp.status_code == 200
        assert resp.json()["common_name"] == "Reishi"

    def test_species_not_found(self, client):
        resp = client.get("/knowledge/taxonomy/species/sp-nonexistent")
        assert resp.status_code == 404

    def test_create_species(self, client):
        resp = client.post("/knowledge/taxonomy/species", json={
            "scientific_name": "Agaricus bisporus",
            "common_name": "Button Mushroom",
            "genus": "Agaricus",
            "phylum": "Basidiomycota",
            "edibility": "edible",
        })
        assert resp.status_code == 200
        assert resp.json()["common_name"] == "Button Mushroom"

    def test_duplicate_species_rejected(self, client):
        resp = client.post("/knowledge/taxonomy/species", json={
            "scientific_name": "Lentinula edodes",
            "common_name": "Shiitake",
        })
        assert resp.status_code == 409

    def test_species_compounds(self, client):
        resp = client.get("/knowledge/taxonomy/species/sp-hericium-erinaceus/compounds")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2  # Hericenone C + Erinacine A
        cpd_ids = [c["compound_id"] for c in data["compounds"]]
        assert "cpd-hericenone-c" in cpd_ids
        assert "cpd-erinacine-a" in cpd_ids


class TestKnowledgeGraph:
    def test_list_edges(self, client):
        resp = client.get("/knowledge/graph/edges")
        assert resp.status_code == 200
        edges = resp.json()
        assert len(edges) >= 6

    def test_filter_edges_by_relation(self, client):
        resp = client.get("/knowledge/graph/edges?relation=produces")
        assert resp.status_code == 200
        assert all(e["relation"] == "produces" for e in resp.json())

    def test_graph_stats(self, client):
        resp = client.get("/knowledge/graph/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total_edges"] >= 6
        assert stats["total_nodes"] >= 10
        assert "species" in stats["node_types"]
        assert "compound" in stats["node_types"]

    def test_get_neighbors_outgoing(self, client):
        resp = client.get(
            "/knowledge/graph/neighbors",
            params={"entity_type": "species", "entity_id": "sp-lentinula-edodes"},
        )
        assert resp.status_code == 200
        neighbors = resp.json()
        assert len(neighbors) >= 1
        assert any(n["entity_id"] == "cpd-lentinan" for n in neighbors)

    def test_get_neighbors_incoming(self, client):
        resp = client.get(
            "/knowledge/graph/neighbors",
            params={"entity_type": "compound", "entity_id": "cpd-psilocybin"},
        )
        assert resp.status_code == 200
        neighbors = resp.json()
        assert any(n["entity_id"] == "sp-psilocybe-cubensis" for n in neighbors)

    def test_add_edge(self, client):
        resp = client.post("/knowledge/graph/edges", json={
            "from_type": "compound",
            "from_id": "cpd-psilocybin",
            "relation": "targets",
            "to_type": "protein",
            "to_id": "5-HT2A",
            "evidence": "Well-characterized serotonin receptor agonist",
        })
        assert resp.status_code == 200
        edge = resp.json()
        assert edge["relation"] == "targets"

    def test_multi_hop_traversal(self, client):
        # Add a second-hop edge
        client.post("/knowledge/graph/edges", json={
            "from_type": "compound",
            "from_id": "cpd-cordycepin",
            "relation": "targets",
            "to_type": "enzyme",
            "to_id": "adenosine-deaminase",
        })
        # Traverse 2 hops from species → compound → target
        resp = client.get(
            "/knowledge/graph/neighbors",
            params={
                "entity_type": "species",
                "entity_id": "sp-cordyceps-militaris",
                "max_depth": 2,
            },
        )
        assert resp.status_code == 200
        neighbors = resp.json()
        ids = [n["entity_id"] for n in neighbors]
        assert "cpd-cordycepin" in ids
        assert "adenosine-deaminase" in ids


class TestEmbeddings:
    def test_index_and_search(self, client):
        # Index some content
        client.post("/knowledge/embeddings/index", json={
            "source_type": "paper",
            "source_id": "paper-001",
            "content": "Beta-glucans from shiitake mushrooms show immunomodulatory effects",
            "metadata": {"domain": "mycology"},
        })
        client.post("/knowledge/embeddings/index", json={
            "source_type": "paper",
            "source_id": "paper-002",
            "content": "Psilocybin therapy for treatment-resistant depression",
            "metadata": {"domain": "neuroscience"},
        })
        client.post("/knowledge/embeddings/index", json={
            "source_type": "note",
            "source_id": "note-001",
            "content": "Cordycepin inhibits adenosine deaminase with high selectivity",
        })

        # Search for related content
        resp = client.get("/knowledge/embeddings/search?query=mushroom immune system")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1

    def test_filter_search_by_type(self, client):
        resp = client.get(
            "/knowledge/embeddings/search?query=mushroom&source_type=paper"
        )
        assert resp.status_code == 200
        for r in resp.json():
            assert r["source_type"] == "paper"

    def test_embedding_stats(self, client):
        resp = client.get("/knowledge/embeddings/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total_embeddings"] >= 3
        assert "paper" in stats["by_source_type"]
