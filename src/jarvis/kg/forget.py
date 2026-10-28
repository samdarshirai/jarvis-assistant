"""Forget an entity and keep it forgotten: graph rows go, a pseudonymous suppression list stays."""
import hashlib
import hmac
import re

from jarvis.kg.facts import norm

HARD = ("email", "phone", "google_contact_id")
_EDGE = "\"'`.,;:!?()[]{}<>*"


def _phone(v: str) -> str:
    return ("+" if v.startswith("+") else "") + re.sub(r"\D", "", v)


def _tokens(text: str) -> tuple[list[str], list[str]]:
    """norm tokens as written, and with edge punctuation and possessives stripped."""
    raw = norm(text).split()
    clean = []
    for t in raw:
        t = t.strip(_EDGE)
        for s in ("'s", "’s"):
            t = t.removesuffix(s)
        if t := t.strip(_EDGE + "’"):
            clean.append(t)
    return raw, clean


def grams(text: str) -> set[str]:
    """1-3 token n-grams (single tokens under 3 chars dropped), plus phone-normalised forms."""
    out = set()
    for toks in _tokens(text):
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i:i + n])
                if n > 1 or len(g) >= 3:
                    out.add(g)
                if len(p := _phone(g)) >= 6:
                    out.add(p)
    return out


def _mentions(text: str, terms: set[str]) -> bool:
    if not text or not terms:
        return False
    gs = grams(text)
    joined = [f" {' '.join(t)} " for t in _tokens(text)]
    return any(t in gs or any(f" {t} " in j for j in joined) for t in terms)


def hmacs(store, conn, values) -> list[str] | None:
    """HMAC-SHA256 of each normalised value under this user's key; None when nothing can be suppressed."""
    if not store.secret:
        if conn.execute("SELECT 1 FROM suppression WHERE user_id = %s LIMIT 1", (store.user_id,)).fetchone():
            raise ValueError("Suppressions exist but no secret is configured.")
        return None
    key = hmac.new(store.secret, f"kg-suppress:{store.user_id}".encode(), hashlib.sha256).digest()
    return [hmac.new(key, norm(v).encode(), hashlib.sha256).hexdigest() for v in values]


def redact(store, conn, excerpt: str) -> str:
    """'' if the excerpt mentions a suppressed name or key; one query, skipped for blank excerpts."""
    if not excerpt.strip():
        return excerpt
    hs = hmacs(store, conn, grams(excerpt))
    if hs is None:
        return excerpt
    hit = conn.execute("SELECT 1 FROM suppression WHERE user_id = %s AND key_hmac = ANY(%s) LIMIT 1",
                       (store.user_id, hs)).fetchone()
    return "" if hit else excerpt


def _plan(store, conn, entity_id: int, include_mentions: bool):
    if not store.secret:
        raise ValueError("Cannot forget without a suppression secret.")
    uid = store.user_id
    row = conn.execute("SELECT kind, display_name FROM entity WHERE user_id = %s AND id = %s FOR UPDATE",
                       (uid, entity_id)).fetchone()
    if not row:
        raise ValueError("unknown entity")
    if row[0] == "self":
        raise ValueError("The self entity cannot be forgotten.")
    keys = conn.execute("SELECT type, value_norm FROM entity_key WHERE user_id = %s AND entity_id = %s",
                        (uid, entity_id)).fetchall()
    names = {v for t, v in keys if t in ("name", "alias")} | {norm(row[1])}
    # a full name is one that is not just a token of another name ("anna" inside "anna müller")
    full = {v for v in names if not any(v != w and v in w.split() for w in names)}
    terms = full | {v for v in names if len(v) >= 3} | {v for t, v in keys if t in HARD}

    own = [r[0] for r in conn.execute("SELECT id FROM fact WHERE user_id = %s AND (subject_id = %s"
                                      " OR object_entity_id = %s)", (uid, entity_id, entity_id)).fetchall()]
    mentions = []
    if include_mentions:
        # ponytail: scans every value of the user's graph in Python; fine for a personal graph, trigram index if huge
        mentions = [i for i, v in conn.execute(
            "SELECT id, object_value FROM fact WHERE user_id = %s AND id <> ALL(%s) AND object_value IS NOT NULL",
            (uid, own)).fetchall() if _mentions(v, terms)]
    gone = own + mentions
    # sources that keep a link to a surviving fact (or never had one) stay, and get redacted if they mention X
    redact_ids = [i for i, ex in conn.execute(
        "SELECT s.id, s.excerpt FROM source s WHERE s.user_id = %s AND coalesce(s.excerpt, '') <> ''"
        " AND (EXISTS (SELECT 1 FROM fact_source fs WHERE fs.user_id = s.user_id AND fs.source_id = s.id"
        "              AND fs.fact_id <> ALL(%s))"
        "  OR NOT EXISTS (SELECT 1 FROM fact_source fs WHERE fs.user_id = s.user_id AND fs.source_id = s.id))",
        (uid, gone)).fetchall() if _mentions(ex, terms)]

    rows = [("hard", t, v) for t, v in keys if t in HARD] + [("name", t, v) for t, v in keys if t in ("name", "alias")
                                                             and v in full]
    if not any(t == "name" for _, t, _ in rows):  # display name always counts, even if its key went missing
        rows.append(("name", "name", norm(row[1])))
    sup = list({(t, h): (k, t, h) for (k, t, _), h in zip(rows, hmacs(store, conn, [v for *_, v in rows]))}.values())
    existing = set(conn.execute("SELECT key_type, key_hmac FROM suppression WHERE user_id = %s",
                                (uid,)).fetchall())
    new = [s for s in sup if (s[1], s[2]) not in existing]
    counts = {"entity": row[1], "facts": len(own), "redacted_excerpts": len(redact_ids),
              "mentions_deleted": len(mentions), "suppressed": len(new)}
    return counts, gone, redact_ids, sup


def preview_forget(store, entity_id: int, *, include_mentions: bool = True) -> dict:
    with store.pool.connection() as conn:
        counts = _plan(store, conn, entity_id, include_mentions)[0]
        conn.rollback()  # drop the FOR UPDATE lock; nothing was written
    return counts


def forget_entity(store, entity_id: int, *, include_mentions: bool = True) -> dict:
    """Delete the entity, its facts, facts about or mentioning it; redact excerpts; suppress its names and keys."""
    uid = store.user_id
    with store.pool.connection() as conn:
        counts, gone, redact_ids, sup = _plan(store, conn, entity_id, include_mentions)
        srcs = [r[0] for r in conn.execute("SELECT DISTINCT source_id FROM fact_source WHERE user_id = %s"
                                           " AND fact_id = ANY(%s)", (uid, gone)).fetchall()]
        conn.execute("DELETE FROM fact WHERE user_id = %s AND id = ANY(%s)", (uid, gone))
        conn.execute("DELETE FROM entity WHERE user_id = %s AND id = %s", (uid, entity_id))
        conn.execute("DELETE FROM source s WHERE s.user_id = %s AND s.id = ANY(%s) AND NOT EXISTS"
                     " (SELECT 1 FROM fact_source fs WHERE fs.user_id = s.user_id AND fs.source_id = s.id)", (uid, srcs))
        conn.execute("UPDATE source SET excerpt = '' WHERE user_id = %s AND id = ANY(%s)", (uid, redact_ids))
        counts["suppressed"] = sum(conn.execute(
            "INSERT INTO suppression (user_id, key_type, key_hmac, kind) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING RETURNING 1", (uid, t, h, k)).fetchone() is not None for k, t, h in sup)
    store.version += 1
    return counts
