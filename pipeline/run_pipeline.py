"""
Pipeline Orchestrator — delegates to the refactored execute_pipeline runner.
"""
import sys
import logging
from pathlib import Path
from typing import Optional

from scripts.run_pipeline import run_pipeline

logger = logging.getLogger(__name__)


def run_full_pipeline(
    config_path: str | Path = "config/tender_config.json",
    output_dir: str | Path = "data",
    vendor_filter: Optional[str] = None,
):
    """
    Delegate pipeline execution to scratch/execute_pipeline.py.
    This preserves the interface used by server.py and other callers.
    """
    logger.info("Delegating pipeline execution to scratch/execute_pipeline.py...")
    # Map arguments from run_full_pipeline's signature
    return run_pipeline(
        config_path=config_path,
        output_dir=output_dir,
        vendor_filter=vendor_filter,
        force_rerun=True,  # Re-run is forced when triggered programmatically/dashboard
        dry_run=False
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run tender evaluation pipeline")
    parser.add_argument("--config", default="config/tender_config.json", help="Path to tender config JSON")
    parser.add_argument("--output", default="data", help="Output directory")
    parser.add_argument("--vendor", default=None, help="Process only this vendor (partial match)")
    parser.add_argument("--force", action="store_true", help="Force re-running of all stages")
    args = parser.parse_args()

    run_pipeline(
        config_path=args.config,
        output_dir=args.output,
        vendor_filter=args.vendor,
        force_rerun=args.force,
        dry_run=False
    )
