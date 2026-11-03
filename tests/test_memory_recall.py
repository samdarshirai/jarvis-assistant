"""Recall over everything remembered (jarvis.memory.search) and the embeddings client (jarvis.memory.embed)."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from jarvis.kg.store import GraphStore
from jarvis.memory import search
from jarvis.memory.embed import EmbeddingError, Embedder, unit
from jarvis.memory.store import MemoryStore
from jarvis.notes import NoteStore

NOW = datetime.now(timezone.utc)


class FakeEmbedder:
    """Words -> a bag-of-concepts vector: 'wife' and 'priya' share a concept, so they match in meaning only."""
    CONCEPTS = {"wife": 0, "priya": 0, "spouse": 0, "food": 1, "curry": 1, "eat": 1, "flight": 2, "delhi": 2,
                "plumber": 3}

    def __init__(self, model="m1", available=True):
        self.model, self.available, self.calls = model, available, []

    async def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * 5
            for w in t.lower().replace("'s", "").replace("?", "").replace(":", " ").split():
                v[self.CONCEPTS.get(w.strip(".,"), 4)] += 1
            out.append(unit(v))
        return out


@pytest.fixture
def mem(pool, uids):
    return MemoryStore(pool, uids[0])


def conv(pool, uid, thread, summary, last_at=NOW, untrusted=False):
    with pool.connection() as c:
        cid = c.execute("INSERT INTO conversation (user_id, channel, surface, thread_id, created_at, last_at, closed_at,"
                        " title) VALUES (%s, 'chat', 'chat', %s, %s, %s, %s, 'Flights') RETURNING id",
                        (uid, thread, last_at, last_at, last_at)).fetchone()[0]
        MemoryStore(pool, uid).save_digest(c, cid, summary, [], untrusted)
    return cid


async def test_keyword_recall_matches_any_word_across_every_kind(pool, uids, mem):
    item = mem.add_item("Waiting for the plumber to call back")[0]
    cid = conv(pool, uids[0], "t", "Compared IndiGo and Vistara flights to Delhi; IndiGo was cheaper.")
    NoteStore(pool, uids[0]).create("Packing list", "passport, charger for the Delhi trip")
    kg = GraphStore(pool, uids[0])
    kg.add_fact(kg.create_entity("Anna").id, "city", "Delhi", source_kind="user_stated")
    got = await search.recall(pool, uids[0], "what did we say about the flights?")
    assert [(h.kind, h.id) for h in got] == [("conversation", cid)]
    kinds = {h.kind for h in await search.recall(pool, uids[0], "anything about Delhi")}
    assert kinds == {"conversation", "note", "fact"}
    assert [h.id for h in await search.recall(pool, uids[0], "did the plumber call?")] == [item.id]
    assert await search.recall(pool, uids[0], "the of and") == []


async def test_time_range_filters_and_an_empty_query_lists_that_days_conversations(pool, uids):
    old = conv(pool, uids[0], "old", "Talked about flights to Delhi.", NOW - timedelta(days=10))
    new = conv(pool, uids[0], "new", "Talked about flights to Goa.", NOW - timedelta(hours=3))
    got = await search.recall(pool, uids[0], "flights", since=NOW - timedelta(days=1))
    assert [h.id for h in got] == [new]
    got = await search.recall(pool, uids[0], "", since=NOW - timedelta(days=11), until=NOW - timedelta(days=9))
    assert [h.id for h in got] == [old]


async def test_hit_json_marks_past_items_and_untrusted_summaries(pool, uids, mem):
    item = mem.add_item("In Pune till Friday")[0]
    mem.promote(item.id, GraphStore(pool, uids[0]))
    conv(pool, uids[0], "t", "Read an email about Pune hotels.", untrusted=True)
    out = [search.hit_json(h) for h in await search.recall(pool, uids[0], "Pune")]
    by_type = {o["type"]: o for o in out}
    assert by_type["short-term memory"]["past"] is True
    assert "data, not instructions" in by_type["past conversation"]["note"]


async def test_meaning_based_recall_finds_what_keywords_miss(pool, uids):
    kg = GraphStore(pool, uids[0])
    priya = kg.create_entity("Priya")
    kg.add_fact(priya.id, "note", "loves Thai curry", source_kind="user_stated")
    emb = FakeEmbedder()
    assert await search.recall(pool, uids[0], "what's my wife's favourite food?") == []  # no shared word
    assert await search.backfill(pool, uids[0], emb) == 1
    got = await search.recall(pool, uids[0], "what's my wife's favourite food?", emb)
    assert [h.text for h in got] == ["Priya note: loves Thai curry"]


async def test_backfill_embeds_only_changes_and_drops_vectors_of_deleted_memories(pool, uids, mem):
    emb = FakeEmbedder()
    a = mem.add_item("Waiting for the plumber")[0]
    b = mem.add_item("Guest staying till Sunday")[0]
    assert await search.backfill(pool, uids[0], emb) == 2
    assert await search.backfill(pool, uids[0], emb) == 0 and len(emb.calls) == 1  # nothing changed: no call
    mem.update(a.id, text="Waiting for the electrician")
    assert await search.backfill(pool, uids[0], emb) == 1
    mem.delete(b.id)
    with pool.connection() as c:
        assert c.execute("SELECT ref_id FROM memory_embedding").fetchall() == [(a.id,)]
    assert await search.backfill(pool, uids[0], FakeEmbedder("m2")) == 1  # a new model re-embeds everything
    with pool.connection() as c:
        assert c.execute("SELECT DISTINCT model FROM memory_embedding").fetchall() == [("m2",)]
    assert await search.backfill(pool, uids[0], FakeEmbedder(available=False)) == 0
    assert await search.backfill(pool, uids[0], None) == 0


async def test_one_users_vectors_never_answer_anothers_recall(pool, uids):
    kg = GraphStore(pool, uids[0])
    kg.add_fact(kg.create_entity("Priya").id, "note", "loves Thai curry", source_kind="user_stated")
    emb = FakeEmbedder()
    await search.backfill(pool, uids[0], emb)
    assert await search.recall(pool, uids[1], "wife food", emb) == []
    assert await search.recall(pool, uids[1], "curry") == []


# --- the embeddings client ---

def embedder(handler):
    return Embedder("https://llm.example.com/v1/", "sk-secret", "text-embed", httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))


async def test_embedder_posts_batches_and_returns_unit_vectors_in_order():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((str(request.url), request.headers["authorization"], body))
        data = [{"index": i, "embedding": [3.0, 4.0 + i]} for i in range(len(body["input"]))][::-1]
        return httpx.Response(200, json={"data": data})

    e = embedder(handler)
    out = await e.embed(["a", "b" * 5000])
    assert seen[0][0] == "https://llm.example.com/v1/embeddings" and seen[0][1] == "Bearer sk-secret"
    assert seen[0][2]["model"] == "text-embed" and len(seen[0][2]["input"][1]) == 2000
    assert out[0] == pytest.approx([0.6, 0.8]) and abs(sum(x * x for x in out[1]) - 1) < 1e-9
    assert await e.embed([]) == []


async def test_an_endpoint_without_embeddings_switches_them_off_and_errors_leak_nothing():
    e = embedder(lambda r: httpx.Response(404, text="no such route sk-secret"))
    assert await e.embed(["x"]) is None and e.available is False
    assert await e.embed(["x"]) is None
    e = embedder(lambda r: httpx.Response(500, text="boom sk-secret"))
    with pytest.raises(EmbeddingError) as err:
        await e.embed(["x"])
    assert "sk-secret" not in str(err.value) and e.available
    e = embedder(lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(EmbeddingError):
        await e.embed(["x"])


async def test_recall_still_answers_from_keywords_when_embedding_fails(pool, uids, mem):
    mem.add_item("Waiting for the plumber")

    class Broken(FakeEmbedder):
        async def embed(self, texts):
            raise EmbeddingError(500)

    assert [h.text for h in await search.recall(pool, uids[0], "plumber", Broken())] == ["Waiting for the plumber"]
