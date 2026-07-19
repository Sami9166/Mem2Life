"""볼트 하이브리드(BM25 + 벡터) 인덱스 — 증분 갱신 지원.

`VaultIndex.refresh()`가 핵심이다:

1. 볼트 md 파일들을 스캔하고 mtime으로 1차 필터링(대부분의 "변경 없음"
   케이스는 디스크 read 없이 stat만으로 끝난다)
2. mtime이 바뀐 파일만 내용을 읽어 sha256 해시로 재확인(터치만 되고
   내용은 그대로인 경우까지 걸러낸다)
3. 실제로 변경/신규/삭제된 파일만 다시 파싱·청킹하고, **임베딩도 그
   파일들의 청크만 새로 계산**한다 — 변경 없는 청크는 캐시의 임베딩을
   그대로 재사용해 임베딩 API 비용/지연을 아낀다
4. BM25는 코퍼스 전체에 대해 매번 다시 학습한다(데모 스케일에서는 이
   비용이 무시할 만하다 — `bm25.py` docstring 참고)
5. `cache_path`가 주어지면 (파일 해시 + 청크 + 임베딩)을 JSON으로
   저장해 프로세스를 재시작해도 임베딩을 다시 계산하지 않는다

이 모듈은 vault 파일을 읽기만 한다 — 쓰기는 캐시 파일(`cache_path`)
에만 한다 (recall-dev 담당 원칙: 위키 파일은 읽기 전용).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date as date_type
from pathlib import Path

from ..vault.chunking import chunk_document
from ..vault.loader import iter_vault_md_files, load_document
from ..vault.types import Chunk, ChunkLevel, DocKind, Evidence
from .bm25 import BM25Index
from .embeddings.base import EmbeddingClient
from .embeddings.factory import DEFAULT_PROVIDER, get_embedding_client
from .tokenize import tokenize
from .vector_store import VectorStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshStats:
    """`refresh()` 1회 실행 결과 — 증분 갱신이 실제로 동작하는지 확인용."""

    total_chunks: int
    changed_files: list[str]
    removed_files: list[str]
    unchanged_files: int
    reembedded_chunks: int
    reused_chunks: int
    skipped_files: list[str]


def _chunk_to_dict(chunk: Chunk, embedding: list[float]) -> dict:
    d = asdict(chunk)
    d["doc_path"] = chunk.doc_path.as_posix()
    d["doc_kind"] = chunk.doc_kind.value
    d["level"] = chunk.level.value
    d["date"] = chunk.date.isoformat() if chunk.date else None
    d["embedding"] = embedding
    return d


def _chunk_from_dict(d: dict) -> tuple[Chunk, list[float]]:
    embedding = d.pop("embedding")
    chunk = Chunk(
        chunk_id=d["chunk_id"],
        doc_path=Path(d["doc_path"]),
        doc_kind=DocKind(d["doc_kind"]),
        level=ChunkLevel(d["level"]),
        text=d["text"],
        date=date_type.fromisoformat(d["date"]) if d["date"] else None,
        session_title=d.get("session_title"),
        session_time_range=d.get("session_time_range"),
        start_sec=d.get("start_sec"),
        timestamp_label=d.get("timestamp_label"),
        speaker=d.get("speaker"),
        video_path=d.get("video_path"),
    )
    return chunk, embedding


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bm25_to_unit(score: float) -> float:
    """BM25 원점수(0 이상, 상한 없음)를 [0, 1)로 포화(saturate)시킨다.

    후보 집합의 min-max로 정규화하면 후보가 1개뿐이거나(coarse 단계에서
    흔함) 거의 동률일 때 항상 0으로 뭉개지고, coarse-to-fine 세 단계가
    서로 다른(크기가 다른) 후보 풀에 대해 각자 min-max를 적용하면 단계별
    점수가 서로 비교 불가능해져 `combined_evidence`를 점수순으로 합치는
    로직이 깨진다(작은 후보 풀의 1등이 실제로는 약한 매칭인데도 1.0으로
    부풀려짐). 그래서 후보 풀 크기와 무관한 고정 스케일 변환을 쓴다.
    """
    if score <= 0:
        return 0.0
    return score / (score + 1.0)


def _cosine_to_unit(score: float) -> float:
    """코사인 유사도([-1, 1])를 [0, 1]로 접는다.

    `(score + 1) / 2` 식의 선형 이동은 완전히 무관한 질의(코사인 ≈ 0)조차
    0.5 근방으로 매핑해버려, "관련 없음"이 "어느 정도 관련 있음"처럼
    보이는 인위적인 바닥 점수를 만든다(무관한 질문도 하이브리드 합산
    점수가 자기평가 임계값을 넘는 원인 중 하나였다 — `self_assessment.py`
    참고). 대신 음의 유사도는 전부 0(무관)으로 접고, 양의 유사도만 그대로
    사용한다 — 이러면 최소한 완전히 무관한 질의의 벡터 항이 0에 가까워야
    한다는 요구가 보존된다.
    """
    return max(0.0, min(1.0, score))


class VaultIndex:
    """볼트 하나에 대한 하이브리드 인덱스 (인메모리 + 선택적 디스크 캐시)."""

    def __init__(
        self,
        vault_dir: Path | str,
        *,
        cache_path: Path | str | None = None,
        embedding_provider: str = DEFAULT_PROVIDER,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.vault_dir = Path(vault_dir)
        self.cache_path = Path(cache_path) if cache_path else None
        self.embedding_client = embedding_client or get_embedding_client(embedding_provider)
        self.embedding_provider = embedding_provider

        self.chunks: list[Chunk] = []
        self._embeddings: list[list[float]] = []
        self._bm25: BM25Index | None = None
        self._vector_store: VectorStore | None = None

        # relpath -> {"mtime": float, "hash": str}
        self._file_meta: dict[str, dict[str, float | str]] = {}
        # relpath -> [chunk dict(embedding 포함), ...] — 증분 재사용의 핵심 캐시
        self._chunks_by_file: dict[str, list[dict]] = {}

        if self.cache_path and self.cache_path.exists():
            self._load_cache()

    # ------------------------------------------------------------------
    # 캐시 I/O
    # ------------------------------------------------------------------
    def _load_cache(self) -> None:
        assert self.cache_path is not None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if payload.get("embedding_provider") != self.embedding_provider:
            # provider가 바뀌면 벡터 차원이 달라질 수 있으므로 캐시 전체 무효화
            return
        self._file_meta = payload.get("files", {})
        self._chunks_by_file = payload.get("chunks_by_file", {})

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        payload = {
            "embedding_provider": self.embedding_provider,
            "files": self._file_meta,
            "chunks_by_file": self._chunks_by_file,
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # 증분 갱신
    # ------------------------------------------------------------------
    def refresh(self) -> RefreshStats:
        """볼트를 다시 스캔해 변경분만 재계산하고 인덱스를 갱신한다."""
        current_paths = iter_vault_md_files(self.vault_dir)
        current_relpaths = {p.relative_to(self.vault_dir).as_posix() for p in current_paths}

        changed: list[str] = []
        skipped: list[str] = []
        unchanged_count = 0

        for path in current_paths:
            relpath = path.relative_to(self.vault_dir).as_posix()
            stat_mtime = path.stat().st_mtime
            cached_meta = self._file_meta.get(relpath)

            if cached_meta and cached_meta.get("mtime") == stat_mtime and relpath in self._chunks_by_file:
                unchanged_count += 1
                continue

            try:
                content_hash = _file_hash(path)
            except OSError as exc:
                # 파일을 읽는 것 자체가 실패(권한 문제 등) — 이 파일만 건너뛰고
                # 나머지 볼트는 계속 인덱싱한다(위키 파일 하나의 문제가 전체
                # 회상 파이프라인을 멈추면 안 된다).
                _logger.warning(
                    "볼트 파일을 읽을 수 없어 이번 갱신에서 건너뜁니다: %s (%s)",
                    relpath,
                    exc,
                )
                skipped.append(relpath)
                continue

            if cached_meta and cached_meta.get("hash") == content_hash and relpath in self._chunks_by_file:
                # 내용은 그대로, mtime만 갱신된 경우 (touch 등) — 재파싱 생략
                self._file_meta[relpath] = {"mtime": stat_mtime, "hash": content_hash}
                unchanged_count += 1
                continue

            # 실제 변경 또는 신규 파일 — 재파싱 + 재청킹, 임베딩은 아직 계산 안 함.
            # 파일 하나가 깨져 있어도(인코딩 오류, frontmatter 파싱 실패 등)
            # 나머지 볼트 인덱싱을 막지 않도록 격리한다. 직전까지 캐시에
            # 남아있던 정상 버전이 있다면 그대로 유지하고(스킵), 새 파일이라면
            # 이번 갱신에서는 색인에 포함되지 않는다 — 다음 갱신 때(파일이
            # 고쳐지거나 mtime이 바뀌면) 다시 시도된다.
            try:
                doc = load_document(path, self.vault_dir)
                new_chunks = chunk_document(doc)
            except Exception as exc:  # noqa: BLE001 - 위키 파일 하나의 문제로 전체 갱신이 죽으면 안 된다
                _logger.warning(
                    "볼트 파일 파싱에 실패해 건너뜁니다 (%s: %s): %s",
                    type(exc).__name__,
                    exc,
                    relpath,
                )
                skipped.append(relpath)
                continue

            self._chunks_by_file[relpath] = [_chunk_to_dict(c, embedding=[]) for c in new_chunks]
            for d in self._chunks_by_file[relpath]:
                d["_needs_embedding"] = True
            self._file_meta[relpath] = {"mtime": stat_mtime, "hash": content_hash}
            changed.append(relpath)

        removed = [rp for rp in list(self._chunks_by_file) if rp not in current_relpaths]
        for rp in removed:
            del self._chunks_by_file[rp]
            self._file_meta.pop(rp, None)

        # 임베딩이 필요한 청크만 모아 배치로 계산
        pending_dicts: list[dict] = []
        for rp in changed:
            pending_dicts.extend(self._chunks_by_file[rp])
        pending_dicts = [d for d in pending_dicts if d.get("_needs_embedding")]

        reused = sum(
            1 for chunks in self._chunks_by_file.values() for d in chunks if not d.get("_needs_embedding")
        )

        if pending_dicts:
            texts = [d["text"] for d in pending_dicts]
            vectors = self.embedding_client.embed(texts)
            for d, vec in zip(pending_dicts, vectors):
                d["embedding"] = vec
                d["_needs_embedding"] = False

        # 평탄화 + BM25/벡터 스토어 재구성
        all_dicts: list[dict] = []
        for rp in sorted(self._chunks_by_file):
            all_dicts.extend(self._chunks_by_file[rp])

        chunks: list[Chunk] = []
        embeddings: list[list[float]] = []
        for d in all_dicts:
            d_copy = dict(d)
            d_copy.pop("_needs_embedding", None)
            chunk, embedding = _chunk_from_dict(d_copy)
            chunks.append(chunk)
            embeddings.append(embedding)

        self.chunks = chunks
        self._embeddings = embeddings
        self._bm25 = BM25Index()
        self._bm25.fit([tokenize(c.text) for c in chunks])
        self._vector_store = VectorStore(vectors=embeddings)

        self._save_cache()

        return RefreshStats(
            total_chunks=len(chunks),
            changed_files=changed,
            removed_files=removed,
            unchanged_files=unchanged_count,
            reembedded_chunks=len(pending_dicts),
            reused_chunks=reused,
            skipped_files=skipped,
        )

    # ------------------------------------------------------------------
    # 검색
    # ------------------------------------------------------------------
    def _ensure_built(self) -> None:
        if self._bm25 is None or self._vector_store is None:
            self.refresh()

    def bm25_scores(self, query: str) -> list[float]:
        self._ensure_built()
        assert self._bm25 is not None
        return self._bm25.score(tokenize(query))

    def vector_scores(self, query: str) -> list[float]:
        self._ensure_built()
        assert self._vector_store is not None
        query_vec = self.embedding_client.embed([query])[0]
        return self._vector_store.score(query_vec)

    def search(
        self,
        query: str,
        *,
        indices: Sequence[int] | None = None,
        top_k: int = 5,
        bm25_weight: float = 0.5,
        vector_weight: float = 0.5,
    ) -> list[Evidence]:
        """하이브리드(BM25 + 벡터) 검색. `indices`로 후보 청크를 좁힐 수 있다
        (coarse-to-fine 단계별 필터링에 사용, 예: 날짜/세션/레벨 제한)."""
        self._ensure_built()
        candidate_idx = list(indices) if indices is not None else list(range(len(self.chunks)))
        if not candidate_idx:
            return []

        bm25_all = self.bm25_scores(query)
        vector_all = self.vector_scores(query)

        bm25_candidates = [bm25_all[i] for i in candidate_idx]
        vector_candidates = [vector_all[i] for i in candidate_idx]
        norm_bm25 = [_bm25_to_unit(s) for s in bm25_candidates]
        norm_vector = [_cosine_to_unit(s) for s in vector_candidates]

        evidences: list[Evidence] = []
        for pos, chunk_idx in enumerate(candidate_idx):
            combined = bm25_weight * norm_bm25[pos] + vector_weight * norm_vector[pos]
            if bm25_candidates[pos] <= 0.0:
                # 키워드 겹침이 원점수 기준 전혀 없는 청크는 벡터 항만으로
                # "근거"로 인정하지 않는다. 지금 쓰는 벡터 임베딩은 실제
                # 의미(semantic) 임베딩이 아니라 토큰 해시 스텁(hash_stub.py)
                # 이라 무관한 텍스트끼리도 해시 충돌만으로 코사인 유사도가
                # 양수로 나올 수 있다 — 이 항을 그대로 신뢰하면 질문과 전혀
                # 무관한 볼트 내용을 근거인 것처럼 답해버린다("답을 지어내지
                # 않는다" 원칙 위반, self_assessment.py의 신뢰도 판정을
                # 무력화하는 원인이었다). 실제 의미 임베딩으로 교체되면 이
                # 규칙은 재검토가 필요하다.
                combined = 0.0
            evidences.append(
                Evidence(
                    chunk=self.chunks[chunk_idx],
                    score=combined,
                    bm25_score=bm25_candidates[pos],
                    vector_score=vector_candidates[pos],
                )
            )
        evidences.sort(key=lambda e: e.score, reverse=True)
        return evidences[:top_k]

    def indices_for(
        self,
        *,
        levels: set[ChunkLevel] | None = None,
        dates: set[date_type] | None = None,
        doc_paths: set[Path] | None = None,
    ) -> list[int]:
        """레벨/날짜/문서 경로 조건에 맞는 청크 인덱스 목록 (coarse-to-fine 필터링용)."""
        self._ensure_built()
        result = []
        for idx, chunk in enumerate(self.chunks):
            if levels is not None and chunk.level not in levels:
                continue
            if dates is not None and chunk.date not in dates:
                continue
            if doc_paths is not None and chunk.doc_path not in doc_paths:
                continue
            result.append(idx)
        return result


def build_index(
    vault_dir: Path | str,
    *,
    cache_path: Path | str | None = None,
    embedding_provider: str = DEFAULT_PROVIDER,
) -> VaultIndex:
    """볼트를 스캔·인덱싱한 `VaultIndex`를 한 번에 만든다 (생성 + `refresh()`)."""
    index = VaultIndex(vault_dir, cache_path=cache_path, embedding_provider=embedding_provider)
    index.refresh()
    return index
