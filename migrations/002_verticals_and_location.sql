-- Expand existing installations for vertical campaign launches and geography.
ALTER TABLE prospects DROP CONSTRAINT IF EXISTS prospects_industry_check;
ALTER TABLE prospects ADD CONSTRAINT prospects_industry_check CHECK (industry IN
  ('eCommerce','Roofing','HVAC','Dental','Garage Door Repair','Plumbing','Emergency Towing',
   'Water Restoration','Mold Remediation','Pest Control','Electrical','Foundation Repair',
   'Tree Service','Pool Service','Landscaping / Lawn Care','Med Spa','Final Expense','Auto Insurance'));
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS location VARCHAR(200) NOT NULL DEFAULT 'Houston, TX';
ALTER TABLE prospects DROP CONSTRAINT IF EXISTS prospects_score_check;
ALTER TABLE prospects ADD CONSTRAINT prospects_score_check CHECK (score BETWEEN 65 AND 100) NOT VALID;
