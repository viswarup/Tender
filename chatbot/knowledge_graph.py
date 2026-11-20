"""
Knowledge Graph — builds a NetworkX graph from extracted vendor data
for structured relational queries.

Nodes: Tender, Vendor, Document, PurchaseOrder, OEM, Organization
Edges: HAS_BIDDER, SUBMITTED_DOC, HAS_PO, ISSUED_BY, AUTHORIZED_BY, etc.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)


class TenderKnowledgeGraph:
    """Builds and queries a knowledge graph from pipeline outputs."""

    def __init__(self):
        self.G = nx.DiGraph()

    def build_from_data_dir(self, data_dir: str | Path, config_path: str | Path):
        """
        Build the graph from the pipeline output directory.

        Args:
            data_dir: Path to the data/ directory containing vendors/ and evaluations/
            config_path: Path to tender_config.json
        """
        data_dir = Path(data_dir)
        config_path = Path(config_path)

        # Load config
        with open(config_path) as f:
            config = json.load(f)

        # Add Tender node
        tender_id = config.get("bid_no", "tender")
        self.G.add_node(tender_id, type="Tender", **{
            "tender_no": config.get("tender_no"),
            "bid_no": config.get("bid_no"),
            "subject": config.get("subject"),
            "organization": config.get("organization"),
            "bid_end_date": config.get("bid_end_date"),
        })

        # Load vendor and evaluation data
        vendors_dir = data_dir / "vendors"
        evals_dir = data_dir / "evaluations"

        if not vendors_dir.exists():
            logger.warning("No vendors directory found")
            return

        for vendor_file in sorted(vendors_dir.glob("*.json")):
            vendor_name = vendor_file.stem
            try:
                with open(vendor_file) as f:
                    vendor_data = json.load(f)

                eval_data = None
                eval_file = evals_dir / f"{vendor_name}.json"
                if eval_file.exists():
                    with open(eval_file) as f:
                        eval_data = json.load(f)

                self._add_vendor(tender_id, vendor_data, eval_data)
            except Exception as e:
                logger.error(f"Error loading vendor {vendor_name}: {e}")

        logger.info(
            f"Knowledge graph built: {self.G.number_of_nodes()} nodes, "
            f"{self.G.number_of_edges()} edges"
        )

    def _add_vendor(self, tender_id: str, vendor_data: dict, eval_data: Optional[dict]):
        """Add a vendor and all its related entities to the graph."""
        vendor_name = vendor_data.get("vendor_name", "Unknown")
        vendor_id = f"vendor:{vendor_name}"

        # Vendor node
        self.G.add_node(vendor_id, type="Vendor", **{
            "name": vendor_name,
            "status": eval_data.get("status") if eval_data else "UNKNOWN",
            "criteria_met": eval_data.get("criteria_met") if eval_data else None,
            "recommendation": eval_data.get("recommendation") if eval_data else None,
            "mse_exemption": eval_data.get("mse_exemption_applicable", False) if eval_data else False,
            "po_count": len(vendor_data.get("purchase_orders", [])),
            "doc_count": len(vendor_data.get("documents", [])),
        })
        self.G.add_edge(tender_id, vendor_id, relation="HAS_BIDDER")

        # Documents
        for doc in vendor_data.get("documents", []):
            doc_id = f"doc:{vendor_name}:{doc['filename']}"
            self.G.add_node(doc_id, type="Document", **{
                "filename": doc["filename"],
                "document_type": doc.get("document_type"),
                "num_pages": doc.get("num_pages"),
                "file_size_bytes": doc.get("file_size_bytes"),
                "classification_confidence": doc.get("classification_confidence"),
            })
            self.G.add_edge(vendor_id, doc_id, relation="SUBMITTED_DOC")

        # Purchase Orders
        for po in vendor_data.get("purchase_orders", []):
            po_num = po.get("po_number", {}).get("value", "Unknown")
            po_id = f"po:{vendor_name}:{po_num}"
            po_value = po.get("po_value_numeric", 0) or 0

            self.G.add_node(po_id, type="PurchaseOrder", **{
                "po_number": po_num,
                "po_date": po.get("po_date", {}).get("value", ""),
                "po_value": po_value,
                "issuing_org": po.get("issuing_organization", {}).get("value", ""),
                "items": po.get("items_description", {}).get("value", "") if po.get("items_description") else "",
                "execution_proof": po.get("execution_proof_status", "NOT_SUBMITTED"),
                "meets_similar_item": po.get("meets_similar_item", "NO"),
            })
            self.G.add_edge(vendor_id, po_id, relation="HAS_PO")

            # Issuing Organization node
            org_name = po.get("issuing_organization", {}).get("value", "")
            if org_name:
                org_id = f"org:{org_name}"
                if not self.G.has_node(org_id):
                    self.G.add_node(org_id, type="Organization", name=org_name)
                self.G.add_edge(po_id, org_id, relation="ISSUED_BY")

        # MAF / OEM Authorizations
        for maf in vendor_data.get("maf_authorizations", []):
            oem_name = maf.get("oem_name", {}).get("value", "Unknown")
            oem_id = f"oem:{oem_name}"
            if not self.G.has_node(oem_id):
                self.G.add_node(oem_id, type="OEM", name=oem_name)

            self.G.add_edge(vendor_id, oem_id, relation="AUTHORIZED_BY")
            maf_id = f"maf:{vendor_name}:{oem_name}"
            self.G.add_node(maf_id, type="MAF", **{
                "oem_name": oem_name,
                "product_category": maf.get("product_category", {}).get("value", "") if maf.get("product_category") else "",
                "validity": maf.get("validity_period", {}).get("value", "") if maf.get("validity_period") else "",
            })
            self.G.add_edge(vendor_id, maf_id, relation="HAS_MAF")

        # Udyam
        for udyam in vendor_data.get("udyam_registrations", []):
            udyam_num = udyam.get("udyam_number", {}).get("value", "Unknown")
            udyam_id = f"udyam:{vendor_name}:{udyam_num}"
            self.G.add_node(udyam_id, type="Udyam", **{
                "udyam_number": udyam_num,
                "enterprise_type": udyam.get("enterprise_type", {}).get("value", "") if udyam.get("enterprise_type") else "",
            })
            self.G.add_edge(vendor_id, udyam_id, relation="HAS_UDYAM")

    # ── Query Methods ──

    def get_all_vendors(self) -> list[dict]:
        """Get all vendor nodes with their attributes."""
        return [
            {"id": n, **self.G.nodes[n]}
            for n in self.G.nodes
            if self.G.nodes[n].get("type") == "Vendor"
        ]

    def get_vendors_by_status(self, status: str) -> list[dict]:
        """Get vendors filtered by PQC status."""
        return [
            v for v in self.get_all_vendors()
            if v.get("status", "").upper() == status.upper()
        ]

    def get_vendor_pos(self, vendor_name: str) -> list[dict]:
        """Get all POs for a vendor."""
        vendor_id = f"vendor:{vendor_name}"
        if not self.G.has_node(vendor_id):
            return []

        pos = []
        for _, target, data in self.G.edges(vendor_id, data=True):
            if data.get("relation") == "HAS_PO":
                pos.append({"id": target, **self.G.nodes[target]})
        return pos

    def get_vendor_mafs(self, vendor_name: str) -> list[dict]:
        """Get OEM authorizations for a vendor."""
        vendor_id = f"vendor:{vendor_name}"
        if not self.G.has_node(vendor_id):
            return []

        mafs = []
        for _, target, data in self.G.edges(vendor_id, data=True):
            if data.get("relation") == "HAS_MAF":
                mafs.append({"id": target, **self.G.nodes[target]})
        return mafs

    def get_vendors_with_oem(self, oem_name: str) -> list[str]:
        """Get all vendors authorized by a specific OEM."""
        oem_id = f"oem:{oem_name}"
        if not self.G.has_node(oem_id):
            return []

        vendors = []
        for source, _, data in self.G.in_edges(oem_id, data=True):
            if data.get("relation") == "AUTHORIZED_BY":
                name = self.G.nodes[source].get("name", source)
                vendors.append(name)
        return vendors

    def get_vendors_missing_proof(self) -> list[dict]:
        """Get vendors that have POs without execution proof."""
        results = []
        for vendor in self.get_all_vendors():
            vendor_name = vendor.get("name", "")
            pos = self.get_vendor_pos(vendor_name)
            missing = [
                po for po in pos
                if po.get("execution_proof") != "SUBMITTED"
            ]
            if missing:
                results.append({
                    "vendor": vendor_name,
                    "status": vendor.get("status"),
                    "missing_proof_count": len(missing),
                    "po_numbers": [po.get("po_number") for po in missing],
                })
        return results

    def get_pos_above_value(self, min_value: float) -> list[dict]:
        """Get all POs with value above threshold."""
        results = []
        for n in self.G.nodes:
            node = self.G.nodes[n]
            if node.get("type") == "PurchaseOrder" and (node.get("po_value", 0) or 0) >= min_value:
                # Find vendor
                vendor = ""
                for source, _, data in self.G.in_edges(n, data=True):
                    if data.get("relation") == "HAS_PO":
                        vendor = self.G.nodes[source].get("name", source)
                        break
                results.append({
                    "vendor": vendor,
                    "po_number": node.get("po_number"),
                    "po_value": node.get("po_value"),
                    "issuing_org": node.get("issuing_org"),
                })
        return sorted(results, key=lambda x: x.get("po_value", 0), reverse=True)

    def get_graph_summary(self) -> str:
        """Generate a text summary of the knowledge graph."""
        type_counts = {}
        for n in self.G.nodes:
            t = self.G.nodes[n].get("type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        lines = [f"Knowledge Graph: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges"]
        for t, c in sorted(type_counts.items()):
            lines.append(f"  {t}: {c}")
        return "\n".join(lines)

    def serialize(self, path: str | Path):
        """Save graph to JSON."""
        data = nx.node_link_data(self.G)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def deserialize(cls, path: str | Path) -> "TenderKnowledgeGraph":
        """Load graph from JSON."""
        with open(path) as f:
            data = json.load(f)
        kg = cls()
        kg.G = nx.node_link_graph(data)
        return kg
