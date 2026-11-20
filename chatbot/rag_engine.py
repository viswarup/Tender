"""
RAG Engine — combines Knowledge Graph and Vector Store to answer
evaluator questions with source citations.

Includes a query router that classifies questions as:
- STRUCTURED → graph query
- SEMANTIC → vector search
- HYBRID → both
"""

import json
import logging
from pathlib import Path
from typing import Optional

from chatbot.knowledge_graph import TenderKnowledgeGraph
from chatbot.vector_store import TenderVectorStore
from pipeline.ollama_client import OllamaClient, ollama

logger = logging.getLogger(__name__)

# Base workspace path resolver (C4/S3)
WORKSPACE_ROOT = Path(__file__).parent.parent.resolve()

CHATBOT_MODEL = "gpt-oss:20b"
ROUTER_MODEL = "gpt-oss:20b"

# Singleton instances
_kg: Optional[TenderKnowledgeGraph] = None
_vs: Optional[TenderVectorStore] = None
_cached_config = None


def _get_config() -> dict:
    global _cached_config
    if _cached_config is None:
        config_path = WORKSPACE_ROOT / "config" / "tender_config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    _cached_config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config in RAG engine: {e}")
                _cached_config = {}
        else:
            _cached_config = {}
    return _cached_config


def _get_kg() -> TenderKnowledgeGraph:
    global _kg
    if _kg is None:
        _kg = TenderKnowledgeGraph()
        data_dir = WORKSPACE_ROOT / "data"
        config_path = WORKSPACE_ROOT / "config" / "tender_config.json"
        if data_dir.exists() and config_path.exists():
            _kg.build_from_data_dir(data_dir, config_path)
    return _kg


def _get_vs() -> TenderVectorStore:
    global _vs
    if _vs is None:
        _vs = TenderVectorStore(persist_dir=str(WORKSPACE_ROOT / "data" / "chroma_db"))
    return _vs


# ── Query Router ──

ROUTER_PROMPT = """Classify this question about a tender evaluation into one category:

STRUCTURED — Questions about specific vendors, statuses, counts, comparisons, or relationships.
  Examples: "What is the PQC status of Arms Peripherals?", "Which vendors are accepted?", "How many POs does Heamons have?"

SEMANTIC — Questions requiring fuzzy text search across documents.
  Examples: "Find documents about defense supply", "What experience does Ravechi have with government contracts?"

HYBRID — Questions needing both structured data and document context.
  Examples: "Compare Fountain Stationers and Heamons experience", "Why was iCare rejected and what documents did they submit?"

Question: "{question}"

Respond with JSON only: {{"query_type": "STRUCTURED" or "SEMANTIC" or "HYBRID", "reasoning": "brief explanation"}}"""


def route_query(question: str, client: Optional[OllamaClient] = None) -> str:
    """Classify a question as STRUCTURED, SEMANTIC, or HYBRID using the unified LLM router."""
    config = _get_config()
    router_model = config.get("models", {}).get("chatbot", "gpt-oss:20b")

    from pipeline.llm_router import query_llm_json
    try:
        result = query_llm_json(ROUTER_PROMPT.format(question=question), local_model=router_model)
        return result.get("query_type", "HYBRID").upper()
    except Exception as e:
        logger.error(f"Error in LLM query routing: {e}")
        return "HYBRID"


# ── Context Assembly ──

