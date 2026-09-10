"""Deterministic event clustering and independent-source selection for news evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import re
from typing import Iterable, Sequence, Tuple
from urllib.parse import urlparse


CLUSTER_ALGORITHM_VERSION = "headline_jaccard_v1"
CLUSTER_WINDOW = timedelta(hours=72)
CLUSTER_THRESHOLD = 0.55
SYNDICATION_THRESHOLD = 0.90


@dataclass(frozen=True)
class ClusterMember:
    event_cluster_id: str
    document_id: str
    membership_role: str
    canonical_source_key: str
    similarity_score: float
    selection_reason: str


def cluster_documents(connection, document_ids: Sequence[str], created_at: datetime) -> Tuple[ClusterMember, ...]:
    """Assign eligible documents to persistent clusters using only deterministic text rules."""
    if not document_ids:
        return ()
    rows = _documents(connection, document_ids)
    known = _existing_members(connection, document_ids)
    prototypes = _cluster_prototypes(connection)
    assigned = []
    for document in rows:
        if document["document_id"] in known:
            assigned.append(known[document["document_id"]])
            continue
        match = _best_match(document, prototypes)
        if match is None:
            cluster_id = "evt_" + sha256(document["content_sha256"].encode("utf-8")).hexdigest()[:24]
            cluster_key = sha256((document["normalized_headline"] + document["published_at"].date().isoformat()).encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO news_event_clusters VALUES (?, ?, ?, ?, ?, ?)",
                [cluster_id, cluster_key, CLUSTER_ALGORITHM_VERSION, document["published_at"], document["published_at"], created_at],
            )
            member = ClusterMember(cluster_id, document["document_id"], "REPRESENTATIVE", document["source_key"],
                                   1.0, "first eligible document for deterministic cluster")
            prototypes.append({"cluster_id": cluster_id, "headline": document["normalized_headline"],
                               "published_at": document["published_at"], "source_keys": {document["source_key"]}})
        else:
            cluster_id, similarity, source_keys = match
            if document["source_key"] in source_keys:
                role, reason = "DUPLICATE", "same canonical source already represented in cluster"
            elif similarity >= SYNDICATION_THRESHOLD:
                role, reason = "SYNDICATED", "near-identical headline across different canonical sources"
            else:
                role, reason = "REPRESENTATIVE", "distinct canonical source supports the same event cluster"
                source_keys.add(document["source_key"])
            member = ClusterMember(cluster_id, document["document_id"], role, document["source_key"], similarity, reason)
            connection.execute(
                "UPDATE news_event_clusters SET earliest_published_at = LEAST(earliest_published_at, ?), "
                "latest_published_at = GREATEST(latest_published_at, ?) WHERE event_cluster_id = ?",
                [document["published_at"], document["published_at"], cluster_id],
            )
        connection.execute("INSERT INTO news_event_cluster_members VALUES (?, ?, ?, ?, ?, ?, ?)", [
            member.event_cluster_id, member.document_id, member.membership_role, member.canonical_source_key,
            member.similarity_score, member.selection_reason, created_at,
        ])
        assigned.append(member)
    return tuple(assigned)


def independent_representatives(connection, event_cluster_id: str) -> Tuple[tuple, ...]:
    """Load only independent, evidence-eligible documents for one event hypothesis."""
    return tuple(connection.execute(
        "SELECT d.document_id, d.headline, d.body, d.published_at, d.received_at, d.source_url "
        "FROM news_event_cluster_members m JOIN news_documents d ON d.document_id = m.document_id "
        "WHERE m.event_cluster_id = ? AND m.membership_role = 'REPRESENTATIVE' "
        "AND d.evidence_role = 'EVIDENCE_ELIGIBLE' AND d.source_url IS NOT NULL "
        "ORDER BY d.published_at, d.document_id", [event_cluster_id]
    ).fetchall())


def _documents(connection, document_ids: Sequence[str]):
    placeholders = ",".join("?" for _ in document_ids)
    rows = connection.execute(
        "SELECT document_id, headline, published_at, COALESCE(publisher, ''), "
        "COALESCE(canonical_url, source_url, ''), content_sha256 "
        f"FROM news_documents WHERE document_id IN ({placeholders}) AND evidence_role = 'EVIDENCE_ELIGIBLE' "
        "ORDER BY published_at, document_id", list(document_ids)
    ).fetchall()
    return [{"document_id": doc_id, "normalized_headline": normalize_text(headline), "published_at": published,
             "source_key": canonical_source_key(publisher, url), "content_sha256": content_hash}
            for doc_id, headline, published, publisher, url, content_hash in rows]


def _existing_members(connection, document_ids: Sequence[str]):
    placeholders = ",".join("?" for _ in document_ids)
    rows = connection.execute(
        "SELECT event_cluster_id, document_id, membership_role, canonical_source_key, similarity_score, selection_reason "
        f"FROM news_event_cluster_members WHERE document_id IN ({placeholders})", list(document_ids)
    ).fetchall()
    return {document_id: ClusterMember(cluster_id, document_id, role, source_key, float(similarity), reason)
            for cluster_id, document_id, role, source_key, similarity, reason in rows}


def _cluster_prototypes(connection):
    rows = connection.execute(
        "SELECT c.event_cluster_id, d.headline, c.latest_published_at, m.canonical_source_key "
        "FROM news_event_clusters c JOIN news_event_cluster_members m ON m.event_cluster_id = c.event_cluster_id "
        "JOIN news_documents d ON d.document_id = m.document_id "
        "WHERE m.membership_role = 'REPRESENTATIVE'"
    ).fetchall()
    by_id = {}
    for cluster_id, headline, published_at, source_key in rows:
        prototype = by_id.setdefault(cluster_id, {"cluster_id": cluster_id, "headline": normalize_text(headline),
                                                  "published_at": published_at, "source_keys": set()})
        prototype["source_keys"].add(source_key)
    return list(by_id.values())


def _best_match(document, prototypes):
    matches = []
    for prototype in prototypes:
        if abs(document["published_at"] - prototype["published_at"]) > CLUSTER_WINDOW:
            continue
        similarity = headline_similarity(document["normalized_headline"], prototype["headline"])
        if similarity >= CLUSTER_THRESHOLD:
            matches.append((similarity, prototype["cluster_id"], prototype["source_keys"]))
    if not matches:
        return None
    similarity, cluster_id, source_keys = max(matches, key=lambda item: (item[0], item[1]))
    return cluster_id, similarity, source_keys


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower()))


def headline_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def canonical_source_key(publisher: str, canonical_url: str) -> str:
    if publisher.strip():
        return publisher.strip().lower()
    return (urlparse(canonical_url).hostname or canonical_url).lower()
