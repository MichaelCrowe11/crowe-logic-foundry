"""Tests for domain modules — mycology, vision, research, compound discovery."""

import pytest
from datetime import date
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Create a test client with all domain routers mounted."""
    from fastapi import FastAPI
    from domain.mycology import router as mycology_router
    from domain.vision import router as vision_router
    from domain.research import router as research_router
    from domain.compound import router as compound_router

    app = FastAPI()
    app.include_router(mycology_router)
    app.include_router(vision_router)
    app.include_router(research_router)
    app.include_router(compound_router)
    return TestClient(app)


# ── Mycology ─────────────────────────────────────────────────────────

class TestMycology:
    def test_list_protocols(self, client):
        resp = client.get("/mycology/protocols")
        assert resp.status_code == 200
        protocols = resp.json()
        assert len(protocols) >= 3  # seeded defaults
        names = [p["name"] for p in protocols]
        assert any("Shiitake" in n for n in names)
        assert any("Oyster" in n for n in names)
        assert any("Lion" in n for n in names)

    def test_get_protocol_by_id(self, client):
        resp = client.get("/mycology/protocols/proto-shiitake-sawdust")
        assert resp.status_code == 200
        p = resp.json()
        assert p["species"] == "Lentinula edodes"
        assert len(p["steps"]) >= 5

    def test_filter_protocols_by_species(self, client):
        resp = client.get("/mycology/protocols?species=Pleurotus")
        assert resp.status_code == 200
        assert all("Pleurotus" in p["species"] for p in resp.json())

    def test_create_strain(self, client):
        resp = client.post("/mycology/strains", json={
            "name": "Blue Oyster #7",
            "species": "Pleurotus ostreatus var. columbinus",
            "source": "Southwest Mushrooms LC",
            "generation": 3,
            "optimal_temp_c": 24.0,
        })
        assert resp.status_code == 200
        strain = resp.json()
        assert strain["name"] == "Blue Oyster #7"
        assert strain["generation"] == 3
        return strain["id"]

    def test_list_strains(self, client):
        # Create one first
        client.post("/mycology/strains", json={
            "name": "Test Strain",
            "species": "Test species",
        })
        resp = client.get("/mycology/strains")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_create_grow_log(self, client):
        # Create strain first
        strain = client.post("/mycology/strains", json={
            "name": "GL Strain",
            "species": "Lentinula edodes",
        }).json()

        resp = client.post("/mycology/grow-logs", json={
            "strain_id": strain["id"],
            "batch_code": "SWM-2026-042",
            "substrate": "supplemented_sawdust",
            "inoculation_date": "2026-04-01",
            "spawn_weight_g": 500.0,
            "substrate_weight_g": 2500.0,
            "temp_c": 21.0,
        })
        assert resp.status_code == 200
        log = resp.json()
        assert log["phase"] == "inoculation"
        assert log["batch_code"] == "SWM-2026-042"
        return log["id"]

    def test_update_grow_log_phase(self, client):
        strain = client.post("/mycology/strains", json={
            "name": "Phase Test",
            "species": "Test",
        }).json()
        log = client.post("/mycology/grow-logs", json={
            "strain_id": strain["id"],
            "batch_code": "PHASE-001",
            "substrate": "grain",
            "inoculation_date": "2026-04-01",
        }).json()

        resp = client.patch(
            f"/mycology/grow-logs/{log['id']}/phase?phase=colonization"
        )
        assert resp.status_code == 200
        assert resp.json()["phase"] == "colonization"

    def test_record_harvest(self, client):
        strain = client.post("/mycology/strains", json={
            "name": "Harvest Strain",
            "species": "Pleurotus ostreatus",
        }).json()
        log = client.post("/mycology/grow-logs", json={
            "strain_id": strain["id"],
            "batch_code": "HARVEST-001",
            "substrate": "straw",
            "inoculation_date": "2026-03-01",
            "substrate_weight_g": 3000.0,
        }).json()

        resp = client.post("/mycology/harvests", json={
            "grow_log_id": log["id"],
            "flush_number": 1,
            "harvest_date": "2026-04-10",
            "wet_weight_g": 1200.0,
        })
        assert resp.status_code == 200
        assert resp.json()["wet_weight_g"] == 1200.0

    def test_contamination_rate_analytics(self, client):
        resp = client.get("/mycology/analytics/contamination-rate")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "rate" in data

    def test_yield_summary_analytics(self, client):
        resp = client.get("/mycology/analytics/yield-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_harvested_g" in data

    def test_generate_sop_matching(self, client):
        resp = client.post("/mycology/sop/generate", json={
            "species": "Lentinula edodes",
            "substrate": "supplemented_sawdust",
        })
        assert resp.status_code == 200
        sop = resp.json()
        assert "steps" in sop
        assert len(sop["steps"]) >= 5

    def test_generate_sop_generic(self, client):
        resp = client.post("/mycology/sop/generate", json={
            "species": "Unknown exotic",
            "substrate": "custom",
        })
        assert resp.status_code == 200
        assert "steps" in resp.json()


# ── Research ─────────────────────────────────────────────────────────

class TestResearch:
    def test_create_paper(self, client):
        resp = client.post("/research/papers", json={
            "title": "Beta-glucans from Lentinula edodes: a review",
            "authors": ["Smith, J.", "Zhang, W."],
            "domain": "mycology",
            "keywords": ["beta-glucan", "lentinan", "immunomodulation"],
        })
        assert resp.status_code == 200
        assert resp.json()["title"].startswith("Beta-glucans")

    def test_list_papers_with_filter(self, client):
        client.post("/research/papers", json={
            "title": "Cordycepin biosynthesis",
            "domain": "compound_discovery",
            "keywords": ["cordycepin", "biosynthesis"],
        })
        resp = client.get("/research/papers?domain=compound_discovery")
        assert resp.status_code == 200
        assert all(p["domain"] == "compound_discovery" for p in resp.json())

    def test_create_experiment(self, client):
        resp = client.post("/research/experiments", json={
            "title": "Substrate optimization for Lion's Mane BE",
            "hypothesis": "Masters Mix with 10% wheat bran increases BE by 20%",
            "methodology": "Randomized block design, n=30 per treatment",
            "domain": "cultivation",
            "variables": {"substrate_ratio": "50/50 vs 50/40/10"},
            "controls": ["Standard Masters Mix 50/50"],
        })
        assert resp.status_code == 200
        exp = resp.json()
        assert exp["status"] == "planned"

    def test_update_experiment_status(self, client):
        exp = client.post("/research/experiments", json={
            "title": "Status Test",
            "hypothesis": "Test",
            "methodology": "Test",
        }).json()

        resp = client.patch(
            f"/research/experiments/{exp['id']}/status?status=in_progress"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_add_data_point(self, client):
        exp = client.post("/research/experiments", json={
            "title": "Data Point Test",
            "hypothesis": "Test",
            "methodology": "Test",
        }).json()

        resp = client.post(
            f"/research/experiments/{exp['id']}/data-point",
            json={"day": 7, "colonization_pct": 45.2, "temp_c": 22.1},
        )
        assert resp.status_code == 200
        assert resp.json()["total_points"] == 1

    def test_create_note(self, client):
        resp = client.post("/research/notes", json={
            "title": "Observation: early primordia on BRF blocks",
            "content": "Day 18 blocks showing primordia 3 days ahead of schedule",
            "domain": "cultivation",
            "tags": ["primordia", "BRF", "early"],
        })
        assert resp.status_code == 200

    def test_generate_protocol(self, client):
        resp = client.post("/research/protocols/generate", json={
            "title": "Mycelium Growth Rate Comparison",
            "objective": "Compare radial growth rates of 5 strains on MEA",
            "materials": ["MEA plates", "5 strains", "parafilm", "ruler"],
        })
        assert resp.status_code == 200
        proto = resp.json()
        assert "protocol" in proto
        assert "4_methodology" in proto["protocol"]

    def test_research_overview(self, client):
        resp = client.get("/research/analytics/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "papers" in data
        assert "experiments" in data


# ── Compound Discovery ───────────────────────────────────────────────

class TestCompound:
    def test_list_seeded_compounds(self, client):
        resp = client.get("/compound/compounds")
        assert resp.status_code == 200
        compounds = resp.json()
        assert len(compounds) >= 6  # seeded defaults
        names = [c["name"] for c in compounds]
        assert "Lentinan" in names
        assert "Cordycepin" in names
        assert "Psilocybin" in names

    def test_get_compound_by_id(self, client):
        resp = client.get("/compound/compounds/cpd-lentinan")
        assert resp.status_code == 200
        c = resp.json()
        assert c["source_organism"] == "Lentinula edodes"
        assert "antitumor" in c["bioactivities"]

    def test_filter_by_bioactivity(self, client):
        resp = client.get("/compound/compounds?bioactivity=neuroprotective")
        assert resp.status_code == 200
        compounds = resp.json()
        assert len(compounds) >= 2  # hericenone, erinacine, psilocybin
        for c in compounds:
            assert "neuroprotective" in c["bioactivities"]

    def test_filter_by_source(self, client):
        resp = client.get("/compound/compounds?source_type=fungal")
        assert resp.status_code == 200
        assert all(c["source_type"] == "fungal" for c in resp.json())

    def test_search_compounds(self, client):
        resp = client.get("/compound/compounds?search=Lion")
        assert resp.status_code == 200
        # Hericenone C and Erinacine A are from Lion's Mane
        assert len(resp.json()) >= 1

    def test_create_compound(self, client):
        resp = client.post("/compound/compounds", json={
            "name": "Ergothioneine",
            "formula": "C9H15N3O2S",
            "molecular_weight": 229.30,
            "source_organism": "Pleurotus ostreatus",
            "compound_class": "other",
            "bioactivities": ["antioxidant"],
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ergothioneine"

    def test_record_assay(self, client):
        resp = client.post("/compound/assays", json={
            "compound_id": "cpd-cordycepin",
            "activity_type": "antitumor",
            "target": "HeLa cells",
            "ic50_um": 12.5,
            "result_summary": "Dose-dependent inhibition of HeLa proliferation",
        })
        assert resp.status_code == 200
        assert resp.json()["ic50_um"] == 12.5

    def test_assay_requires_valid_compound(self, client):
        resp = client.post("/compound/assays", json={
            "compound_id": "nonexistent",
            "activity_type": "antimicrobial",
            "result_summary": "Test",
        })
        assert resp.status_code == 404

    def test_lipinski_check(self, client):
        resp = client.post("/compound/admet/predict-lipinski?compound_id=cpd-cordycepin")
        assert resp.status_code == 200
        data = resp.json()
        assert "drug_like" in data
        assert data["mw_under_500"] is True  # MW=251.24

    def test_add_target(self, client):
        resp = client.post("/compound/targets", json={
            "compound_id": "cpd-psilocybin",
            "target_name": "5-HT2A Receptor",
            "target_type": "protein",
            "interaction_type": "agonist",
        })
        assert resp.status_code == 200
        assert resp.json()["target_name"] == "5-HT2A Receptor"

    def test_pipeline_analytics(self, client):
        resp = client.get("/compound/analytics/pipeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_compounds"] >= 6
        assert "by_development_stage" in data
        assert "clinical" in data["by_development_stage"]

    def test_organisms_summary(self, client):
        resp = client.get("/compound/analytics/organisms")
        assert resp.status_code == 200
        data = resp.json()
        assert "Hericium erinaceus" in data
        assert data["Hericium erinaceus"]["count"] >= 2


# ── Vision ───────────────────────────────────────────────────────────

class TestVision:
    def test_vision_health(self, client):
        resp = client.get("/vision/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "backend_available" in data

    def test_list_analyses_empty(self, client):
        resp = client.get("/vision/analyses")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_analyze_image_url(self, client, monkeypatch):
        async def fake_fetch(_image_url):
            return b"fake-image"

        monkeypatch.setattr("domain.vision._fetch_image_url", fake_fetch)
        resp = client.post("/vision/analyze-url", json={
            "image_url": "https://example.com/test.png",
            "analysis_type": "general",
            "context": "Tray photo",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis_type"] == "general"
        assert "results" in data

    def test_contamination_check_url(self, client, monkeypatch):
        async def fake_fetch(_image_url):
            return b"fake-image"

        monkeypatch.setattr("domain.vision._fetch_image_url", fake_fetch)
        resp = client.post("/vision/contamination-check-url", json={
            "image_url": "https://example.com/test.png",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "detected" in data
        assert "severity" in data
