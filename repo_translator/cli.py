#!/usr/bin/env python3
"""
repo-translator CLI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repo_translator.agent import (
    _ALIAS_MAP,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    LANGUAGE_META,
    estimate_translation,
    price_label,
    resolve_language,
    translate_repo,
)
from repo_translator.providers import SUPPORTED_PROVIDERS, make_offline_provider, make_provider
from repo_translator.providers.base import LLMProvider


def build_parser() -> argparse.ArgumentParser:
    supported_langs = ", ".join(
        f"{name} ({', '.join(m['aliases'])})" if m["aliases"] else name
        for name, m in LANGUAGE_META.items()
    )

    parser = argparse.ArgumentParser(
        prog="repo-translate",
        description="Translate a code repository from one language to another using an LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Supported languages:\n  {supported_langs}",
    )
    parser.add_argument("--input",    "-i", required=True, metavar="PATH",
                        help="Path to the source repository")
    parser.add_argument("--from",     "-f", dest="from_lang", required=True, metavar="LANG",
                        help="Source language (e.g. ts, go, java)")
    parser.add_argument("--to",       "-t", dest="to_lang",   required=True, metavar="LANG",
                        help="Target language (e.g. python, rust, kotlin)")
    parser.add_argument("--output",   "-o", metavar="PATH", default=None,
                        help="Output directory (default: <input>_<to_lang>)")
    parser.add_argument("--provider", "-p", default=DEFAULT_PROVIDER,
                        choices=list(SUPPORTED_PROVIDERS),
                        help=f"LLM provider (default: {DEFAULT_PROVIDER})")
    parser.add_argument("--model",    "-m", default=DEFAULT_MODEL, metavar="MODEL",
                        help=f"Model name (default: {DEFAULT_MODEL}). "
                             f"Claude: haiku/sonnet/opus. "
                             f"OpenAI: gpt-4o, gpt-4o-mini. "
                             f"Gemini: gemini-1.5-pro. "
                             f"Groq: llama-3.1-70b-versatile, mixtral-8x7b-32768. "
                             f"Ollama: llama3, deepseek-coder, etc. "
                             f"openai-compat: any model supported by the target API.")
    parser.add_argument("--base-url", metavar="URL", default=None,
                        help="Base URL for openai-compat provider "
                             "(e.g. https://api.together.xyz/v1, https://openrouter.ai/api/v1)")
    parser.add_argument("--api-key",  metavar="KEY", default=None,
                        help="API key (env vars: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                             "GEMINI_API_KEY, GROQ_API_KEY, OPENAI_COMPAT_API_KEY)")
    parser.add_argument("--estimate", "-e", action="store_true",
                        help="Show token/cost estimate, confirm, then translate")
    parser.add_argument("--yes",      "-y", action="store_true",
                        help="Skip confirmation prompt when using --estimate")
    parser.add_argument("--run-tests", action="store_true",
                        help="After translation, run the translated test suite")
    parser.add_argument("--no-manifest", action="store_true",
                        help="Skip dependency manifest translation (package.json etc.)")
    parser.add_argument("--no-confidence", action="store_true",
                        help="Skip confidence scoring (saves extra API calls)")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip saving the summary report")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                        help="Resume an interrupted run using the output directory's checkpoint "
                             "(.translation_state.json), skipping already-completed files. "
                             "Use --no-resume to always start from scratch. (default: --resume)")
    parser.add_argument("--cross-file-context", action="store_true",
                        help="Give every file's translation prompt a read-only map of the "
                             "other source files and their top-level symbols, to help keep "
                             "cross-file imports/calls consistent. Off by default: increases "
                             "prompt size per file.")
    parser.add_argument("--quiet",    "-q", action="store_true",
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

    # Pricing label for display
    price = price_label(args.provider, args.model, args.base_url)

    print(f"""
╔══════════════════════════════════════════════╗
║           langshift  🔄                      ║
╚══════════════════════════════════════════════╝

  Input    : {input_path}
  From     : {_ALIAS_MAP[from_key]}
  To       : {_ALIAS_MAP[to_key]}
  Provider : {args.provider} / {args.model}  ({price})
  Output   : {output_path}
""")

    if args.estimate:
        est = estimate_translation(
            repo_path=input_path,
            from_lang=from_key,
            to_lang=to_key,
            translate_manifests=not args.no_manifest,
            provider=args.provider,
            model=args.model,
            score_confidence=not args.no_confidence,
        )
        manifest_line = f" + {est['manifest_count']} manifest" if est["manifest_count"] else ""
        conf_note     = "  (includes confidence scoring calls)" if not args.no_confidence else ""
        cost_str      = (
            f"~${est['estimated_cost']:.4f} USD"
            if est["estimated_cost"] is not None
            else "unknown (pricing not on record)"
        )
        print(f"  📊 Cost Estimate  ({args.provider}/{args.model})")
        print(f"  {'─' * 44}")
        print(f"   Files      : {est['file_count']} source{manifest_line}")
        print(f"   Input      : ~{est['input_tokens']:,} tokens{conf_note}")
        print(f"   Output     : ~{est['output_tokens']:,} tokens")
        print(f"   Est. cost  : {cost_str}")
        print(f"  {'─' * 44}\n")

        if not args.yes:
            try:
                answer = input("  Proceed with translation? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Aborted.")
                sys.exit(0)
            if answer not in ("y", "yes"):
                print("  Aborted.")
                sys.exit(0)

    try:
        provider: LLMProvider
        if args.provider == "offline":
            from_resolved = resolve_language(from_key)
            to_resolved   = resolve_language(to_key)
            provider = make_offline_provider(from_resolved, to_resolved)
        else:
            provider = make_provider(args.provider, args.model, args.api_key, args.base_url)
    except (ImportError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    report = translate_repo(
        repo_path=input_path,
        output_path=output_path,
        from_lang=from_key,
        to_lang=to_key,
        provider=provider,
        verbose=not args.quiet,
        translate_manifests=not args.no_manifest,
        run_tests_after=args.run_tests,
        score_confidence=not args.no_confidence,
        resume=args.resume,
        cross_file_context=args.cross_file_context,
    )

    report.print_summary()

    if not args.no_report:
        report.save(output_path)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
