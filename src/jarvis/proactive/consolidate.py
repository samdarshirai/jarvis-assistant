"""Memory upkeep, the way sleep consolidates memory: every run expires short-term notes past their date and embeds what
changed (for recall); once a night it also folds duplicate notes together and suggests keeping the ones that keep coming
up, through the Learned inbox (the user decides; nothing becomes a lasting fact on its own)."""
import asyncio
import logging
from datetime import datetime

from jarvis.kg.candidates import CandidateStore
from jarvis.kg.facts import norm
from jarvis.memory import search
from jarvis.memory.embed import dot
from jarvis.memory.store import same

log = logging.getLogger(__name__)
NIGHT_HOUR = 3  # local hour from which the nightly part runs (once a day)
NEAR = 0.92  # cosine above which two notes mean the same


def dedupe(memory, embedder=None) -> int:
    """Merge active notes that say the same thing; the older one is kept. Blocking."""
    items = memory.active()
    vecs = memory.item_vectors([i.id for i in items], embedder.model) if embedder is not None and items else {}
    gone, merged = set(), 0
    for a_i, a in enumerate(items):
        if a.id in gone:
            continue
        for b in items[a_i + 1:]:
            if b.id in gone:
                continue
            near = a.id in vecs and b.id in vecs and dot(vecs[a.id], vecs[b.id]) >= NEAR
            if same(a.text, b.text) or near:
                keep, drop = (a, b) if a.id < b.id else (b, a)
                if memory.merge(keep.id, drop.id):
                    gone.add(drop.id)
                    merged += 1
    return merged


def suggest(memory, kg, now: datetime) -> int:
    """Notes mentioned in several conversations, or that sounded lasting, become 'keep forever?' candidates. Blocking."""
    cands = CandidateStore(kg.pool, kg.user_id)
    made = 0
    for item, quote in memory.promotable():
        key = ("me", "note", norm(item.text))
        known = cands.known([key]) or any(f.predicate == "note" and norm(f.value) == key[2]
                                          for f in kg.facts_for(kg.self_entity().id))
        with kg.pool.connection() as conn:
            if not known:
                conn.execute(
                    "INSERT INTO fact_candidate (user_id, conversation_id, subject_name, subject_kind, predicate, value,"
                    " quote, subject_norm, value_norm) VALUES (%s, %s, 'me', 'self', 'note', %s, %s, 'me', %s)",
                    (kg.user_id, item.conversation_id, item.text[:200], (quote or item.text)[:300], key[2]))
                made += 1
            memory.mark_suggested(item.id, conn)
    return made


async def run_once(rt, now: datetime, suggest_facts: bool = True) -> dict:
    """One user's upkeep. `now` is the user's local time. `suggest_facts` is off while the Learned inbox is hidden."""
    memory = getattr(rt, "memory", None)
    if memory is None:
        return {}
    out = {"expired": await asyncio.to_thread(memory.expire_due)}
    embedder = getattr(rt, "embedder", None)
    if embedder is not None:
        try:
            out["embedded"] = await search.backfill(memory.pool, memory.user_id, embedder)
        except Exception as e:  # recall falls back to keywords; the next run tries again
            log.warning("embedding backfill failed: %s", type(e).__name__)
    if now.hour >= NIGHT_HOUR and await asyncio.to_thread(rt.proactive.claim_alert, f"consolidate:{now.date().isoformat()}"):
        out["merged"] = await asyncio.to_thread(dedupe, memory, embedder if embedder and embedder.available else None)
        if suggest_facts and getattr(rt, "kg", None) is not None:
            out["suggested"] = await asyncio.to_thread(suggest, memory, rt.kg, now)
    if any(out.values()):
        await asyncio.to_thread(rt.audit.record, "proactive", "consolidate", result=out)
    return out
