"""hybrid_search.py — search_nodes (RRF fusion) and find_similar (vector-only dedup).
Real fastembed model throughout; every fixture's expected ranking below was verified
empirically against the real model before being locked into an assertion (bge-small-en-v1.5
embeddings are deterministic ONNX inference, so these reproduce across runs/machines)."""

from __future__ import annotations

from conftest import insert_node
from redraft.retrieval.fts import fts_candidates
from redraft.retrieval.hybrid_search import find_similar, search_nodes, vector_candidates
from redraft.retrieval.vector_index import embed_upsert


def _seed(conn, config, nodes: dict[str, tuple[str, str]]) -> None:
    for nid, (title, body) in nodes.items():
        insert_node(conn, nid, "concept", title=title, body=body)
    conn.commit()
    for nid, (title, body) in nodes.items():
        embed_upsert(conn, config, nid, "concept", title, body)


def test_search_nodes_finds_exact_identifier_via_fts(vec_ready_conn, retrieval_config):
    _seed(
        vec_ready_conn,
        retrieval_config,
        {
            "Backbone Choice": (
                "Backbone choice",
                "We picked sm_120 as the minimum compute capability target for the registration backbone.",
            ),
            "Cooking Notes": ("Cooking notes", "Simmer the sauce for twenty minutes then add fresh basil."),
            "Travel Plan": ("Travel plan", "We are flying to Tokyo next month for the robotics conference."),
        },
    )
    hits = search_nodes(vec_ready_conn, retrieval_config, "sm_120", k=5)
    assert hits[0].node["id"] == "Backbone Choice"
    assert hits[0].matched_fts is True


def test_find_similar_surfaces_paraphrase_by_id(vec_ready_conn, retrieval_config):
    _seed(
        vec_ready_conn,
        retrieval_config,
        {
            "GPU Requirement": (
                "GPU requirement",
                "The project requires an NVIDIA GPU with compute capability sm_120 to support Blackwell.",
            ),
            "Compute Capability Note": (
                "Compute capability note",
                "We need a graphics card supporting the sm_120 architecture generation for our CUDA kernels.",
            ),
            "Unrelated Topic": ("Unrelated topic", "The weather today is sunny with a light breeze."),
        },
    )
    hits = find_similar(vec_ready_conn, retrieval_config, "GPU Requirement", k=2)
    assert [h.node["id"] for h in hits] != []
    assert hits[0].node["id"] == "Compute Capability Note"
    assert all(h.node["id"] != "GPU Requirement" for h in hits)  # exact-id match excludes self
    assert all(h.matched_vector and not h.matched_fts for h in hits)  # vector-only per design


def test_find_similar_surfaces_paraphrase_by_free_text(vec_ready_conn, retrieval_config):
    _seed(
        vec_ready_conn,
        retrieval_config,
        {
            "GPU Requirement": (
                "GPU requirement",
                "The project requires an NVIDIA GPU with compute capability sm_120 to support Blackwell.",
            ),
            "Compute Capability Note": (
                "Compute capability note",
                "We need a graphics card supporting the sm_120 architecture generation for our CUDA kernels.",
            ),
            "Unrelated Topic": ("Unrelated topic", "The weather today is sunny with a light breeze."),
        },
    )
    hits = find_similar(
        vec_ready_conn, retrieval_config, "what graphics card supports our required compute capability", k=2
    )
    ids = [h.node["id"] for h in hits]
    assert set(ids) == {"Compute Capability Note", "GPU Requirement"}
    assert "Unrelated Topic" not in ids


