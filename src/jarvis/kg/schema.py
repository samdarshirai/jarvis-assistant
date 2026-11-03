from jarvis.db import USER_REF
from jarvis.kg.predicates import KINDS, SINGLE, TRUST

# Every cross-table reference carries user_id, so a row can never point at another user's row.
# SET NULL names its column: a plain composite SET NULL would null the NOT NULL user_id too.
_SINGLE_IN = ", ".join(f"'{p}'" for p in sorted(SINGLE))
_KINDS_IN = ", ".join(f"'{k}'" for k in KINDS)
_SOURCE_IN = ", ".join(f"'{k}'" for k in TRUST)

KG_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS entity (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  kind TEXT NOT NULL CHECK (kind IN ({_KINDS_IN})),
  display_name TEXT NOT NULL,
  merged_into INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, merged_into) REFERENCES entity (user_id, id) ON DELETE SET NULL (merged_into)
);
CREATE UNIQUE INDEX IF NOT EXISTS entity_one_self ON entity (user_id) WHERE kind = 'self';
CREATE TABLE IF NOT EXISTS entity_key (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  entity_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  value_norm TEXT NOT NULL,
  shared BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, entity_id, type, value_norm),
  FOREIGN KEY (user_id, entity_id) REFERENCES entity (user_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS entity_key_lookup_idx ON entity_key (user_id, type, value_norm);
CREATE TABLE IF NOT EXISTS source (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  kind TEXT NOT NULL CHECK (kind IN ({_SOURCE_IN})),
  ref TEXT,
  observed_at TIMESTAMPTZ NOT NULL,
  excerpt TEXT,
  trust INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'live' CHECK (status IN ('live', 'gone')),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, id),
  UNIQUE (user_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS fact (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  subject_id INTEGER NOT NULL,
  predicate TEXT NOT NULL,
  object_entity_id INTEGER,
  object_value TEXT,
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'pending', 'retracted')),
  superseded_by INTEGER,
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, subject_id) REFERENCES entity (user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, object_entity_id) REFERENCES entity (user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, superseded_by) REFERENCES fact (user_id, id) ON DELETE SET NULL (superseded_by)
);
CREATE INDEX IF NOT EXISTS fact_subject_idx ON fact (user_id, subject_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS fact_one_active_single_idx ON fact (user_id, subject_id, predicate)
  WHERE status = 'active' AND predicate IN ({_SINGLE_IN});
CREATE TABLE IF NOT EXISTS fact_source (
  {USER_REF},
  fact_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (fact_id, source_id),
  FOREIGN KEY (user_id, fact_id) REFERENCES fact (user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, source_id) REFERENCES source (user_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS suppression (
  {USER_REF},
  key_type TEXT NOT NULL,
  key_hmac TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('hard', 'name')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key_type, key_hmac)
);
-- Facts the extractor noticed in a conversation, waiting for the user's decision (never in the graph itself).
-- subject_norm / value_norm are kg.facts.norm of subject_name / value: the keys for "already proposed or dismissed".
CREATE TABLE IF NOT EXISTS fact_candidate (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  conversation_id INTEGER NOT NULL,
  subject_name TEXT NOT NULL,
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ({_KINDS_IN})),
  relation TEXT,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  subject_norm TEXT NOT NULL,
  value_norm TEXT NOT NULL,
  replaces_fact_id INTEGER,
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'approved', 'dismissed', 'expired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ,
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, conversation_id) REFERENCES conversation (user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, replaces_fact_id) REFERENCES fact (user_id, id) ON DELETE SET NULL (replaces_fact_id)
);
CREATE INDEX IF NOT EXISTS fact_candidate_status_idx ON fact_candidate (user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS fact_candidate_seen_idx ON fact_candidate (user_id, subject_norm, predicate, value_norm);
CREATE INDEX IF NOT EXISTS conversation_summary_tsv ON conversation USING GIN (summary_tsv);
-- Short-term memory: time-bound things the user mentioned ("in Pune till Friday", "waiting on the plumber"). Saved
-- without a confirmation (the chat shows it with Undo), shown in every prompt while active, expired by the clock.
CREATE TABLE IF NOT EXISTS memory_item (
  id SERIAL PRIMARY KEY,
  {USER_REF},
  kind TEXT NOT NULL CHECK (kind IN ('context', 'plan', 'commitment')),
  text TEXT NOT NULL,
  text_norm TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'promoted')),
  origin TEXT NOT NULL CHECK (origin IN ('tool', 'digest')),
  conversation_id INTEGER,
  quote TEXT,
  durable BOOLEAN NOT NULL DEFAULT false,  -- the digest thinks this may be lasting: consolidation suggests keeping it
  seen_count INTEGER NOT NULL DEFAULT 1,  -- conversations that mentioned it
  seen_in INTEGER[] NOT NULL DEFAULT '{{}}',  -- their ids, so one conversation counts once
  suggested_at TIMESTAMPTZ,  -- a keep-forever candidate was put in the Learned inbox
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ,  -- promoted or expired (a deleted item is gone)
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, conversation_id) REFERENCES conversation (user_id, id) ON DELETE SET NULL (conversation_id)
);
CREATE INDEX IF NOT EXISTS memory_item_active_idx ON memory_item (user_id, status, expires_at);
CREATE INDEX IF NOT EXISTS memory_item_tsv_idx ON memory_item USING GIN (tsv);
-- Meaning-based search: one vector per fact, item, conversation summary or note. Plain REAL[] so any Postgres works
-- (no pgvector); recall compares them in Python, fine at one person's scale. `digest` is the sha256 of the text that
-- was embedded, so an edited text is embedded again; a row whose source is gone is deleted by the backfill job.
CREATE TABLE IF NOT EXISTS memory_embedding (
  {USER_REF},
  ref_kind TEXT NOT NULL CHECK (ref_kind IN ('fact', 'item', 'conversation', 'note')),
  ref_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  digest TEXT NOT NULL,
  vec REAL[] NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, ref_kind, ref_id)
);
"""
