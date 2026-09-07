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
from repo_translator.diagram import DIAGRAM_TYPES, generate_diagrams
from repo_translator.providers import (
    DEFAULT_BACKEND,
    SUPPORTED_BACKENDS,
    SUPPORTED_PROVIDERS,
    make_offline_provider,
    make_provider,
)
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
    parser.add_argument("--mode", default="translate",
                        choices=["translate", "comment", "diagram"],
                        help="'translate' (default): shift the repo from --from to --to. "
                             "'comment': add doc-comments to classes/functions and inline "
                             "comments to non-obvious code, in the same language (no --to). "
                             "'diagram': analyze the repo's structure and emit static/dynamic/"
                             "HLD/LLD diagrams (mermaid + draw.io), see --diagram-types "
                             "(no --to).")
    parser.add_argument("--from",     "-f", dest="from_lang", required=True, metavar="LANG",
                        help="Source language (e.g. ts, go, java)")
    parser.add_argument("--to",       "-t", dest="to_lang",   default=None, metavar="LANG",
                        help="Target language (e.g. python, rust, kotlin). "
                             "Required for --mode translate; not used otherwise.")
    parser.add_argument("--diagram-types", default=None, metavar="LIST",
                        help=f"Comma-separated subset of {','.join(DIAGRAM_TYPES)} to generate "
                             "with --mode diagram (default: all four).")
    parser.add_argument("--in-place", action="store_true",
                        help="Write translated files into the source tree itself, beside "
                             "the files they came from (src/main.ts → src/main.py), instead "
                             "of a separate output directory. A file whose destination "
                             "already exists and wasn't written by langshift is skipped, "
                             "never overwritten. Cannot be combined with --output.")
    parser.add_argument("--output",   "-o", metavar="PATH", default=None,
                        help="Output directory (default: <input>_<to_lang> for --mode "
                             "translate, <input>_commented for --mode comment, "
                             "<input>_diagrams for --mode diagram)")
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
                        help="API key. Prefer the environment (ANTHROPIC_API_KEY, "
                             "OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, "
                             "OPENAI_COMPAT_API_KEY): a key passed here is visible to "
                             "anyone who can list processes, and lands in your shell "
                             "history.")
    parser.add_argument("--backend", default=DEFAULT_BACKEND,
                        choices=list(SUPPORTED_BACKENDS),
                        help="Completion backend (default: "
                             f"{DEFAULT_BACKEND!r}, or set $LANGSHIFT_BACKEND). "
                             "'native' talks to the vendor SDK directly, same as always. "
                             "'model-dispatcher' routes claude/openai/gemini/groq through "
                             "the shared model-dispatcher gateway instead (same retry/backoff "
                             "behaviour, just the implementation these other apps share). "
                             "ollama/openai-compat/offline always run natively either way.")
    parser.add_argument("--estimate", "-e", action="store_true",
                        help="Show token/cost estimate, confirm, then translate")
    parser.add_argument("--yes",      "-y", action="store_true",
                        help="Skip confirmation prompt when using --estimate")
    parser.add_argument("--run-tests", action="store_true",
                        help="After translation, run the translated test suite")
    parser.add_argument("--run", action=argparse.BooleanOptionalAction, default=None,
                        help="Execute each translated file once to check it works, feeding "
                             "any error back for up to 3 auto-fix attempts. Off by default: "
                             "the model's output is code, and running it is a decision you "
                             "make rather than one made for you. Turning it on markedly "
                             "improves output, since nothing else verifies the translation "
                             "runs. --run-tests implies it. (default: --no-run)")
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

    if args.in_place and args.output:
        print("Error: --in-place and --output are contradictory.")
        print("  --in-place writes into the input directory; drop one of them.")
        sys.exit(1)

    if args.run is False and args.run_tests:
        print("Error: --no-run and --run-tests are contradictory.")
        print("  Running the translated test suite executes the translated code.")
        sys.exit(1)

    if args.mode == "translate" and not args.to_lang:
        print("Error: --to is required for --mode translate.")
        sys.exit(1)
    if args.mode != "translate" and args.to_lang:
        print(f"Error: --to is not used with --mode {args.mode} (source language only).")
        sys.exit(1)
    if args.mode != "translate" and args.provider == "offline":
        print(f"Error: --provider offline does not support --mode {args.mode} "
              f"(rule-based source-to-source transformer only).")
        sys.exit(1)
    if args.mode != "translate" and args.cross_file_context:
        print(f"Error: --cross-file-context is not supported with --mode {args.mode}.")
        sys.exit(1)
    if args.mode != "diagram" and args.diagram_types:
        print("Error: --diagram-types only applies to --mode diagram.")
        sys.exit(1)
    if args.mode == "diagram" and args.in_place:
        print("Error: --in-place is not supported with --mode diagram.")
        sys.exit(1)
    if args.mode == "diagram" and (args.run_tests or args.run is True):
        print("Error: --run-tests/--run are not supported with --mode diagram "
              "(nothing is executed).")
        sys.exit(1)
    if args.mode == "diagram" and args.estimate:
        print("Error: --estimate is not supported with --mode diagram yet.")
        sys.exit(1)

    from_key = args.from_lang.lower().strip()
    if from_key not in _ALIAS_MAP:
        print(f"Error: unknown language for --from: '{from_key}'")
        print(f"  Supported: {', '.join(LANGUAGE_META)}")
        sys.exit(1)

    diagram_types: tuple[str, ...] = DIAGRAM_TYPES
    if args.mode == "diagram" and args.diagram_types:
        requested = tuple(t.strip() for t in args.diagram_types.split(","))
        unknown = [t for t in requested if t not in DIAGRAM_TYPES]
        if unknown:
            print(f"Error: unknown diagram type(s): {', '.join(unknown)}")
            print(f"  Supported: {', '.join(DIAGRAM_TYPES)}")
            sys.exit(1)
        diagram_types = requested

    to_key = ""
    if args.mode == "translate":
        to_key = args.to_lang.lower().strip()
        if to_key not in _ALIAS_MAP:
            print(f"Error: unknown language for --to: '{to_key}'")
            print(f"  Supported: {', '.join(LANGUAGE_META)}")
            sys.exit(1)
    elif args.mode == "comment":
        to_key = from_key  # annotate in place, same language

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"Error: input path does not exist: {input_path}")
        sys.exit(1)

    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = Path(args.output).expanduser().resolve()
    elif args.mode == "translate":
        output_path = input_path.parent / f"{input_path.name}_{_ALIAS_MAP[to_key]}"
    elif args.mode == "comment":
        output_path = input_path.parent / f"{input_path.name}_commented"
    else:  # diagram
        output_path = input_path.parent / f"{input_path.name}_diagrams"

    # Pricing label for display
    price = price_label(args.provider, args.model, args.base_url)

    if args.mode == "translate":
        mode_lines = f"  From     : {_ALIAS_MAP[from_key]}\n  To       : {_ALIAS_MAP[to_key]}"
    elif args.mode == "comment":
        mode_lines = f"  Language : {_ALIAS_MAP[from_key]}  (mode: comment)"
    else:
        mode_lines = (
            f"  Language : {_ALIAS_MAP[from_key]}  (mode: diagram)\n"
            f"  Diagrams : {', '.join(diagram_types)}"
        )

    print(f"""
╔══════════════════════════════════════════════╗
║           langshift  🔄                      ║
╚══════════════════════════════════════════════╝

  Input    : {input_path}
{mode_lines}
  Provider : {args.provider} / {args.model}  ({price})
  Backend  : {args.backend}
  Output   : {output_path}
""")

    if args.estimate:
        est = estimate_translation(
            repo_path=input_path,
            from_lang=from_key,
            to_lang=to_key,
            translate_manifests=not args.no_manifest and args.mode == "translate",
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
                answer = input("  Proceed? [y/N]: ").strip().lower()
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
            provider = make_provider(
                args.provider, args.model, args.api_key, args.base_url, backend=args.backend
            )
    except (ImportError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.mode == "diagram":
        diagram_report = generate_diagrams(
            repo_path=input_path,
            output_path=output_path,
            lang=from_key,
            provider=provider,
            diagram_types=diagram_types,
            verbose=not args.quiet,
        )
        diagram_report.print_summary()
        if not args.no_report:
            diagram_report.save(output_path)
        sys.exit(1 if diagram_report.failed > 0 else 0)

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
        execute=args.run,
        mode=args.mode,
    )

    report.print_summary()

    if not args.no_report:
        report.save(output_path)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
