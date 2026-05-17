-- migrations/002_dim_patients_add_contact_fields.sql
-- Adds name, phone and address columns to gold.dim_patients
-- Run once on the VPS before deploying the updated load_gold_vps.py
-- Date: 2026-05-17

ALTER TABLE gold.dim_patients
    ADD COLUMN IF NOT EXISTS nome             TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS telefone         TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS bairro           TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS cidade           TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS endereco_entrega TEXT DEFAULT '';

COMMENT ON COLUMN gold.dim_patients.nome             IS 'Nome do paciente (LGPD: legítimo interesse clínico)';
COMMENT ON COLUMN gold.dim_patients.telefone         IS 'Telefone de contato (LGPD: legítimo interesse clínico)';
COMMENT ON COLUMN gold.dim_patients.bairro           IS 'Bairro — EspoCRM address_street';
COMMENT ON COLUMN gold.dim_patients.cidade           IS 'Cidade — EspoCRM address_city';
COMMENT ON COLUMN gold.dim_patients.endereco_entrega IS 'Nome da Rua e Número — EspoCRM c_delivery_street';
