-- Pricing columns + premium seed values.
--
-- Adds the display-facing columns the pricing page needs, and fills in
-- the premium monthly prices agreed for launch. Stripe price IDs are
-- populated at runtime from env vars (set by scripts/stripe_bootstrap.py).

ALTER TABLE plans ADD COLUMN IF NOT EXISTS monthly_price_cents      INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS annual_price_cents       INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS stripe_price_id_annual   TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS overage_per_1k_cents     INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS tagline                  TEXT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS highlights               JSONB DEFAULT '[]';
ALTER TABLE plans ADD COLUMN IF NOT EXISTS sort_order               INTEGER DEFAULT 100;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_public                BOOLEAN DEFAULT TRUE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS cta_label                TEXT DEFAULT 'Get started';

-- Premium launch pricing. Annual is ~20% off (2.4 months free).
-- Overage is metered at $0.012 per 1K tokens (premium routed models).

UPDATE plans SET
    monthly_price_cents   = 4900,
    annual_price_cents    = 47000,
    overage_per_1k_cents  = 2,
    tagline               = 'Solo developers shipping with a premium AI assistant.',
    highlights            = '[
        "Crowe Logic extension for VS Code",
        "500K tokens / month on Developer tier models",
        "Bring your own API keys (BYOK) for frontier models",
        "10 Crowe Vision jobs / month",
        "Email support"
    ]'::jsonb,
    sort_order            = 10,
    cta_label             = 'Start Developer'
WHERE id = 'developer';

UPDATE plans SET
    monthly_price_cents   = 12900,
    annual_price_cents    = 124000,
    overage_per_1k_cents  = 2,
    tagline               = 'For professionals who live in the IDE. Hosted remote IDE + Studio-tier models.',
    highlights            = '[
        "Everything in Developer",
        "5M tokens / month on Studio-tier models (DeepSeek R1, Mistral Large 3, Kimi K2.5, MiniMax M2.5)",
        "Hosted remote IDE, 100 container hours / month",
        "500 Crowe Vision jobs / month, 10 GB workspace storage",
        "Up to 3 seats, 2 concurrent sessions",
        "Priority support"
    ]'::jsonb,
    sort_order            = 20,
    cta_label             = 'Start Studio'
WHERE id = 'studio';

UPDATE plans SET
    monthly_price_cents   = 39900,
    annual_price_cents    = 383000,
    overage_per_1k_cents  = 1,
    tagline               = 'Small teams and research labs with Lab-tier models, private datasets, and a dedicated session pool.',
    highlights            = '[
        "Everything in Studio",
        "50M tokens / month on Lab-tier models (Claude Opus 4.6, GPT-5.4, Gemini 2.x)",
        "500 hosted IDE hours / month, 100 GB workspace storage",
        "Up to 10 seats, 5 concurrent sessions",
        "Private datasets + notebook collaboration",
        "SSO (Google, GitHub), audit log, 1-year retention",
        "Named technical contact, early-access features"
    ]'::jsonb,
    sort_order            = 30,
    cta_label             = 'Start Lab'
WHERE id = 'lab';

UPDATE plans SET
    monthly_price_cents   = NULL,
    annual_price_cents    = NULL,
    overage_per_1k_cents  = 0,
    tagline               = 'Unlimited seats, dedicated compute, Enterprise-tier models, SLA, and deployment options including on-prem and BYO-cloud.',
    highlights            = '[
        "Unlimited seats and concurrent sessions",
        "Enterprise-tier models (GPT-5.4 Pro, Grok 4.20 Reasoning, Claude Opus 4.5)",
        "Dedicated session pool or BYO-cloud (AWS / Azure / GCP)",
        "Private gateway, SSO with SCIM, 7-year audit retention",
        "Domain-specific CroweLM fine-tuning (Titan / Prime)",
        "Mutual SLA, named engineering liaison, 24x7 incident response",
        "Volume and commitment discounts"
    ]'::jsonb,
    sort_order            = 40,
    cta_label             = 'Talk to sales'
WHERE id = 'enterprise';
