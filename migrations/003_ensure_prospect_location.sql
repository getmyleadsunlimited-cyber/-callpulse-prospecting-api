-- Repair pre-vertical-launcher installations without replacing or rewriting data.
ALTER TABLE prospects
  ADD COLUMN IF NOT EXISTS location VARCHAR(200) NOT NULL DEFAULT 'Houston, TX';
