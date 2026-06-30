"""
main.py — Command-line entry point for the Data Quality Manager.

USAGE EXAMPLES:
  # Full analysis pipeline on a file:
  python main.py analyze --file path/to/constituents.xlsx

  # Generate sample data then run analysis:
  python main.py demo

  # Standardize data and save cleaned output:
  python main.py standardize --file path/to/constituents.xlsx --output cleaned.xlsx

  # Run unit tests:
  python main.py test

  # Interactive duplicate review:
  python main.py analyze --file path/to/constituents.xlsx --interactive

ARCHITECTURE:
main.py is intentionally thin — it parses arguments, wires up dependencies,
calls the pipeline, and prints results. All business logic lives in the core/
modules. This "thin controller, fat model" pattern makes the code easy to
reuse: you could swap this CLI for a web interface or a Jupyter notebook
without touching any of the business logic.
"""

import argparse
import logging
import os
import sys
import time
import unittest
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
# We configure logging before importing anything else so that early log
# messages are captured.

def setup_logging(log_dir: str = "logs", verbose: bool = False) -> None:
    """
    Configure logging to both a rotating file and the console.

    WHY LOG TO BOTH FILE AND CONSOLE?
    Console output lets you watch progress in real time. File logs preserve
    the full history for post-run review — especially useful when a long job
    finishes overnight and you want to see what happened step by step.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / "data_quality_manager.log"

    handlers = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
    )

logger = logging.getLogger(__name__)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_analysis(
    file_path: str,
    interactive: bool = False,
    output_dir: str = "output",
    verbose: bool = False,
) -> None:
    """
    Full analysis pipeline:
    1. Load data
    2. Standardize fields
    3. Detect duplicates
    4. Merge high-confidence duplicates
    5. Validate remaining data
    6. Detect hard errors
    7. Generate all reports
    """
    setup_logging(verbose=verbose)

    # Import here (not at top level) so --help works even without dependencies
    import pandas as pd
    from config.settings import Settings
    from core.data_loader import DataLoader
    from core.duplicate_detector import DuplicateDetector
    from core.record_merger import RecordMerger
    from core.data_validator import DataValidator
    from core.error_detector import ErrorDetector
    from core.report_generator import ReportGenerator
    from models.schemas import QualityReport
    from utils.audit_trail import AuditTrail

    start_time = time.time()

    # ── 1. Configure ──────────────────────────────────────────────────────────
    settings = Settings()
    settings.output_dir = output_dir
    audit = AuditTrail(settings.audit_file)

    print("\n" + "="*65)
    print("  DATA QUALITY MANAGER — Analysis Pipeline")
    print("="*65)

    # ── 2. Load data ──────────────────────────────────────────────────────────
    print(f"\n[1/6] Loading data from: {file_path}")
    loader = DataLoader(settings)
    df = loader.load(file_path)
    n_original = len(df)
    print(f"      {n_original:,} records loaded.")

    report = QualityReport(total_records=n_original)

    # ── 3. Standardize ────────────────────────────────────────────────────────
    print("\n[2/6] Standardizing fields (phone, email, name, state, ZIP)...")
    validator = DataValidator(settings, audit)
    df = validator.standardize(df)
    print("      Standardization complete.")

    # ── 4. Detect duplicates ──────────────────────────────────────────────────
    print("\n[3/6] Detecting duplicates...")
    detector = DuplicateDetector(settings)
    all_pairs = detector.detect_all(df)
    exact_pairs = [p for p in all_pairs if p.match_type == "exact"]
    fuzzy_pairs = [p for p in all_pairs if p.match_type == "fuzzy"]
    report.exact_duplicates = len(exact_pairs)
    report.fuzzy_duplicates = len(fuzzy_pairs)
    report.duplicate_pairs  = all_pairs
    print(f"      Found: {len(exact_pairs)} exact duplicates, {len(fuzzy_pairs)} fuzzy candidates.")

    # Export pending-review file for medium-confidence pairs
    merger = RecordMerger(settings, audit)
    review_path = str(Path(output_dir) / "pending_review.xlsx")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    merger.export_pending_review(df, all_pairs, review_path)
    if any(p.confidence_score < settings.auto_merge_threshold for p in all_pairs):
        print(f"      Medium-confidence pairs saved to: {review_path}")

    # ── 5. Merge ──────────────────────────────────────────────────────────────
    print(f"\n[4/6] Merging duplicates (auto-merge threshold: {settings.auto_merge_threshold}%)...")
    df, merge_results = merger.review_and_merge(df, all_pairs, interactive=interactive)
    report.records_merged = len(merge_results)
    report.merge_results  = merge_results
    print(f"      {len(merge_results)} record pairs merged. Dataset now has {len(df):,} records.")

    # ── 6. Validate ───────────────────────────────────────────────────────────
    print("\n[5/6] Validating data quality...")
    issues = validator.detect_issues(df)
    report.validation_issues  = issues
    report.missing_value_issues = sum(
        1 for i in issues if i.issue_type.value == "Missing Value"
    )
    report.format_issues = sum(
        1 for i in issues
        if i.issue_type.value in ("Invalid Format", "Needs Standardization")
    )
    report.invalid_emails = sum(
        1 for i in issues if i.issue_type.value == "Invalid Email"
    )
    report.invalid_phones = sum(
        1 for i in issues if i.issue_type.value == "Invalid Phone"
    )
    print(f"      {len(issues)} validation issues found.")

    # ── 7. Error detection ────────────────────────────────────────────────────
    print("\n[6/6] Detecting hard errors (dates, IDs, inconsistencies)...")
    error_det = ErrorDetector(settings)
    df_for_errors = df.copy()
    for date_col in ("date_added", "date_modified"):
        if date_col in df_for_errors.columns:
            df_for_errors[date_col] = pd.to_datetime(df_for_errors[date_col], errors="coerce")

    errors = error_det.detect_all(df_for_errors)
    report.error_records = errors
    report.invalid_dates = sum(
        1 for e in errors if e.error_type.value == "Invalid Date"
    )
    report.duplicate_ids = sum(
        1 for e in errors if e.error_type.value == "Duplicate Constituent ID"
    )
    print(f"      {len(errors)} hard errors found.")

    # ── 8. Reports ────────────────────────────────────────────────────────────
    print("\n[Reporting] Generating Excel and CSV reports...")
    reporter = ReportGenerator(settings)
    paths = reporter.generate_all(report)

    # Export cleaned dataset
    cleaned_path = str(Path(output_dir) / "cleaned_constituents.xlsx")
    df.to_excel(cleaned_path, index=False)
    paths["cleaned_data"] = cleaned_path

    # Log run summary
    audit.log_run_summary({
        "total_records":    n_original,
        "records_merged":   report.records_merged,
        "exact_duplicates": report.exact_duplicates,
        "fuzzy_duplicates": report.fuzzy_duplicates,
        "quality_score":    report.overall_quality_score,
    })

    elapsed = time.time() - start_time

    # ── Console summary ───────────────────────────────────────────────────────
    print("\n" + "="*65)
    print("  ANALYSIS COMPLETE")
    print("="*65)
    print(f"  Records analyzed:       {n_original:>10,}")
    print(f"  Exact duplicates:       {report.exact_duplicates:>10,}")
    print(f"  Fuzzy candidates:       {report.fuzzy_duplicates:>10,}")
    print(f"  Records merged:         {report.records_merged:>10,}")
    print(f"  Validation issues:      {len(issues):>10,}")
    print(f"  Hard errors:            {len(errors):>10,}")
    score = report.overall_quality_score
    score_label = "GOOD" if score >= 80 else "FAIR" if score >= 60 else "POOR"
    print(f"  Quality score:          {score:>9.1f}%  ({score_label})")
    print(f"  Time elapsed:           {elapsed:>9.1f}s")
    print("\n  Output files:")
    for name, path in paths.items():
        print(f"    {name:<20} {path}")
    print("="*65 + "\n")


def run_demo() -> None:
    """Generate sample data and run the full pipeline against it."""
    setup_logging(verbose=False)

    sample_path = "sample_data/sample_constituents.xlsx"
    print("Generating sample constituent data...")
    from sample_data.generate_sample_data import generate_sample_data
    generate_sample_data(
        n_clean=80, n_exact_dupe=5, n_fuzzy_dupe=8,
        n_messy=10, n_errors=5, output_path=sample_path,
    )
    print(f"Sample data written to: {sample_path}\n")
    run_analysis(sample_path, output_dir="output/demo")


def run_standardize(file_path: str, output_path: str) -> None:
    """Standardize a file and save the cleaned result without running full analysis."""
    setup_logging(verbose=False)

    from config.settings import Settings
    from core.data_loader import DataLoader
    from core.data_validator import DataValidator
    from utils.audit_trail import AuditTrail

    settings = Settings()
    audit = AuditTrail(settings.audit_file)
    loader = DataLoader(settings)

    print(f"Loading: {file_path}")
    df = loader.load(file_path)
    print(f"Standardizing {len(df):,} records...")
    validator = DataValidator(settings, audit)
    df = validator.standardize(df)
    df.to_excel(output_path, index=False)
    print(f"Cleaned data saved to: {output_path}")


def run_tests() -> None:
    """Discover and run all unit tests."""
    loader = unittest.TestLoader()
    suite  = loader.discover(start_dir="tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ── CLI argument parsing ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="data_quality_manager",
        description="Constituent Data Quality Manager for Raiser's Edge / Blackbaud exports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # analyze subcommand
    p_analyze = subparsers.add_parser("analyze", help="Run full analysis pipeline on a file.")
    p_analyze.add_argument("--file",        required=True, help="Path to input CSV or Excel file.")
    p_analyze.add_argument("--output",      default="output", help="Output directory (default: output/).")
    p_analyze.add_argument("--interactive", action="store_true",
                           help="Interactively review medium-confidence duplicate pairs.")
    p_analyze.add_argument("--verbose",     action="store_true", help="Enable DEBUG logging.")

    # demo subcommand
    subparsers.add_parser("demo", help="Generate sample data and run a demo analysis.")

    # standardize subcommand
    p_std = subparsers.add_parser("standardize", help="Standardize and save a cleaned file.")
    p_std.add_argument("--file",   required=True, help="Path to input file.")
    p_std.add_argument("--output", required=True, help="Path to save cleaned output.")

    # test subcommand
    subparsers.add_parser("test", help="Run unit tests.")

    args = parser.parse_args()

    if args.command == "analyze":
        run_analysis(args.file, args.interactive, args.output, args.verbose)

    elif args.command == "demo":
        run_demo()

    elif args.command == "standardize":
        run_standardize(args.file, args.output)

    elif args.command == "test":
        run_tests()


if __name__ == "__main__":
    main()
