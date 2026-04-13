-- Knowledge Plane Schema — Embeddings, Research Graph, Domain Persistence
--
-- Requires pgvector extension (Neon has it pre-installed).
-- Extends 002_control_plane.sql.

CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Embeddings Store ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS embeddings (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id   TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    source_type    TEXT NOT NULL,            -- paper | experiment | note | sop | protocol | compound | strain
    source_id      TEXT NOT NULL,            -- FK to the domain entity
    chunk_index    INT NOT NULL DEFAULT 0,   -- for long documents split into chunks
    content        TEXT NOT NULL,            -- original text chunk
    embedding      vector(1536),            -- OpenAI text-embedding-3-small dimension
    metadata       JSONB DEFAULT '{}',       -- extra searchable fields
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_type, source_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_source
    ON embeddings (source_type, source_id);

CREATE INDEX IF NOT EXISTS idx_embeddings_workspace
    ON embeddings (workspace_id);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_embeddings_vector
    ON embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- ─── Species Taxonomy ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS species (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    scientific_name TEXT UNIQUE NOT NULL,
    common_name     TEXT,
    kingdom         TEXT DEFAULT 'Fungi',
    phylum          TEXT,
    family          TEXT,
    genus           TEXT,
    description     TEXT,
    edibility       TEXT,                     -- edible | medicinal | toxic | psychoactive | unknown
    habitat         TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_species_genus ON species (genus);


-- ─── Strains (persistent, linked to species) ────────────────────────

CREATE TABLE IF NOT EXISTS strains (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id    TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    species_id      TEXT REFERENCES species(id),
    name            TEXT NOT NULL,
    source          TEXT,
    generation      INT DEFAULT 0,
    optimal_temp_c  REAL,
    optimal_humidity_pct REAL,
    incubation_days INT,
    fruiting_days   INT,
    notes           TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_strains_workspace ON strains (workspace_id);
CREATE INDEX IF NOT EXISTS idx_strains_species ON strains (species_id);


-- ─── Grow Logs (persistent) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS grow_logs (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id      TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    strain_id         TEXT REFERENCES strains(id),
    batch_code        TEXT,
    substrate         TEXT,
    inoculation_date  DATE,
    phase             TEXT DEFAULT 'inoculation',
    spawn_weight_g    REAL,
    substrate_weight_g REAL,
    temp_c            REAL,
    humidity_pct      REAL,
    contaminated      BOOLEAN DEFAULT false,
    notes             TEXT,
    metadata          JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grow_logs_workspace ON grow_logs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_grow_logs_strain ON grow_logs (strain_id);
CREATE INDEX IF NOT EXISTS idx_grow_logs_phase ON grow_logs (phase);


-- ─── Harvests ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS harvests (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    grow_log_id     TEXT NOT NULL REFERENCES grow_logs(id) ON DELETE CASCADE,
    flush_number    INT NOT NULL DEFAULT 1,
    harvest_date    DATE,
    wet_weight_g    REAL,
    dry_weight_g    REAL,
    quality_grade   TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_harvests_grow_log ON harvests (grow_log_id);


-- ─── Research Papers ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS papers (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    authors     TEXT[],
    abstract    TEXT,
    doi         TEXT,
    url         TEXT,
    domain      TEXT,
    keywords    TEXT[],
    full_text   TEXT,
    metadata    JSONB DEFAULT '{}',
    added_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_papers_workspace ON papers (workspace_id);
CREATE INDEX IF NOT EXISTS idx_papers_domain ON papers (domain);


-- ─── Experiments ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS experiments (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id  TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    hypothesis    TEXT,
    methodology   TEXT,
    domain        TEXT,
    status        TEXT DEFAULT 'planned',    -- planned | in_progress | completed | abandoned
    variables     JSONB DEFAULT '{}',
    controls      JSONB DEFAULT '[]',
    results       JSONB DEFAULT '{}',
    data_points   JSONB DEFAULT '[]',
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_experiments_workspace ON experiments (workspace_id);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments (status);


-- ─── Compounds (persistent) ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compounds (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id      TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    formula           TEXT,
    molecular_weight  REAL,
    smiles            TEXT,
    inchi_key         TEXT,
    source_organism   TEXT,
    source_type       TEXT DEFAULT 'fungal',
    compound_class    TEXT,
    bioactivities     TEXT[],
    development_stage TEXT DEFAULT 'discovery',
    metadata          JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compounds_workspace ON compounds (workspace_id);
CREATE INDEX IF NOT EXISTS idx_compounds_organism ON compounds (source_organism);
CREATE INDEX IF NOT EXISTS idx_compounds_stage ON compounds (development_stage);


-- ─── Bioactivity Assays ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assays (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    compound_id      TEXT NOT NULL REFERENCES compounds(id) ON DELETE CASCADE,
    activity_type    TEXT NOT NULL,
    target           TEXT,
    ic50_um          REAL,
    ec50_um          REAL,
    ki_nm            REAL,
    selectivity      REAL,
    result_summary   TEXT,
    metadata         JSONB DEFAULT '{}',
    tested_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assays_compound ON assays (compound_id);


-- ─── Compound Targets ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compound_targets (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    compound_id       TEXT NOT NULL REFERENCES compounds(id) ON DELETE CASCADE,
    target_name       TEXT NOT NULL,
    target_type       TEXT,                   -- protein | pathway | receptor | enzyme | gene
    interaction_type  TEXT,                   -- inhibitor | agonist | antagonist | modulator
    binding_affinity  REAL,
    evidence_level    TEXT DEFAULT 'predicted',
    metadata          JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compound_targets_compound ON compound_targets (compound_id);


-- ─── Research Graph Edges ────────────────────────────────────────────
-- Generic relationship table for the knowledge graph.

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    from_type   TEXT NOT NULL,               -- species | strain | compound | paper | experiment
    from_id     TEXT NOT NULL,
    relation    TEXT NOT NULL,               -- produces | targets | cited_in | tested_in | derived_from | contains
    to_type     TEXT NOT NULL,
    to_id       TEXT NOT NULL,
    weight      REAL DEFAULT 1.0,
    evidence    TEXT,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (from_type, from_id, relation, to_type, to_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_edges_from
    ON knowledge_edges (from_type, from_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_to
    ON knowledge_edges (to_type, to_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation
    ON knowledge_edges (relation);


-- ─── Environment Readings (time-series) ──────────────────────────────

CREATE TABLE IF NOT EXISTS environment_readings (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    grow_log_id TEXT REFERENCES grow_logs(id) ON DELETE CASCADE,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    sensor_id   TEXT,
    temp_c      REAL,
    humidity_pct REAL,
    co2_ppm     REAL,
    light_lux   REAL,
    ph           REAL,
    metadata    JSONB DEFAULT '{}',
    recorded_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_env_readings_grow_log
    ON environment_readings (grow_log_id);
CREATE INDEX IF NOT EXISTS idx_env_readings_time
    ON environment_readings (recorded_at DESC);


-- ─── Vision Analyses ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vision_analyses (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    workspace_id   TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    grow_log_id    TEXT REFERENCES grow_logs(id),
    image_url      TEXT NOT NULL,
    analysis_type  TEXT NOT NULL,             -- contamination_check | growth_stage | morphology | general
    backend        TEXT DEFAULT 'gpt-4o',
    results        JSONB NOT NULL DEFAULT '{}',
    confidence     REAL,
    human_reviewed BOOLEAN DEFAULT false,
    reviewer_notes TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vision_workspace ON vision_analyses (workspace_id);
CREATE INDEX IF NOT EXISTS idx_vision_grow_log ON vision_analyses (grow_log_id);


-- ─── Seed Species Taxonomy ───────────────────────────────────────────

INSERT INTO species (id, scientific_name, common_name, genus, family, phylum, edibility, description) VALUES
    ('sp-lentinula-edodes',    'Lentinula edodes',        'Shiitake',          'Lentinula',  'Omphalotaceae',      'Basidiomycota', 'edible',      'Premier edible and medicinal mushroom, source of lentinan'),
    ('sp-pleurotus-ostreatus', 'Pleurotus ostreatus',     'Blue Oyster',       'Pleurotus',  'Pleurotaceae',       'Basidiomycota', 'edible',      'Fast-growing oyster mushroom, commercially important'),
    ('sp-hericium-erinaceus',  'Hericium erinaceus',      'Lion''s Mane',      'Hericium',   'Hericiaceae',        'Basidiomycota', 'medicinal',   'Neuroprotective compounds (hericenones, erinacines)'),
    ('sp-ganoderma-lucidum',   'Ganoderma lucidum',       'Reishi',            'Ganoderma',  'Ganodermataceae',    'Basidiomycota', 'medicinal',   'Adaptogenic mushroom, ganoderic acids'),
    ('sp-cordyceps-militaris', 'Cordyceps militaris',     'Cordyceps',         'Cordyceps',  'Cordycipitaceae',    'Ascomycota',    'medicinal',   'Source of cordycepin, cultivable entomopathogen'),
    ('sp-psilocybe-cubensis',  'Psilocybe cubensis',      'Golden Teacher',    'Psilocybe',  'Hymenogastraceae',   'Basidiomycota', 'psychoactive','Source of psilocybin and psilocin'),
    ('sp-trametes-versicolor', 'Trametes versicolor',     'Turkey Tail',       'Trametes',   'Polyporaceae',       'Basidiomycota', 'medicinal',   'Source of PSK and PSP, immune modulators'),
    ('sp-inonotus-obliquus',   'Inonotus obliquus',       'Chaga',             'Inonotus',   'Hymenochaetaceae',   'Basidiomycota', 'medicinal',   'Birch-parasitic, high antioxidant content')
ON CONFLICT (scientific_name) DO NOTHING;


-- ─── Seed Knowledge Graph Edges ──────────────────────────────────────

INSERT INTO knowledge_edges (from_type, from_id, relation, to_type, to_id, evidence) VALUES
    ('species', 'sp-lentinula-edodes',    'produces', 'compound', 'cpd-lentinan',       'Well-established β-glucan from shiitake'),
    ('species', 'sp-hericium-erinaceus',  'produces', 'compound', 'cpd-hericenone-c',   'Isolated from fruiting body'),
    ('species', 'sp-hericium-erinaceus',  'produces', 'compound', 'cpd-erinacine-a',    'Isolated from mycelium'),
    ('species', 'sp-psilocybe-cubensis',  'produces', 'compound', 'cpd-psilocybin',     'Primary psychoactive constituent'),
    ('species', 'sp-ganoderma-lucidum',   'produces', 'compound', 'cpd-ganoderic-a',    'Major triterpenoid from reishi'),
    ('species', 'sp-cordyceps-militaris', 'produces', 'compound', 'cpd-cordycepin',     'Adenosine analog from cordyceps')
ON CONFLICT (from_type, from_id, relation, to_type, to_id) DO NOTHING;