def test_rrf_beats_either_branch_alone_on_mixed_query(vec_ready_conn, retrieval_config):
    """The target node is a moderate-but-not-top match on *each* branch (present, ranked
    #2, in both FTS and vector candidate lists); one distractor wins FTS outright (heavy
    repetition of the rare literal identifier "E4") and the other wins vector similarity
    outright (a fluent paraphrase of the underlying problem with zero literal term
    overlap) -- each distractor barely registering, or ranking near-last, on the *other*
    axis. Reciprocal Rank Fusion sums support from both lists, so the balanced node beats
    either single-axis extreme even though it is the top-1 result of *neither* branch
    alone -- this is the property under test, not just "hybrid finds a good answer"."""
    conn, config = vec_ready_conn, retrieval_config
    nodes = {
        "Target": ("Reported code", "The device reported code E4 during startup."),
        "FTS Distractor": ("Crate stamp", "E4 E4 E4 crate routing stamp number."),
        "Vector Distractor": (
            "Thermostat suspicion",
            "The appliance fails to bring the liquid up to temperature during the brew cycle, "
            "likely a faulty thermostat.",
        ),
        "Filler 1": (
            "General complaint",
            "The customer reported general dissatisfaction with the product's performance over the weekend.",
        ),
        "Filler 2": ("Escalation note", "Support staff escalated the ticket to a senior technician for further diagnosis."),
        "Totally Unrelated": ("Sales report", "The quarterly sales report is due next Friday."),
    }
    _seed(conn, config, nodes)
    query = "E4 error coffee machine won't heat water"

    fts_only = fts_candidates(conn, query, 10)
    vec_only = vector_candidates(conn, config, query, 10)
    fused = search_nodes(conn, config, query, k=6)

    # Precondition: each distractor really does win its own axis outright, and Target
    # tops neither branch alone -- otherwise this test would not be exercising fusion.
    assert fts_only[0] == "FTS Distractor"
    assert vec_only[0] == "Vector Distractor"
    assert fts_only[0] != "Target" and vec_only[0] != "Target"

    assert fused[0].node["id"] == "Target"
    assert fused[0].matched_fts is True
    assert fused[0].matched_vector is True


def test_search_nodes_filters_before_ranking_by_type(vec_ready_conn, retrieval_config):
    """A node that would otherwise rank first is excluded by a type filter *before* RRF
    rank position is assigned -- it must not consume the fused rank-1 weight and must not
    appear in the results at all."""
    conn, config = vec_ready_conn, retrieval_config
    insert_node(conn, "Wrong Type Match", "observation", title="Wrong type match",
                body="sm_120 compute capability requirement note.")
    insert_node(conn, "Right Type Match", "decision", title="Right type match", status="accepted",
                body="sm_120 compute capability requirement note, decision record.")
    conn.commit()
    embed_upsert(conn, config, "Wrong Type Match", "observation", "Wrong type match",
                 "sm_120 compute capability requirement note.")
    embed_upsert(conn, config, "Right Type Match", "decision", "Right type match",
                 "sm_120 compute capability requirement note, decision record.")

    unfiltered_ids = {h.node["id"] for h in search_nodes(conn, config, "sm_120", k=5)}
    assert unfiltered_ids == {"Wrong Type Match", "Right Type Match"}  # both are legitimate candidates

    filtered_ids = [h.node["id"] for h in search_nodes(conn, config, "sm_120", types=["decision"], k=5)]
    assert filtered_ids == ["Right Type Match"]


def test_search_nodes_empty_query_returns_empty(vec_ready_conn, retrieval_config):
    _seed(vec_ready_conn, retrieval_config, {"A": ("A", "alpha content")})
    assert search_nodes(vec_ready_conn, retrieval_config, "", k=5) == []


def test_search_nodes_non_positive_k_returns_empty(vec_ready_conn, retrieval_config):
    """Regression guard: n_pool = max(k*5, config.fts_candidate_pool) is floored at the
    pool size, so a negative k does NOT starve the candidate lists the way it does in
    knn()/fts_candidates() -- without an explicit guard, the final `sorted(...)[:k]` slices
    with a *negative* k (Python's [:-2] means "drop the last 2", not "keep 0"), silently
    returning a wrong, non-empty result instead of []."""
    _seed(vec_ready_conn, retrieval_config, {f"N{i}": (f"N{i}", "shared keyword alpha") for i in range(6)})
    assert search_nodes(vec_ready_conn, retrieval_config, "alpha", k=0) == []
    assert search_nodes(vec_ready_conn, retrieval_config, "alpha", k=-2) == []


def test_find_similar_non_positive_k_returns_empty(vec_ready_conn, retrieval_config):
    _seed(vec_ready_conn, retrieval_config, {f"N{i}": (f"N{i}", "shared keyword alpha") for i in range(6)})
    assert find_similar(vec_ready_conn, retrieval_config, "N0", k=0) == []
    assert find_similar(vec_ready_conn, retrieval_config, "N0", k=-2) == []
    assert find_similar(vec_ready_conn, retrieval_config, "alpha free text", k=-2) == []
