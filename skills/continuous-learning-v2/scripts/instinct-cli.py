#!/usr/bin/env python3
"""
Instinct CLI - Manage instincts for Continuous Learning v2

Commands:
  status   - Show all instincts and their status
  import   - Import instincts from file or URL
  export   - Export instincts to file
  evolve   - Cluster instincts into skills/commands/agents
"""

import argparse
import sys
import re
import urllib.request
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from urllib.parse import urlparse

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

HOMUNCULUS_DIR = Path.home() / ".claude" / "homunculus"
INSTINCTS_DIR = HOMUNCULUS_DIR / "instincts"
PERSONAL_DIR = INSTINCTS_DIR / "personal"
INHERITED_DIR = INSTINCTS_DIR / "inherited"
EVOLVED_DIR = HOMUNCULUS_DIR / "evolved"
OBSERVATIONS_FILE = HOMUNCULUS_DIR / "observations.jsonl"

ALLOWED_URL_SCHEMES = {"https", "http"}

# Ensure directories exist
for d in [PERSONAL_DIR, INHERITED_DIR, EVOLVED_DIR / "skills", EVOLVED_DIR / "commands", EVOLVED_DIR / "agents"]:
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Instinct Parser
# ─────────────────────────────────────────────

def _finalize_instinct(current: dict, content_lines: list[str]) -> dict:
    """Attach accumulated content lines to an instinct dict."""
    current['content'] = '\n'.join(content_lines).strip()
    return current


