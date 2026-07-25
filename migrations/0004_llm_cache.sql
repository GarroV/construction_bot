CREATE TABLE llm_cache (
    material_hash TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
