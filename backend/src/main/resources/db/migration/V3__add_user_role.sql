-- =============================================================================
-- QuantEdge AI — V3 User Role Schema Addendum
-- Migration: V3__add_user_role.sql
-- Compatible: PostgreSQL 15+  |  Flyway baseline ≥ V1
-- =============================================================================
-- PURPOSE:
--   Adds role column to users table to support server-side authorization
--   and isolation between regular trading users (USER) and developers (DEVELOPER/ADMIN).
-- =============================================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT 'USER';

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