def _parse_frontmatter_line(line: str, current: dict) -> None:
    """Parse a single YAML-like frontmatter key-value pair into current."""
    if ':' not in line:
        return
    key, value = line.split(':', 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if key == 'confidence':
        current[key] = float(value)
    else:
        current[key] = value


def parse_instinct_file(content: str) -> list[dict]:
    """Parse YAML-like instinct file format."""
    instincts = []
    current = {}
    in_frontmatter = False
    content_lines = []

    for line in content.split('\n'):
        if line.strip() == '---':
            if current:
                _finalize_instinct(current, content_lines)
                instincts.append(current)
            current = {}
            content_lines = []
            in_frontmatter = not in_frontmatter
        elif in_frontmatter:
            _parse_frontmatter_line(line, current)
        else:
            content_lines.append(line)

    # Don't forget the last instinct
    if current:
        _finalize_instinct(current, content_lines)
        instincts.append(current)

    return [i for i in instincts if i.get('id')]


def load_all_instincts() -> list[dict]:
    """Load all instincts from personal and inherited directories."""
    instincts = []

    for directory in [PERSONAL_DIR, INHERITED_DIR]:
        if not directory.exists():
            continue
        for file in directory.glob("*.yaml"):
            try:
                content = file.read_text()
                parsed = parse_instinct_file(content)
                for inst in parsed:
                    inst['_source_file'] = str(file)
                    inst['_source_type'] = directory.name
                instincts.extend(parsed)
            except Exception as e:
                print(f"Warning: Failed to parse {file}: {e}", file=sys.stderr)

    return instincts


# ─────────────────────────────────────────────
# Status Command
# ─────────────────────────────────────────────

def _print_instinct_detail(inst: dict) -> None:
    """Print a single instinct's status line with confidence bar and action."""
    conf = inst.get('confidence', 0.5)
    conf_bar = '\u2588' * int(conf * 10) + '\u2591' * (10 - int(conf * 10))
    trigger = inst.get('trigger', 'unknown trigger')

    print(f"  {conf_bar} {int(conf * 100):3d}%  {inst.get('id', 'unnamed')}")
    print(f"            trigger: {trigger}")

    content = inst.get('content', '')
    action_match = re.search(r'## Action\s*\n\s*(.+?)(?:\n\n|\n##|$)', content, re.DOTALL)
    if action_match:
        action = action_match.group(1).strip().split('\n')[0]
        print(f"            action: {action[:60]}{'...' if len(action) > 60 else ''}")

    print()


def _print_observations_stats() -> None:
    """Print observation file statistics if the file exists."""
    if not OBSERVATIONS_FILE.exists():
        return
    with open(OBSERVATIONS_FILE) as f:
        obs_count = sum(1 for _ in f)
    print("\u2500" * 57)
    print(f"  Observations: {obs_count} events logged")
    print(f"  File: {OBSERVATIONS_FILE}")


def cmd_status(args):
    """Show status of all instincts."""
    instincts = load_all_instincts()

    if not instincts:
        print("No instincts found.")
        print("\nInstinct directories:")
        print(f"  Personal:  {PERSONAL_DIR}")
        print(f"  Inherited: {INHERITED_DIR}")
        return

    by_domain = defaultdict(list)
    for inst in instincts:
        by_domain[inst.get('domain', 'general')].append(inst)

    print(f"\n{'=' * 60}")
    print(f"  INSTINCT STATUS - {len(instincts)} total")
    print(f"{'=' * 60}\n")

    personal = sum(1 for i in instincts if i.get('_source_type') == 'personal')
    inherited = sum(1 for i in instincts if i.get('_source_type') == 'inherited')
    print(f"  Personal:  {personal}")
    print(f"  Inherited: {inherited}")
    print()

    for domain in sorted(by_domain.keys()):
        domain_instincts = by_domain[domain]
        print(f"## {domain.upper()} ({len(domain_instincts)})")
        print()
        for inst in sorted(domain_instincts, key=lambda x: -x.get('confidence', 0.5)):
            _print_instinct_detail(inst)

    _print_observations_stats()
    print(f"\n{'=' * 60}\n")


# ─────────────────────────────────────────────
# Import Command
# ─────────────────────────────────────────────

def _fetch_content(source: str) -> str | None:
    """Fetch instinct content from a URL or local file path. Returns None on error."""
    if source.startswith('http://') or source.startswith('https://'):
        parsed = urlparse(source)
        if parsed.scheme not in ALLOWED_URL_SCHEMES:
            print(f"Error: URL scheme '{parsed.scheme}' not allowed", file=sys.stderr)
            return None
        print(f"Fetching from URL: {source}")
        try:
            with urllib.request.urlopen(source) as response:  # nosec B310
                return response.read().decode('utf-8')
        except Exception as e:
            print(f"Error fetching URL: {e}", file=sys.stderr)
            return None
    else:
        path = Path(source).expanduser()
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            return None
        return path.read_text()


def _categorize_instincts(new_instincts: list[dict], existing: list[dict]) -> tuple:
    """Split new instincts into to_add, to_update, and duplicates."""
    existing_ids = {i.get('id') for i in existing}
    to_add, to_update, duplicates = [], [], []

    for inst in new_instincts:
        inst_id = inst.get('id')
        if inst_id not in existing_ids:
            to_add.append(inst)
            continue
        existing_inst = next((e for e in existing if e.get('id') == inst_id), None)
        if existing_inst and inst.get('confidence', 0) > existing_inst.get('confidence', 0):
            to_update.append(inst)
        else:
            duplicates.append(inst)

    return to_add, to_update, duplicates


def _print_import_summary(to_add: list, to_update: list, duplicates: list) -> None:
    """Print categorized import summary."""
    if to_add:
        print(f"NEW ({len(to_add)}):")
        for inst in to_add:
            print(f"  + {inst.get('id')} (confidence: {inst.get('confidence', 0.5):.2f})")

    if to_update:
        print(f"\nUPDATE ({len(to_update)}):")
        for inst in to_update:
            print(f"  ~ {inst.get('id')} (confidence: {inst.get('confidence', 0.5):.2f})")

    if duplicates:
        print(f"\nSKIP ({len(duplicates)} - already exists with equal/higher confidence):")
        for inst in duplicates[:5]:
            print(f"  - {inst.get('id')}")
        if len(duplicates) > 5:
            print(f"  ... and {len(duplicates) - 5} more")


def _write_instincts_file(instincts: list[dict], source: str, output_file: Path) -> None:
    """Serialize instincts to a YAML-like file."""
    parts = [f"# Imported from {source}\n# Date: {datetime.now().isoformat()}\n\n"]

    for inst in instincts:
        parts.append("---\n")
        parts.append(f"id: {inst.get('id')}\n")
        parts.append(f"trigger: \"{inst.get('trigger', 'unknown')}\"\n")
        parts.append(f"confidence: {inst.get('confidence', 0.5)}\n")
        parts.append(f"domain: {inst.get('domain', 'general')}\n")
        parts.append("source: inherited\n")
        parts.append(f"imported_from: \"{source}\"\n")
        if inst.get('source_repo'):
            parts.append(f"source_repo: {inst.get('source_repo')}\n")
        parts.append("---\n\n")
        parts.append(inst.get('content', '') + "\n\n")

    output_file.write_text(''.join(parts))


def _filter_by_confidence(instincts: list[dict], min_conf: float) -> list[dict]:
    """Filter instincts by minimum confidence threshold."""
    return [i for i in instincts if i.get('confidence', 0.5) >= min_conf]


def _confirm_import(to_add: list, to_update: list) -> bool:
    """Prompt the user for import confirmation. Returns True if confirmed."""
    response = input(f"\nImport {len(to_add)} new, update {len(to_update)}? [y/N] ")
    return response.lower() == 'y'


def _build_output_path(source: str) -> Path:
    """Build the output file path for imported instincts."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    source_name = Path(source).stem if not source.startswith('http') else 'web-import'
    return INHERITED_DIR / f"{source_name}-{timestamp}.yaml"


def cmd_import(args):
    """Import instincts from file or URL."""
    source = args.source

    content = _fetch_content(source)
    if content is None:
        return 1

    new_instincts = parse_instinct_file(content)
    if not new_instincts:
        print("No valid instincts found in source.")
        return 1

    print(f"\nFound {len(new_instincts)} instincts to import.\n")

    existing = load_all_instincts()
    to_add, to_update, duplicates = _categorize_instincts(new_instincts, existing)

    min_conf = args.min_confidence or 0.0
    to_add = _filter_by_confidence(to_add, min_conf)
    to_update = _filter_by_confidence(to_update, min_conf)

    _print_import_summary(to_add, to_update, duplicates)

    if args.dry_run:
        print("\n[DRY RUN] No changes made.")
        return 0

    if not to_add and not to_update:
        print("\nNothing to import.")
        return 0

    if not args.force and not _confirm_import(to_add, to_update):
        print("Cancelled.")
        return 0

    output_file = _build_output_path(source)
    _write_instincts_file(to_add + to_update, source, output_file)

    print("\nImport complete!")
    print(f"   Added: {len(to_add)}")
    print(f"   Updated: {len(to_update)}")
    print(f"   Saved to: {output_file}")

    return 0


# ─────────────────────────────────────────────
# Export Command
# ─────────────────────────────────────────────

def _format_instinct_yaml(inst: dict) -> str:
    """Format a single instinct as YAML-like text for export."""
    parts = ["---\n"]
    for key in ['id', 'trigger', 'confidence', 'domain', 'source', 'source_repo']:
        if inst.get(key):
            value = inst[key]
            if key == 'trigger':
                parts.append(f'{key}: "{value}"\n')
            else:
                parts.append(f"{key}: {value}\n")
    parts.append("---\n\n")
    parts.append(inst.get('content', '') + "\n\n")
    return ''.join(parts)


def _apply_export_filters(instincts: list[dict], domain: str | None, min_confidence: float | None) -> list[dict]:
    """Apply domain and confidence filters to instincts."""
    if domain:
        instincts = [i for i in instincts if i.get('domain') == domain]
    if min_confidence:
        instincts = [i for i in instincts if i.get('confidence', 0.5) >= min_confidence]
    return instincts


def cmd_export(args):
    """Export instincts to file."""
    instincts = load_all_instincts()

    if not instincts:
        print("No instincts to export.")
        return 1

    instincts = _apply_export_filters(instincts, args.domain, args.min_confidence)

    if not instincts:
        print("No instincts match the criteria.")
        return 1

    header = f"# Instincts export\n# Date: {datetime.now().isoformat()}\n# Total: {len(instincts)}\n\n"
    output = header + ''.join(_format_instinct_yaml(inst) for inst in instincts)

    if args.output:
        Path(args.output).write_text(output)
        print(f"Exported {len(instincts)} instincts to {args.output}")
    else:
        print(output)

    return 0


# ─────────────────────────────────────────────
# Evolve Command
# ─────────────────────────────────────────────

TRIGGER_STOP_WORDS = ['when', 'creating', 'writing', 'adding', 'implementing', 'testing']


def _normalize_trigger(trigger: str) -> str:
    """Normalize a trigger string by removing common stop words."""
    result = trigger.lower()
    for keyword in TRIGGER_STOP_WORDS:
        result = result.replace(keyword, '').strip()
    return result


def _find_skill_candidates(instincts: list[dict]) -> list[dict]:
    """Cluster instincts by normalized trigger and return candidates with 2+ members."""
    trigger_clusters = defaultdict(list)
    for inst in instincts:
        key = _normalize_trigger(inst.get('trigger', ''))
        trigger_clusters[key].append(inst)

    candidates = []
    for trigger, cluster in trigger_clusters.items():
        if len(cluster) >= 2:
            avg_conf = sum(i.get('confidence', 0.5) for i in cluster) / len(cluster)
            candidates.append({
                'trigger': trigger,
                'instincts': cluster,
                'avg_confidence': avg_conf,
                'domains': list(set(i.get('domain', 'general') for i in cluster)),
            })

    candidates.sort(key=lambda x: (-len(x['instincts']), -x['avg_confidence']))
    return candidates


def _print_skill_candidates(candidates: list[dict]) -> None:
    """Print skill candidate clusters."""
    if not candidates:
        return
    print("\n## SKILL CANDIDATES\n")
    for i, cand in enumerate(candidates[:5], 1):
        print(f"{i}. Cluster: \"{cand['trigger']}\"")
        print(f"   Instincts: {len(cand['instincts'])}")
        print(f"   Avg confidence: {cand['avg_confidence']:.0%}")
        print(f"   Domains: {', '.join(cand['domains'])}")
        print("   Instincts:")
        for inst in cand['instincts'][:3]:
            print(f"     - {inst.get('id')}")
        print()


def _print_command_candidates(instincts: list[dict]) -> None:
    """Print workflow instincts that could become commands."""
    workflow = [i for i in instincts if i.get('domain') == 'workflow' and i.get('confidence', 0) >= 0.7]
    if not workflow:
        return
    print(f"\n## COMMAND CANDIDATES ({len(workflow)})\n")
    for inst in workflow[:5]:
        trigger = inst.get('trigger', 'unknown')
        cmd_name = trigger.replace('when ', '').replace('implementing ', '').replace('a ', '')
        cmd_name = cmd_name.replace(' ', '-')[:20]
        print(f"  /{cmd_name}")
        print(f"    From: {inst.get('id')}")
        print(f"    Confidence: {inst.get('confidence', 0.5):.0%}")
        print()


def _print_agent_candidates(skill_candidates: list[dict]) -> None:
    """Print complex multi-step agent candidates."""
    agents = [c for c in skill_candidates if len(c['instincts']) >= 3 and c['avg_confidence'] >= 0.75]
    if not agents:
        return
    print(f"\n## AGENT CANDIDATES ({len(agents)})\n")
    for cand in agents[:3]:
        agent_name = cand['trigger'].replace(' ', '-')[:20] + '-agent'
        print(f"  {agent_name}")
        print(f"    Covers {len(cand['instincts'])} instincts")
        print(f"    Avg confidence: {cand['avg_confidence']:.0%}")
        print()


def cmd_evolve(args):
    """Analyze instincts and suggest evolutions to skills/commands/agents."""
    instincts = load_all_instincts()

    if len(instincts) < 3:
        print("Need at least 3 instincts to analyze patterns.")
        print(f"Currently have: {len(instincts)}")
        return 1

    print(f"\n{'=' * 60}")
    print(f"  EVOLVE ANALYSIS - {len(instincts)} instincts")
    print(f"{'=' * 60}\n")

    high_conf = sum(1 for i in instincts if i.get('confidence', 0) >= 0.8)
    print(f"High confidence instincts (>=80%): {high_conf}")

    skill_candidates = _find_skill_candidates(instincts)
    print(f"\nPotential skill clusters found: {len(skill_candidates)}")

    _print_skill_candidates(skill_candidates)
    _print_command_candidates(instincts)
    _print_agent_candidates(skill_candidates)

    if args.generate:
        print("\n[Would generate evolved structures here]")
        print("  Skills would be saved to:", EVOLVED_DIR / "skills")
        print("  Commands would be saved to:", EVOLVED_DIR / "commands")
        print("  Agents would be saved to:", EVOLVED_DIR / "agents")

    print(f"\n{'=' * 60}\n")
    return 0


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Instinct CLI for Continuous Learning v2')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Status
    subparsers.add_parser('status', help='Show instinct status')

    # Import
    import_parser = subparsers.add_parser('import', help='Import instincts')
    import_parser.add_argument('source', help='File path or URL')
    import_parser.add_argument('--dry-run', action='store_true', help='Preview without importing')
    import_parser.add_argument('--force', action='store_true', help='Skip confirmation')
    import_parser.add_argument('--min-confidence', type=float, help='Minimum confidence threshold')

    # Export
    export_parser = subparsers.add_parser('export', help='Export instincts')
    export_parser.add_argument('--output', '-o', help='Output file')
    export_parser.add_argument('--domain', help='Filter by domain')
    export_parser.add_argument('--min-confidence', type=float, help='Minimum confidence')

    # Evolve
    evolve_parser = subparsers.add_parser('evolve', help='Analyze and evolve instincts')
    evolve_parser.add_argument('--generate', action='store_true', help='Generate evolved structures')

    args = parser.parse_args()

    handlers = {
        'status': cmd_status,
        'import': cmd_import,
        'export': cmd_export,
        'evolve': cmd_evolve,
    }
    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    parser.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main() or 0)
