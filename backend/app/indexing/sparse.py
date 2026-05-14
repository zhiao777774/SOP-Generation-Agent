from typing import Iterable, List, Optional, Sequence, Tuple

from backend.app.indexing.tokenizer import SparseTokenizer, TokenizationRecord


class BM25Index:
    def __init__(
        self,
        documents: Sequence[str],
        tokenizer: Optional[SparseTokenizer] = None,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        self.documents = list(documents)
        self.tokenizer = tokenizer or SparseTokenizer()
        self.records: List[TokenizationRecord] = [
            self.tokenizer.tokenize_with_record(document) for document in self.documents
        ]
        self.doc_tokens = [record.final_tokens for record in self.records]
        self._ranked_cache = {}
        try:
            import bm25s
        except ImportError as exc:
            raise RuntimeError("bm25s is required for sparse retrieval.") from exc
        self._retriever = bm25s.BM25(k1=k1, b=b, corpus=list(range(len(self.documents))))
        if self.documents:
            self._retriever.index(self.doc_tokens)

    def score(self, query: str, index: int) -> float:
        if not self.documents:
            return 0.0
        ranked_lookup = dict(self.ranked(query))
        return ranked_lookup.get(index, 0.0)

    def ranked(self, query: str) -> List[Tuple[int, float]]:
        if query in self._ranked_cache:
            return self._ranked_cache[query]
        if not self.documents:
            return []
        query_tokens = self.tokenizer.tokenize(query)
        results, scores = self._retriever.retrieve([query_tokens], k=len(self.documents))
        ranked = [
            (int(results[0, rank]), float(scores[0, rank]))
            for rank in range(results.shape[1])
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        self._ranked_cache[query] = ranked
        return ranked

    def tokenization_report(self, label: str) -> dict:
        return {
            "label": label,
            "tokenizer": self.tokenizer.name,
            "dictionary_term_count": self.tokenizer.dictionary_term_count,
            "temporary_term_count": self.tokenizer.temporary_term_count,
            "domain_token_extraction": self.tokenizer.config.domain_token_extraction,
            "document_count": len(self.documents),
            "documents": [
                {
                    "document_index": index,
                    "text_preview": record.text_preview,
                    "tokenizer": record.tokenizer,
                    "script": record.script,
                    "script_ratio": record.script_ratio,
                    "cjk_tokens": record.cjk_tokens[:200],
                    "normalized_tokens": record.normalized_tokens[:120],
                    "permanent_dictionary_hits": record.permanent_dictionary_hits,
                    "temporary_dictionary_hits": record.temporary_dictionary_hits,
                    "preserved_domain_tokens": record.preserved_domain_tokens,
                    "final_tokens": record.final_tokens[:240],
                    "final_token_count": len(record.final_tokens),
                }
                for index, record in enumerate(self.records)
            ],
        }


def reciprocal_rank_fusion(rankings: Iterable[Sequence[int]], k: int = 60) -> List[Tuple[int, float]]:
    scores = {}
    for ranking in rankings:
        for rank, item_index in enumerate(ranking, start=1):
            scores[item_index] = scores.get(item_index, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