def get_structured_context(question: str) -> tuple[str, list[str]]:
    """
    Get context from the knowledge graph for structured queries.

    Returns:
        Tuple of (context_text, source_list)
    """
    kg = _get_kg()
    q_lower = question.lower()
    context_parts = []
    sources = []

    # Vendor status queries
    if any(s in q_lower for s in ["accepted", "status", "passed"]):
        accepted = kg.get_vendors_by_status("ACCEPTED")
        context_parts.append(f"Accepted vendors ({len(accepted)}):")
        for v in accepted:
            context_parts.append(f"  - {v['name']}: {v.get('criteria_met', 'N/A')}")
        sources.append("knowledge_graph:accepted_vendors")

    if any(s in q_lower for s in ["rejected", "failed"]):
        rejected = kg.get_vendors_by_status("REJECTED")
        context_parts.append(f"Rejected vendors ({len(rejected)}):")
        for v in rejected:
            context_parts.append(f"  - {v['name']}: {v.get('recommendation', 'N/A')}")
        sources.append("knowledge_graph:rejected_vendors")

    if any(s in q_lower for s in ["queried", "pending", "query"]):
        queried = kg.get_vendors_by_status("QUERIED")
        context_parts.append(f"Queried vendors ({len(queried)}):")
        for v in queried:
            context_parts.append(f"  - {v['name']}: {v.get('recommendation', 'N/A')}")
        sources.append("knowledge_graph:queried_vendors")

    # Missing execution proof
    if "missing" in q_lower and "proof" in q_lower:
        missing = kg.get_vendors_missing_proof()
        context_parts.append(f"Vendors missing execution proof ({len(missing)}):")
        for m in missing:
            context_parts.append(
                f"  - {m['vendor']} ({m['status']}): {m['missing_proof_count']} POs without proof"
            )
        sources.append("knowledge_graph:missing_proof")

    # Specific vendor queries
    for vendor in kg.get_all_vendors():
        vname = vendor.get("name", "")
        if vname.lower() in q_lower:
            context_parts.append(f"\nVendor: {vname}")
            context_parts.append(f"  Status: {vendor.get('status', 'UNKNOWN')}")
            context_parts.append(f"  Criteria: {vendor.get('criteria_met', 'None')}")
            context_parts.append(f"  Recommendation: {vendor.get('recommendation', 'N/A')}")
            context_parts.append(f"  Documents: {vendor.get('doc_count', 0)}")
            context_parts.append(f"  POs: {vendor.get('po_count', 0)}")

            # PO details
            pos = kg.get_vendor_pos(vname)
            if pos:
                context_parts.append(f"  Purchase Orders:")
                for po in pos:
                    context_parts.append(
                        f"    - {po.get('po_number')}: ₹{po.get('po_value', 0):,.0f} "
                        f"from {po.get('issuing_org')} [{po.get('execution_proof')}]"
                    )

            # MAFs
            mafs = kg.get_vendor_mafs(vname)
            if mafs:
                context_parts.append(f"  OEM Authorizations:")
                for maf in mafs:
                    context_parts.append(f"    - {maf.get('oem_name')}")

            sources.append(f"knowledge_graph:vendor:{vname}")

    # OEM queries
    for oem in ["HP", "Kyocera", "hp", "kyocera"]:
        if oem.lower() in q_lower and ("authorization" in q_lower or "maf" in q_lower):
            vendors_with_oem = kg.get_vendors_with_oem(oem)
            if vendors_with_oem:
                context_parts.append(f"Vendors with {oem} authorization: {', '.join(vendors_with_oem)}")
                sources.append(f"knowledge_graph:oem:{oem}")

    # PO value queries
    import re
    value_match = re.search(r'above\s*(?:₹|rs\.?|inr)?\s*([\d,]+)', q_lower)
    if value_match:
        threshold = float(value_match.group(1).replace(',', ''))
        pos = kg.get_pos_above_value(threshold)
        context_parts.append(f"POs with value above ₹{threshold:,.0f} ({len(pos)}):")
        for po in pos[:10]:
            context_parts.append(
                f"  - {po['vendor']}: PO {po['po_number']} = ₹{po['po_value']:,.0f} from {po['issuing_org']}"
            )
        sources.append(f"knowledge_graph:po_value_above:{threshold}")

    # Fallback: provide all vendor statuses
    if not context_parts:
        all_vendors = kg.get_all_vendors()
        context_parts.append(f"All vendors ({len(all_vendors)}):")
        for v in all_vendors:
            context_parts.append(f"  - {v['name']}: {v.get('status', 'UNKNOWN')}")
        sources.append("knowledge_graph:all_vendors")

    return "\n".join(context_parts), sources


