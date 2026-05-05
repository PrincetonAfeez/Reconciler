"""CLI interface for the reconciler module."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reconciler import export_mock_csvs, run_reconciliation

logger = logging.getLogger(__name__)


def menu() -> None:
    """Interactive reconciliation menu."""
    valid_choices = {"1", "2", "3", "4", "5"}
    current_source = None
    current_reference = None
    fuzzy_threshold = 0.80
    date_tolerance = 2
    amount_tolerance = 0.50

    while True:
        print()
        print("Expense Reconciliation")
        print("1. Load two files")
        print("2. Generate mock data")
        print("3. Run reconciliation")
        print("4. Adjust thresholds")
        print("5. Quit")
        choice = input("Choose an option: ").strip()
        if choice not in valid_choices:
            print("Please choose one of the listed options.")
            continue

        if choice == "1":
            current_source = input("Source CSV path: ").strip()
            current_reference = input("Reference CSV path: ").strip()
            print("File paths saved.")

        elif choice == "2":
            source_path, reference_path = export_mock_csvs()
            current_source = str(source_path)
            current_reference = str(reference_path)
            print(f"Mock files written to {source_path} and {reference_path}.")

        elif choice == "3":
            try:
                if current_source and current_reference:
                    result = run_reconciliation(
                        source_file=current_source,
                        reference_file=current_reference,
                        fuzzy_threshold=fuzzy_threshold,
                        date_tolerance=date_tolerance,
                        amount_tolerance=amount_tolerance,
                    )
                else:
                    result = run_reconciliation(
                        use_mock=True,
                        fuzzy_threshold=fuzzy_threshold,
                        date_tolerance=date_tolerance,
                        amount_tolerance=amount_tolerance,
                    )
                    print("No files were loaded, so mock data was used.")

                for warning in result["warnings"]:
                    print(warning)
                print(result["report_text"])
                export = input("Export this report to a text file too? (y/n): ").strip().lower()
                if export == "y":
                    rerun = run_reconciliation(
                        source_file=current_source,
                        reference_file=current_reference,
                        fuzzy_threshold=fuzzy_threshold,
                        date_tolerance=date_tolerance,
                        amount_tolerance=amount_tolerance,
                        use_mock=(current_source is None or current_reference is None),
                        export_report=True,
                    )
                    print(f"Report exported to {rerun['output_path']}")
            except (ValueError, FileNotFoundError, OSError, UnicodeDecodeError, KeyError, TypeError, IndexError) as error:
                print(f"Could not run reconciliation: {error}")

        elif choice == "4":
            fuzzy_text = input(f"Fuzzy threshold 0-100 [{int(fuzzy_threshold * 100)}]: ").strip()
            date_text = input(f"Date tolerance days [{date_tolerance}]: ").strip()
            amount_text = input(f"Amount tolerance dollars [{amount_tolerance}]: ").strip()
            try:
                if fuzzy_text:
                    fuzzy_threshold = float(fuzzy_text) / 100
                if date_text:
                    date_tolerance = int(date_text)
                if amount_text:
                    amount_tolerance = float(amount_text)
                print("Thresholds updated.")
            except ValueError:
                print("One of those values was invalid, so the old settings stayed in place.")

        elif choice == "5":
            print("Exiting reconciler.")
            break


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two transaction CSVs and report matches, mismatches, and duplicates.",
    )
    parser.add_argument(
        "--menu",
        action="store_true",
        help="Open the interactive menu (default when no other CLI flags are used).",
    )
    parser.add_argument("--source", type=Path, metavar="PATH", help="Source CSV (e.g. bank export).")
    parser.add_argument("--reference", type=Path, metavar="PATH", help="Reference CSV (e.g. personal ledger).")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use built-in sample data instead of files.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write reconciliation_report.txt to --output-dir or the current directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for --export (default: current working directory).",
    )
    parser.add_argument(
        "--fuzzy",
        type=float,
        default=80.0,
        metavar="PCT",
        help="Merchant fuzzy-match threshold as percent 0-100 (default: 80).",
    )
    parser.add_argument(
        "--date-tolerance",
        type=int,
        default=2,
        metavar="DAYS",
        help="Match if dates are within this many days (default: 2).",
    )
    parser.add_argument(
        "--amount-tolerance",
        type=float,
        default=0.50,
        metavar="USD",
        help="Treat amounts within this dollar spread as compatible (default: 0.50).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log debug details to stderr.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Less logging to stderr (warnings and errors only).")
    parser.add_argument(
        "--quiet-report",
        action="store_true",
        help="Do not print the report to stdout (still writes a file when using --export).",
    )
    return parser


def _configure_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s", force=True)
        return
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", force=True)


def _fuzzy_threshold_from_percent(value: float) -> float:
    if value > 1.0:
        return max(0.0, min(1.0, value / 100.0))
    return max(0.0, min(1.0, value))


def run_cli_args(args: argparse.Namespace) -> int:
    """Run one reconciliation from parsed CLI args; return process exit code."""
    _configure_logging(args.verbose, args.quiet)
    if args.quiet and args.verbose:
        logger.warning("Both --quiet and --verbose; using quiet logging levels.")

    use_cli = args.source is not None or args.reference is not None or args.mock or args.export
    if args.menu or not use_cli:
        menu()
        return 0

    if args.mock:
        if args.source is not None or args.reference is not None:
            logger.error("Do not combine --mock with --source/--reference.")
            return 2
    else:
        if args.source is None or args.reference is None:
            logger.error("Provide both --source and --reference, or use --mock.")
            return 2

    fuzzy = _fuzzy_threshold_from_percent(args.fuzzy)
    try:
        result = run_reconciliation(
            source_file=args.source,
            reference_file=args.reference,
            fuzzy_threshold=fuzzy,
            date_tolerance=args.date_tolerance,
            amount_tolerance=args.amount_tolerance,
            use_mock=args.mock,
            export_report=args.export,
            output_dir=args.output_dir,
        )
    except (ValueError, FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        logger.error("%s", exc)
        return 1

    for warning in result["warnings"]:
        logger.warning("%s", warning)

    if not args.quiet_report:
        print(result["report_text"])

    if args.export and result["output_path"] is not None:
        logger.info("Report file: %s", result["output_path"])

    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point: CLI when arguments are present, otherwise interactive menu."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        menu()
        return

    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    code = run_cli_args(args)
    raise SystemExit(code)

