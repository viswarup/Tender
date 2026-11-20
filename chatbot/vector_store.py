"""
Vector Store — uses ChromaDB with nomic-embed-text for semantic search
over extracted document text chunks.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from pipeline.ollama_client import OllamaClient, ollama

logger = logging.getLogger(__name__)

EMBED_MODEL = "nomic-embed-text:latest"
COLLECTION_NAME = "tender_docs"
CHUNK_SIZE = 500  # chars per chunk
CHUNK_OVERLAP = 100


class TenderVectorStore:
    """ChromaDB-backed vector store with Ollama embeddings."""

    def __init__(self, persist_dir: str | Path = "data/chroma_db"):
        self.persist_dir = str(persist_dir)
        self.client_db = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client_db.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.ollama_client = ollama

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Ollama."""
        if not texts:
            return []
        # Batch embed with nomic-embed-text
        # Prefix with "search_document: " for nomic as per its docs
        prefixed = [f"search_document: {t}" for t in texts]
        return self.ollama_client.embed_batch(EMBED_MODEL, prefixed)

    def _embed_query(self, query: str) -> list[float]:
        """Embed a search query."""
        # Prefix with "search_query: " for nomic
        return self.ollama_client.embed(EMBED_MODEL, f"search_query: {query}")

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks."""
        if not text:
            return []
        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start = end - CHUNK_OVERLAP
            if start >= len(text):
                break
        return chunks

    def index_vendor_data(self, data_dir: str | Path):
        """
        Index all vendor extraction data into the vector store.

        Args:
            data_dir: Path to data/ directory with vendors/ subfolder.
        """
        data_dir = Path(data_dir)
        vendors_dir = data_dir / "vendors"
        evals_dir = data_dir / "evaluations"

        if not vendors_dir.exists():
            logger.warning("No vendors directory found for indexing")
            return

        all_ids = []
        all_docs = []
        all_embeds = []
        all_metas = []

        for vendor_file in sorted(vendors_dir.glob("*.json")):
            vendor_name = vendor_file.stem
            logger.info(f"Indexing vendor: {vendor_name}")

            with open(vendor_file) as f:
                vendor_data = json.load(f)

            # Index document texts
            for doc in vendor_data.get("documents", []):
                doc_type = doc.get("document_type", "OTHER")
                for page in doc.get("pages", []):
                    text = page.get("ocr_text") or page.get("text", "")
                    if not text.strip():
                        continue

                    chunks = self._chunk_text(text)
                    for ci, chunk in enumerate(chunks):
                        doc_id = f"{vendor_name}:{doc['filename']}:p{page['page_number']}:c{ci}"
                        all_ids.append(doc_id)
                        all_docs.append(chunk)
                        all_metas.append({
                            "vendor": vendor_name,
                            "filename": doc["filename"],
                            "page": page["page_number"],
                            "document_type": doc_type,
                            "chunk_index": ci,
                        })

            # Index vendor summary
            summary = vendor_data.get("vendor_summary", "")
            if summary:
                all_ids.append(f"{vendor_name}:summary")
                all_docs.append(summary)
                all_metas.append({
                    "vendor": vendor_name,
                    "filename": "summary",
                    "page": 0,
                    "document_type": "SUMMARY",
                })

            # Index evaluation reasoning
            eval_file = evals_dir / f"{vendor_name}.json"
            if eval_file.exists():
                with open(eval_file) as f:
                    eval_data = json.load(f)
                reasoning = eval_data.get("detailed_reasoning", "")
                if reasoning:
                    all_ids.append(f"{vendor_name}:evaluation")
                    all_docs.append(reasoning)
                    all_metas.append({
                        "vendor": vendor_name,
                        "filename": "evaluation",
                        "page": 0,
                        "document_type": "EVALUATION",
                    })

        if not all_docs:
            logger.warning("No documents to index")
            return

        # Batch embed and add
        logger.info(f"Embedding {len(all_docs)} chunks...")
        batch_size = 32
        for i in range(0, len(all_docs), batch_size):
            batch_ids = all_ids[i:i + batch_size]
            batch_docs = all_docs[i:i + batch_size]
            batch_metas = all_metas[i:i + batch_size]

            try:
                batch_embeds = self._embed(batch_docs)
                self.collection.upsert(
                    ids=batch_ids,
                    documents=batch_docs,
                    embeddings=batch_embeds,
                    metadatas=batch_metas,
                )
            except Exception as e:
                logger.error(f"Failed to index batch at {i}: {e}")

        logger.info(f"Indexed {len(all_docs)} chunks from {len(list(vendors_dir.glob('*.json')))} vendors")

    def search(
        self,
        query: str,
        n_results: int = 5,
        vendor_filter: Optional[str] = None,
        doc_type_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Semantic search across all indexed documents.

        Args:
            query: Natural language search query.
            n_results: Number of results to return.
            vendor_filter: Optional vendor name filter.
            doc_type_filter: Optional document type filter.

        Returns:
            List of dicts with 'text', 'vendor', 'filename', 'page', 'score'.
        """
        where_filter = {}
        if vendor_filter:
            where_filter["vendor"] = vendor_filter
        if doc_type_filter:
            where_filter["document_type"] = doc_type_filter

        try:
            query_embed = self._embed_query(query)

            results = self.collection.query(
                query_embeddings=[query_embed],
                n_results=n_results,
                where=where_filter if where_filter else None,
                include=["documents", "metadatas", "distances"],
            )

            output = []
            if results and results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    output.append({
                        "text": doc,
                        "vendor": meta.get("vendor", ""),
                        "filename": meta.get("filename", ""),
                        "page": meta.get("page", 0),
                        "document_type": meta.get("document_type", ""),
                        "score": 1 - distance,  # Convert distance to similarity
                    })

            return output

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    def get_stats(self) -> dict:
        """Get collection statistics."""
        return {
            "total_chunks": self.collection.count(),
            "persist_dir": self.persist_dir,
        }