def get_semantic_context(question: str) -> tuple[str, list[str]]:
    """
    Get context from the vector store for semantic queries.

    Returns:
        Tuple of (context_text, source_list)
    """
    vs = _get_vs()
    results = vs.search(question, n_results=5)

    if not results:
        return "No relevant documents found.", []

    context_parts = []
    sources = []

    for r in results:
        context_parts.append(
            f"[{r['vendor']} / {r['filename']} / Page {r['page']}] "
            f"(score: {r['score']:.2f})\n{r['text']}"
        )
        sources.append(f"{r['vendor']}:{r['filename']}:p{r['page']}")

    return "\n\n".join(context_parts), sources


# ── Answer Generation ──

ANSWER_PROMPT = """You are an AI assistant for a tender evaluation system.
You help evaluators understand vendor bids and PQC (Pre-Qualification Criteria) evaluation results.

Based on the following context and conversation history, answer the user's question accurately and concisely.
Always cite your sources when referring to specific data points.
If the context doesn't contain enough information, say so clearly.

CONTEXT:
{context}

{history}USER: {question}
ASSISTANT:"""


def answer_question(
    question: str,
    history: list[dict] = None,
    client: Optional[OllamaClient] = None,
) -> dict:
    """
    Answer a question using the RAG pipeline.

    Args:
        question: User's question.
        history: Chat history for context.
        client: OllamaClient instance.

    Returns:
        Dict with 'answer' and 'sources'.
    """
    client = client or ollama

    # Route the query
    query_type = route_query(question, client)
    logger.info(f"Query routed as: {query_type}")

    # Gather context
    all_context = []
    all_sources = []

    if query_type in ("STRUCTURED", "HYBRID"):
        ctx, srcs = get_structured_context(question)
        if ctx:
            all_context.append(f"[STRUCTURED DATA]\n{ctx}")
            all_sources.extend(srcs)

    if query_type in ("SEMANTIC", "HYBRID"):
        ctx, srcs = get_semantic_context(question)
        if ctx:
            all_context.append(f"[DOCUMENT SEARCH]\n{ctx}")
            all_sources.extend(srcs)

    context = "\n\n".join(all_context) if all_context else "No relevant context found."

    config = _get_config()
    chatbot_model = config.get("models", {}).get("chatbot", "gpt-oss:20b")

    # Format history if present
    history_text = ""
    if history:
        history_parts = []
        for msg in history:
            role = "USER" if msg.get("role") == "user" else "ASSISTANT"
            content = msg.get("content", "")
            history_parts.append(f"{role}: {content}")
        history_text = "CONVERSATION HISTORY:\n" + "\n".join(history_parts) + "\n\n"

    # Generate answer
    prompt = ANSWER_PROMPT.format(context=context, history=history_text, question=question)

    from pipeline.llm_router import query_llm_text
    try:
        answer = query_llm_text(prompt, local_model=chatbot_model)

        return {
            "answer": answer.strip(),
            "sources": list(set(all_sources)),
            "query_type": query_type,
        }

    except Exception as e:
        logger.error(f"Answer generation failed: {e}")
        return {
            "answer": f"Error generating answer: {str(e)}",
            "sources": [],
            "query_type": query_type,
        }


def initialize_rag(data_dir: str = None, config_path: str = None):
    """
    Initialize the RAG system: build knowledge graph and index vector store.
    Call this after the pipeline has been run.
    """
    global _kg, _vs

    if data_dir is None:
        data_dir = str(WORKSPACE_ROOT / "data")
    if config_path is None:
        config_path = str(WORKSPACE_ROOT / "config" / "tender_config.json")

    logger.info("Initializing RAG system...")

    # Build knowledge graph
    _kg = TenderKnowledgeGraph()
    _kg.build_from_data_dir(data_dir, config_path)
    _kg.serialize(Path(data_dir) / "graph.json")
    logger.info(f"Knowledge graph: {_kg.get_graph_summary()}")

    # Index vector store
    _vs = TenderVectorStore(persist_dir=f"{data_dir}/chroma_db")
    _vs.index_vendor_data(data_dir)
    stats = _vs.get_stats()
    logger.info(f"Vector store: {stats['total_chunks']} chunks indexed")

    return {"graph_summary": _kg.get_graph_summary(), "vector_stats": stats}
