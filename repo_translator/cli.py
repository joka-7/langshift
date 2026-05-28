#!/usr/bin/env python3
"""
repo-translator CLI
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from repo_translator.agent import LANGUAGE_META, _ALIAS_MAP, translate_repo


def build_parser() -> argparse.ArgumentParser:
    supported = ", ".join(
        f"{name} ({', '.join(m['aliases'])})" if m["aliases"] else name
        for name, m in LANGUAGE_META.items()
    )

    parser = argparse.ArgumentParser(
        prog="repo-translate",
        description="Translate a code repository from one language to another using Claude AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Supported languages:\n  {supported}",
    )
    parser.add_argument("--input",   "-i", required=True, metavar="PATH",
                        help="Path to the source repository")
    parser.add_argument("--from",    "-f", dest="from_lang", required=True, metavar="LANG",
                        help="Source language (e.g. ts, go, java)")
    parser.add_argument("--to",      "-t", dest="to_lang",   required=True, metavar="LANG",
                        help="Target language (e.g. python, rust, kotlin)")
    parser.add_argument("--output",  "-o", metavar="PATH", default=None,
                        help="Output directory (default: <input>_<to_lang>)")
    parser.add_argument("--api-key", metavar="KEY", default=None,
                        help="Anthropic API key (defaults to ANTHROPIC_API_KEY env var)")
    parser.add_argument("--run-tests", action="store_true",
                        help="After translation, run the translated test suite")
    parser.add_argument("--no-manifest", action="store_true",
                        help="Skip dependency manifest translation (package.json etc.)")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip saving the summary report")
    parser.add_argument("--quiet",   "-q", action="store_true",
                        help="Suppress progress output")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    from_key = args.from_lang.lower().strip()
    to_key   = args.to_lang.lower().strip()

    for key, label in [(from_key, "--from"), (to_key, "--to")]:
        if key not in _ALIAS_MAP:
            print(f"Error: unknown language for {label}: '{key}'")
            print(f"  Supported: {', '.join(LANGUAGE_META)}")
            sys.exit(1)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"Error: input path does not exist: {input_path}")
        sys.exit(1)

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.parent / f"{input_path.name}_{_ALIAS_MAP[to_key]}"
    )

    print(f"""
╔══════════════════════════════════════════════╗
║           repo-translator  🔄                ║
╚══════════════════════════════════════════════╝

  Input  : {input_path}
  From   : {_ALIAS_MAP[from_key]}
  To     : {_ALIAS_MAP[to_key]}
  Output : {output_path}
""")

    report = translate_repo(
        repo_path=input_path,
        output_path=output_path,
        from_lang=from_key,
        to_lang=to_key,
        api_key=args.api_key,
        verbose=not args.quiet,
        translate_manifests=not args.no_manifest,
        run_tests_after=args.run_tests,
    )

    report.print_summary()

    if not args.no_report:
        report.save(output_path)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
