"""CLI for the textstrata substrate.

Use --workspace or TEXTSTRATA_WORKSPACE to choose the workspace.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import cast
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import activity, classify, embeddings, frontmatter
from .catalog import Catalog
from .control import backup_workspace, control_doctor, load_config, load_effective_config, process_approved_ingest
from .ingest import build_item, ingest_file
from .linking import build_links, links_for
from .presentation import PAPER_SKIN, RenderContext, render_item_html, render_text
from .research import daily_briefing, relate, research, synthesize
from .store import TextStrataStore
from .validate import validate
from .workspace import apply_config_environment, load_cascading_config, resolve_workspace
from .vault import export_obsidian_vault, import_obsidian_vault
from .application.setup import initialize_workspace, setup_status


def _root() -> Path:
    return resolve_workspace()


def _catalog(root: Path) -> Catalog:
    root.mkdir(parents=True, exist_ok=True)
    return Catalog(root)


def _load_items(store: TextStrataStore) -> list:
    return [
        build_item(p.read_text(encoding="utf-8"), fallback_id=p.stem)[0]
        for p in store.normalized_paths()
    ]


def _load_item(store: TextStrataStore, item_id: str):
    path = store.normalized_path_for_id(item_id)
    if path is None:
        raise FileNotFoundError(item_id)
    item, suggested, fm = build_item(path.read_text(encoding="utf-8"), fallback_id=item_id)
    result = validate(item)
    policy = classify.suggest_policy(item.type, item.title, item.body)
    return item, suggested, fm, result, policy


def cmd_ingest(paths: list[str]) -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    for path in paths:
        res = ingest_file(store, path)
        status = "published" if res.published else "REJECTED"
        print(f"[{status}] {res.item.id}  ({res.item.type.value})")
        if res.had_stacked_frontmatter:
            print(f"    merged {len(res.frontmatter_conflicts)} conflict(s) from stacked front-matter blocks")
        for c in res.frontmatter_conflicts:
            print(f"    conflict: {c}")
        if res.suggested_tags:
            print(f"    suggested tags: {', '.join(res.suggested_tags)}")
        for e in res.validation.errors:
            print(f"    error: {e}")
        for w in res.validation.warnings:
            print(f"    warning: {w}")
        if res.published:
            cat.index_item(res.item)
    cat.close()
    return 0


def cmd_vault_import(path: str, overwrite: bool = False) -> int:
    store = TextStrataStore(_root())
    result = import_obsidian_vault(store, path, overwrite=overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


def cmd_vault_export(path: str) -> int:
    store = TextStrataStore(_root())
    print(json.dumps(export_obsidian_vault(store, path), ensure_ascii=False, indent=2))
    return 0


def cmd_preview(path: str, json_output: bool = False) -> int:
    p = Path(path)
    item, suggested, fm = build_item(p.read_text(encoding="utf-8"), fallback_id=p.stem)
    result = validate(item)
    policy = classify.suggest_policy(item.type, item.title, item.body)
    payload = {
        "path": str(p),
        "item_id": item.id,
        "title": item.title,
        "type": item.type.value,
        "handling": item.handling.value,
        "preservation": item.preservation.value,
        "suggested_handling": policy.handling.value,
        "suggested_preservation": policy.preservation.value,
        "suggested_policy_reason": policy.rationale,
        "tags": item.tags,
        "suggested_tags": suggested,
        "warnings": result.warnings,
        "errors": result.errors,
        "had_stacked_frontmatter": fm.had_stacked_blocks,
        "frontmatter_conflicts": fm.conflicts,
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{item.id} ({item.type.value})")
        print(f"  title: {item.title}")
        print(f"  handling: {item.handling.value} -> {policy.handling.value}")
        print(f"  preservation: {item.preservation.value} -> {policy.preservation.value}")
        if suggested:
            print(f"  suggested tags: {', '.join(suggested)}")
        for w in result.warnings:
            print(f"  warning: {w}")
        for e in result.errors:
            print(f"  error: {e}")
    return 0


def cmd_render(item_id: str, fmt: str = "text", out: str | None = None) -> int:
    store = TextStrataStore(_root())
    item, suggested, fm, result, policy = _load_item(store, item_id)
    ctx = RenderContext(
        title=item.title,
        item=item,
        validation_errors=result.errors,
        validation_warnings=result.warnings,
        suggested_tags=suggested,
        policy_handling=policy.handling.value,
        policy_preservation=policy.preservation.value,
        policy_reason=policy.rationale,
    )
    rendered = render_item_html(ctx, PAPER_SKIN) if fmt == "html" else render_text(ctx)
    if out:
        Path(out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


def cmd_rebuild() -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    n = cat.rescan(store)
    cat.close()
    print(f"reindexed {n} item(s) from {store.normalized_dir}")
    return 0


def cmd_search(query: str, json_output: bool = False, semantic: bool = False, sort: str = "relevance") -> int:
    if semantic:
        return _cmd_search_semantic(query, json_output=json_output)
    cat = _catalog(_root())
    hits = cat.search(query, sort=sort)
    if json_output:
        print(json.dumps([
            {
                "id": h.id, "type": h.type, "title": h.title,
                "snippet": h.snippet.strip(),
                "knowledge_score": h.knowledge_score,
                "ingested_at": h.ingested_at,
            }
            for h in hits
        ], ensure_ascii=False, indent=2))
    else:
        if not hits:
            print("no matches")
        for h in hits:
            prefix = ""
            if sort == "score":
                prefix = f"{h.knowledge_score:6.2f}  "
            elif sort in ("newest", "oldest"):
                prefix = f"{h.ingested_at[:10]}  "
            print(f"{prefix}{h.id}  [{h.type}]  {h.title}")
            if h.snippet.strip():
                print(f"    {h.snippet.strip()}")
    cat.close()
    return 0


def _cmd_search_semantic(query: str, json_output: bool = False) -> int:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        print(
            "Semantic search requires sentence-transformers. "
            "Install with: pip install sentence-transformers",
            flush=True,
        )
        return 1

    root = _root()
    store = TextStrataStore(root)
    items = _load_items(store)
    if not items:
        print("no items to search")
        return 1

    model_name = os.environ.get("TEXTSTRATA_EMBEDDING_MODEL") or os.environ.get("FABRIC_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    item_map = {it.id: it for it in items}

    emb = embeddings.ensure_embeddings(items, root, model_name=model_name)
    results = embeddings.search_semantic(query, emb, model_name=model_name, top_k=10)

    if json_output:
        out = []
        for item_id, score in results:
            item = item_map.get(item_id)
            if item:
                out.append({
                    "id": item.id,
                    "type": item.type.value,
                    "title": item.title,
                    "score": round(score, 4),
                    "snippet": item.body[:200].strip() if item.body else "",
                })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not results:
            print("no matches")
            return 0
        for item_id, score in results:
            item = item_map.get(item_id)
            if item:
                print(f"{score:.4f}  {item.id}  [{item.type.value}]  {item.title}")
                if item.body:
                    snippet = item.body[:200].replace("\n", " ").strip()
                    print(f"    {snippet}")
    return 0


def cmd_links(item_id: str, json_output: bool = False) -> int:
    store = TextStrataStore(_root())
    items = _load_items(store)
    links = build_links(items)
    out = links_for(item_id, links)
    if json_output:
        print(json.dumps([
            {"source": lnk.source, "target": lnk.target, "reason": lnk.reason, "weight": lnk.weight}
            for lnk in out
        ], ensure_ascii=False, indent=2))
    else:
        if not out:
            print(f"no outgoing links for {item_id}")
        for link in out:
            print(f"{link.source} -> {link.target}  ({link.reason}, w={link.weight})")
    return 0


def cmd_analyze(json_output: bool = False) -> int:
    from .analyze import analyze, print_report

    store = TextStrataStore(_root())
    items = _load_items(store)
    if not items:
        if json_output:
            print(json.dumps({"total_items": 0}))
        else:
            print("No items to analyze.")
        return 0
    report = analyze(items, store)
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
    return 0


def cmd_score(json_output: bool = False, show_clusters: bool = False) -> int:
    from .similarity import score_corpus

    store = TextStrataStore(_root())
    items = _load_items(store)
    if not items:
        if json_output:
            print(json.dumps([]))
        else:
            print("no items to score")
        return 0
    explicit = [(link.source, link.target, float(link.weight)) for link in build_links(items)]
    scores = score_corpus(items, explicit)

    cat = _catalog(_root())
    cat.update_scores(scores)
    cat.close()

    if json_output:
        print(json.dumps([
            {"item_id": s.item_id, "score": round(s.score, 2), "pagerank": round(s.pagerank, 6),
             "hub": round(s.hub, 6), "authority": round(s.authority, 6),
             "degree": s.degree, "community": s.community, "neighbours": s.neighbours}
            for s in sorted(scores.values(), key=lambda x: -x.score)
        ], ensure_ascii=False, indent=2))
        return 0

    if show_clusters:
        comm = Counter(s.community for s in scores.values())
        print(f"{len(comm)} emergent cluster(s):")
        for label, n in comm.most_common():
            print(f"\n[{n}] anchored by {label}")
            for member in sorted(s.item_id for s in scores.values() if s.community == label):
                print(f"     {member}  ({scores[member].score:.1f})")
        return 0

    for s in sorted(scores.values(), key=lambda x: -x.score):
        print(f"{s.score:6.2f}  deg={s.degree:<3d} {s.item_id}")
    return 0


def cmd_check(verbose: bool = False) -> int:
    store = TextStrataStore(_root())
    store.ensure_dirs()
    items = _load_items(store)
    errors: list[str] = []
    warnings: list[str] = []

    if not items:
        print("No items in store.")
        return 1

    for item in items:
        result = validate(item)
        for e in result.errors:
            errors.append(f"{item.id}: {e}")
        for w in result.warnings:
            warnings.append(f"{item.id}: {w}")

    links = build_links(items)
    all_ids = {it.id for it in items}
    linked_ids: set[str] = set()
    for link in links:
        linked_ids.add(link.source)
        linked_ids.add(link.target)
    orphaned = all_ids - linked_ids

    print(f"Store: {store.root}")
    print(f"Items: {len(items)}")
    print(f"Links: {len(links)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Orphaned: {len(orphaned)}")
    if verbose:
        for e in errors:
            print(f"  ERROR: {e}")
        for w in warnings:
            print(f"  WARN:  {w}")
        if orphaned:
            print("  Orphaned items:")
            for oid in sorted(orphaned):
                print(f"    {oid}")
    return 1 if errors else 0


def cmd_init() -> int:
    result = initialize_workspace(_root())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(json_output: bool = False) -> int:
    # Deliberately read-only: unlike init, doctor never creates directories or
    # catalog state, so it is safe to run against an offline or unwritable path.
    status = setup_status(
        _root(),
        host=os.environ.get("FABRIC_HOST", ""),
        port=int(os.environ["FABRIC_PORT"]) if os.environ.get("FABRIC_PORT", "").isdigit() else None,
    )
    if json_output:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"Workspace: {status['workspace']}")
        print(f"Initialized: {'yes' if status['initialized'] else 'no'}")
        print(f"Items: {status['item_count']}")
        print(f"Core ready: {'yes' if status['core_ready'] else 'no'}")
        for capability in cast(list[dict[str, object]], status["optional_capabilities"]):
            print(f"Optional {capability['id']}: {'available' if capability['available'] else 'not available'}")
    return 0 if status["core_ready"] else 1


def cmd_control(action: str, *, dry_run: bool = False) -> int:
    root = _root()
    config, config_file = load_config(root) if action == "doctor" else load_effective_config(root)
    if action == "doctor":
        result = control_doctor(root, config=config, config_file=config_file)
    elif action == "backup":
        result = backup_workspace(root, config=config, dry_run=dry_run)
    elif action == "ingest":
        result = process_approved_ingest(root, config=config, dry_run=dry_run)
    else:
        if dry_run:
            raise ValueError("--dry-run is only supported for control backup and ingest")
        ingest = process_approved_ingest(root, config=config)
        backup = backup_workspace(root, config=config)
        result = {"ingest": ingest, "backup": backup}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_log(tail: bool = False, limit: int | None = None, since: str | None = None, json_output: bool = False) -> int:
    root = _root()
    entries = activity.read(root, limit=limit, since=since, tail=tail)
    if json_output:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("No activity logged yet.")
        return 0
    for e in entries:
        ts = e.get("timestamp", "?")[:19]
        action = e.get("action", "?")
        item = e.get("item_id") or ""
        outcome = e.get("outcome", "")
        print(f"{ts}  {action:12s}  {item:30s}  {outcome}")
    return 0


def cmd_stats() -> int:
    store = TextStrataStore(_root())
    items = _load_items(store)
    cat = _catalog(_root())
    n_catalog = cat.count()
    cat.close()
    if not items:
        print("Store is empty.")
        return 0
    total = len(items)
    total_words = sum(len(it.body.split()) for it in items if it.body)
    avg_body = total_words // total if total else 0
    by_type: Counter[str] = Counter(it.type.value for it in items)
    all_tags: Counter[str] = Counter(t.lower() for it in items for t in it.tags)
    links = build_links(items)
    link_density = len(links) / total if total else 0
    store_size = sum(
        p.stat().st_size for p in store.normalized_dir.glob("*.md")
    ) if store.normalized_dir.exists() else 0
    print(f"TextStrata Store: {store.root}")
    print(f"{'─'*50}")
    print(f"  Items:           {total}")
    print(f"  Catalog entries: {n_catalog}")
    print(f"  Total words:     {total_words:,}")
    print(f"  Avg body length: {avg_body} words")
    print(f"  Cross-links:     {len(links)}  ({link_density:.2f}/item)")
    print(f"  Store size:      {store_size:,} bytes")
    print()
    print("Content type breakdown:")
    for t, n in by_type.most_common():
        pct = n / total * 100
        bar = "█" * int(pct / 5) + "░" * max(0, 20 - int(pct / 5))
        print(f"  {t:28s} {n:3d}  {bar}")
    print()
    if all_tags:
        print(f"Top 10 tags (of {len(all_tags)}):")
        for tag, n in all_tags.most_common(10):
            print(f"  #{tag:20s} {n}")
    return 0


def cmd_ask(query: str, model: str | None = None) -> int:
    from .research import _fts_safe
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    hits = cat.search(_fts_safe(query))
    cat.close()
    if not hits:
        print("No relevant content found in the knowledge base.")
        return 1
    items = []
    for h in hits[:5]:
        path = store.normalized_path_for_id(h.id)
        if path:
            item, _, _ = build_item(path.read_text(encoding="utf-8"), fallback_id=h.id)
            items.append(item)
    context = "\n\n".join(
        f"---\nid: {it.id}\ntitle: {it.title}\ntype: {it.type.value}\ntags: {', '.join(it.tags)}\n---\n{it.body[:2000]}"
        for it in items
    )
    model_name = model or os.environ.get("TEXTSTRATA_LLM_MODEL") or os.environ.get("FABRIC_LLM_MODEL", "phi3:mini")
    prompt = f"""You are answering based on a personal knowledge base. Use ONLY the context below to answer. If the context doesn't contain enough information, say so. Cite sources by their item ID in brackets like [item-id].

Context:
{context}

Question: {query}

Answer concisely (2-4 sentences) with citations:"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({"model": model_name, "prompt": prompt, "stream": False, "options": {"num_predict": 512}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            answer = result.get("response", "").strip()
            if answer:
                print(answer)
            else:
                print("No response from model.")
    except Exception as exc:
        print(f"Could not reach Ollama at localhost:11434 ({exc})")
        print()
        print("Relevant items from the knowledge base:")
        for it in items:
            print(f"  [{it.id}] {it.title} ({it.type.value})")
    return 0


def cmd_research(query: str, model: str | None = None, depth: str = "balanced") -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    try:
        result = research(query, store, cat, model=model, depth=depth)
    except RuntimeError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    finally:
        cat.close()
    print(result.answer)
    if result.sources:
        print(f"\n── Sources ({len(result.sources)}) ──")
        for s in result.sources:
            print(f"  [{s.item_id}] {s.title}")
    return 0


def cmd_synthesize(topic: str, model: str | None = None) -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    try:
        result = synthesize(topic, store, cat, model=model)
    except RuntimeError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    finally:
        cat.close()
    print(result.answer)
    if result.sources:
        print(f"\n── Sources ({len(result.sources)}) ──")
        for s in result.sources:
            print(f"  [{s.item_id}] {s.title}")
    return 0


def cmd_daily(model: str | None = None, days: int = 1) -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    try:
        result = daily_briefing(store, cat, model=model, days=days)
    except RuntimeError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    finally:
        cat.close()
    print(result.answer)
    return 0


def cmd_relate(item_ids: list[str], model: str | None = None) -> int:
    store = TextStrataStore(_root())
    cat = _catalog(_root())
    try:
        result = relate(item_ids, store, cat, model=model)
    except RuntimeError as exc:
        print(f"Error: {exc}", flush=True)
        return 1
    finally:
        cat.close()
    print(result.answer)
    if result.sources:
        print(f"\n── Items ({len(result.sources)}) ──")
        for s in result.sources:
            print(f"  [{s.item_id}] {s.title}")
    return 0


DOCKERFILE = """FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[all]"

FROM base AS textstrata
ENTRYPOINT ["python3", "-m", "textstrata"]
CMD ["--help"]

FROM base AS textstrata-mcp
ENTRYPOINT ["python3", "-m", "textstrata.mcp_server"]
"""

COMPOSE_TEMPLATE = """services:
  textstrata:
    build:
      context: {context}
      target: textstrata
    image: textstrata:latest
    container_name: textstrata
    entrypoint: ["python3", "-m", "textstrata"]
    environment:
      - TEXTSTRATA_WORKSPACE=/data/textstrata-store
      - OLLAMA_HOST=http://ollama:11434
      - TEXTSTRATA_LLM_MODEL=${{TEXTSTRATA_LLM_MODEL:-deepseek-r1:1.5b}}
      - PYTHONUNBUFFERED=1
    volumes:
      - {store_path}:/data/textstrata-store
    depends_on:
      ollama:
        condition: service_healthy
    stdin_open: true
    tty: true
    profiles:
      - full

  ollama:
    image: ollama/ollama:latest
    container_name: textstrata-ollama
    environment:
      - OLLAMA_KEEP_ALIVE=24h
    volumes:
      - ollama-models:/root/.ollama
    ports:
      - "11434:11434"
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 120s
    profiles:
      - full

  textstrata-lite:
    build:
      context: {context}
      target: textstrata
    image: textstrata:latest
    container_name: textstrata-lite
    entrypoint: ["python3", "-m", "textstrata"]
    environment:
      - TEXTSTRATA_WORKSPACE=/data/textstrata-store
      - PYTHONUNBUFFERED=1
    volumes:
      - {store_path}:/data/textstrata-store
    stdin_open: true
    tty: true
    profiles:
      - lite

volumes:
  ollama-models:
"""


def cmd_docker(action: str, context: str | None = None) -> int:
    root = _root().resolve()
    store_path = root.as_posix()
    ctx = context or os.getcwd()

    if action == "init":
        dcf = Path(ctx) / "Dockerfile"
        if not dcf.exists():
            dcf.write_text(DOCKERFILE)
            print(f"Wrote {dcf}")
        else:
            print(f"{dcf} already exists — skipping")

        yml = Path(ctx) / "docker-compose.yml"
        if not yml.exists():
            content = COMPOSE_TEMPLATE.format(store_path=store_path, context=ctx)
            yml.write_text(content)
            print(f"Wrote {yml}")
        else:
            print(f"{yml} already exists — skipping")

        print()
        print("  # Run with Ollama (full stack)")
        print("  docker compose --profile full up -d")
        print("  docker compose --profile full run --rm textstrata stats")
        print()
        print("  # Run without Ollama (lite)")
        print("  docker compose --profile lite run --rm textstrata stats")
        print()
        print("  # Or use the alias (like in the NetworkChuck Docker video):")
        print(f'  alias textstrata=\'docker run --rm -e TEXTSTRATA_WORKSPACE=/data/textstrata-store -v "{store_path}:/data/textstrata-store" textstrata:latest\'')
        return 0

    if action == "alias":
        store_path = _root().resolve().as_posix()
        print('Copy into your ~/.bashrc or ~/.zshrc:\n')
        print(f'  alias textstrata=\'docker run --rm -e TEXTSTRATA_WORKSPACE=/data/textstrata-store -v "{store_path}:/data/textstrata-store" textstrata:latest\'')
        print()
        print('Then: source ~/.bashrc && textstrata stats')
        return 0

    print("Usage: textstrata docker init|alias")
    return 2


def cmd_migrate(dry_run: bool = False) -> int:
    """Backfill contributor_chain on items that predate the provenance field."""
    store = TextStrataStore(_root())
    store.ensure_dirs()
    updated = 0
    skipped = 0
    for path in store.normalized_paths():
        raw = path.read_text(encoding="utf-8")
        fm = frontmatter.parse(raw)
        data = fm.data
        prov = data.get("provenance", {})
        if not isinstance(prov, dict):
            prov = {}
        if prov.get("contributor_chain"):
            skipped += 1
            continue
        created_via = str(prov.get("created_via") or data.get("created_via") or "")
        handling = str(data.get("handling") or "")
        if created_via.startswith("textstrata") or handling == "human_plus_ai":
            prov["contributor_chain"] = "via_script"
        elif handling == "human_only":
            prov["contributor_chain"] = "human"
        elif handling == "ai_only_eyes" or handling == "auto_sanitize_then_review":
            prov["contributor_chain"] = "via_ai"
        else:
            skipped += 1
            continue
        data["provenance"] = prov
        rendered = frontmatter.render(data, fm.body)
        if not dry_run:
            path.write_text(rendered, encoding="utf-8")
        updated += 1
        print(f"  {path.name}: contributor_chain={prov['contributor_chain']}")
    print(f"\nUpdated: {updated}, Skipped (already set): {skipped}")
    if updated and not dry_run:
        print("Rebuilding catalog...")
        catalog = Catalog(_root())
        catalog.rescan(store)
        catalog.close()
        print("Catalog rebuilt.")
    return 0


def cmd_completion(shell: str) -> int:
    if shell == "bash":
        print("""_textstrata_completions() {
    local cur prev words cword
    _init_completion || return
    local commands="ingest preview render rebuild search links score analyze check log stats ask research synthesize daily relate completion migrate watch mcp web"
    if [[ $cword -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        return
    fi
    case "${words[1]}" in
        ingest) COMPREPLY=($(compgen -f -- "$cur")) ;;
        preview) COMPREPLY=($(compgen -f -- "$cur")) ;;
        render) COMPREPLY=() ;;
        search) COMPREPLY=($(compgen -W "--semantic --json" -- "$cur")) ;;
        links) ;;
        score) COMPREPLY=($(compgen -W "--json --clusters" -- "$cur")) ;;
        analyze) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        check) COMPREPLY=($(compgen -W "--verbose" -- "$cur")) ;;
        log) COMPREPLY=($(compgen -W "--json --tail --since" -- "$cur")) ;;
        ask) ;;
        research) COMPREPLY=($(compgen -W "--model --depth" -- "$cur")) ;;
        synthesize) COMPREPLY=($(compgen -W "--model" -- "$cur")) ;;
        daily) COMPREPLY=($(compgen -W "--model --days" -- "$cur")) ;;
        relate) ;;
        completion) COMPREPLY=($(compgen -W "bash zsh fish" -- "$cur")) ;;
    esac
}
complete -F _textstrata_completions textstrata""")
    elif shell == "zsh":
        print("""#compdef textstrata
_textstrata() {
    local -a commands
    commands=(
        'ingest:ingest one or more files'
        'preview:inspect a file before ingest'
        'render:render a normalized item'
        'rebuild:rebuild the FTS catalog'
        'search:full-text and semantic search'
        'links:show outgoing cross-links for an item'
        'score:knowledge scores and communities'
        'analyze:gap/coverage analysis report'
        'check:integrity and health check'
        'log:view activity log'
        'stats:knowledge base statistics'
        'ask:ask a question over the knowledge base'
        'research:deep research with citations'
        'synthesize:synthesize a topic briefing'
        'daily:daily briefing from recent activity'
        'relate:find connections between items'
        'completion:generate shell completions'
        'watch:watch directories for changes'
        'mcp:run the stdio MCP server'
        'web:run the local HTTP server'
    )
    _describe 'textstrata' commands
}
compdef _textstrata textstrata""")
    elif shell == "fish":
        print("""complete -c textstrata -f -a 'ingest preview render rebuild search links score analyze check log stats ask research synthesize daily relate completion watch mcp web'
complete -c textstrata -n '__fish_use_subcommand' -a 'ingest' -d 'ingest one or more files'
complete -c textstrata -n '__fish_use_subcommand' -a 'preview' -d 'inspect a file before ingest'
complete -c textstrata -n '__fish_use_subcommand' -a 'render' -d 'render a normalized item'
complete -c textstrata -n '__fish_use_subcommand' -a 'search' -d 'full-text and semantic search'
complete -c textstrata -n '__fish_seen_subcommand_from search' -s s -l semantic -d 'semantic (embedding-based) search'
complete -c textstrata -n '__fish_use_subcommand' -a 'links' -d 'show outgoing cross-links for an item'
complete -c textstrata -n '__fish_use_subcommand' -a 'score' -d 'knowledge scores and communities'
complete -c textstrata -n '__fish_use_subcommand' -a 'check' -d 'integrity and health check'
complete -c textstrata -n '__fish_use_subcommand' -a 'log' -d 'view activity log'
complete -c textstrata -n '__fish_use_subcommand' -a 'stats' -d 'knowledge base statistics'
complete -c textstrata -n '__fish_use_subcommand' -a 'ask' -d 'ask a question over the knowledge base'
complete -c textstrata -n '__fish_use_subcommand' -a 'research' -d 'deep research with citations'
complete -c textstrata -n '__fish_use_subcommand' -a 'synthesize' -d 'synthesize a topic briefing'
complete -c textstrata -n '__fish_seen_subcommand_from synthesize' -l model -d 'Ollama model name'
complete -c textstrata -n '__fish_use_subcommand' -a 'daily' -d 'daily briefing from activity'
complete -c textstrata -n '__fish_seen_subcommand_from daily' -l model -d 'Ollama model name'
complete -c textstrata -n '__fish_seen_subcommand_from daily' -l days -d 'days back'
complete -c textstrata -n '__fish_use_subcommand' -a 'relate' -d 'find connections between items'
complete -c textstrata -n '__fish_use_subcommand' -a 'web' -d 'run the local HTTP server'""")
    else:
        print(f"Unknown shell: {shell}. Supported: bash, zsh, fish")
        return 1
    return 0


def cmd_watch(directories: list[str]) -> int:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("Watch mode requires `watchdog`. Install with: pip install watchdog", flush=True)
        return 1

    class TextStrataIngestHandler(FileSystemEventHandler):
        def __init__(self, store):
            self.store = store
            self._debounce: dict[str, float] = {}
        def on_created(self, event):
            if event.is_directory or not event.src_path.endswith(".md"):
                return
            Path(event.src_path)  # validate
            self._ingest(event.src_path)
        def on_modified(self, event):
            if event.is_directory or not event.src_path.endswith(".md"):
                return
            self._ingest(event.src_path)
        def _ingest(self, path):
            import time as _time
            now = _time.time()
            last = self._debounce.get(path, 0)
            if now - last < 0.5:
                return
            self._debounce[path] = now
            try:
                res = ingest_file(self.store, path)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                status = "published" if res.published else "REJECTED"
                print(f"[WATCH] {ts}  {status:10s}  {res.item.id:30s}  ({res.item.type.value})", flush=True)
                activity.write(self.store.root, "watch-ingest", item_id=res.item.id, outcome=status, path=path)
            except Exception as exc:
                print(f"[WATCH] ERROR  {path}: {exc}", flush=True)

    store = TextStrataStore(_root())
    handler = TextStrataIngestHandler(store)
    observer = Observer()
    for d in directories:
        p = Path(d).resolve()
        if not p.is_dir():
            print(f"Not a directory: {p}", flush=True)
            continue
        observer.schedule(handler, str(p), recursive=False)
        print(f"Watching {p} for markdown changes...", flush=True)
    if not observer._watches:
        print("No valid directories to watch.", flush=True)
        return 1
    observer.start()
    try:
        while True:
            import time as _time
            _time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


def cmd_mcp() -> int:
    from .mcp_server import main as mcp_main
    mcp_main()
    return 0


def cmd_web() -> int:
    from .web import main as web_main
    web_main()
    return 0


def cmd_restart(host: str, port: int, workspace_root: Path) -> int:
    import signal
    import time
    pid_path = workspace_root / ".fabric" / "server.pid"
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, signal.SIGTERM)
            print(f"Stopped old server (pid {old_pid})", flush=True)
            for _ in range(30):
                if not pid_path.exists():
                    break
                time.sleep(0.1)
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)
            print("Removed stale PID file", flush=True)
    else:
        print("No running server found, starting fresh", flush=True)
    from .web import serve as web_serve
    web_serve(host=host, port=port, workspace_root=workspace_root)
    return 0


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="output as JSON")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="textstrata",
        description="TextStrata — layered knowledge infrastructure for local-first work.",
        epilog="Workspace precedence: --workspace, TEXTSTRATA_WORKSPACE, then ./.workspace.",
    )
    p.add_argument("--workspace", metavar="PATH", help="isolated TextStrata workspace")
    sub = p.add_subparsers(dest="command", required=True)

    ingest_p = sub.add_parser("ingest", help="ingest one or more files")
    ingest_p.add_argument("files", nargs="+", metavar="FILE", help="path to markdown file(s)")

    vault_import_p = sub.add_parser("vault-import", help="import an Obsidian vault")
    vault_import_p.add_argument("path", metavar="PATH", help="Obsidian vault directory")
    vault_import_p.add_argument("--overwrite", action="store_true", help="replace existing imported item IDs")

    vault_export_p = sub.add_parser("vault-export", help="export the workspace as an Obsidian vault")
    vault_export_p.add_argument("path", metavar="PATH", help="destination directory")

    preview_p = sub.add_parser("preview", help="inspect a file before ingest")
    preview_p.add_argument("file", metavar="FILE", help="path to markdown file")
    _add_json_flag(preview_p)

    render_p = sub.add_parser("render", help="render a normalized item")
    render_p.add_argument("item_id", metavar="ITEM_ID", help="item identifier")
    render_p.add_argument("--format", choices=("text", "html"), default="text", help="output format")
    render_p.add_argument("--out", metavar="FILE", help="write to file instead of stdout")

    sub.add_parser("init", help="initialize workspace directories without creating notes")
    doctor_p = sub.add_parser("doctor", help="read-only workspace and capability diagnostics")
    doctor_p.add_argument("--json", action="store_true", help="output as JSON")
    control_p = sub.add_parser("control", help="optional backup and approved-ingest control plane")
    control_p.add_argument("action", choices=("doctor", "backup", "ingest", "run"), help="control operation")
    control_p.add_argument("--dry-run", action="store_true", help="show planned external actions without mutating or uploading")

    sub.add_parser("rebuild", help="rebuild the FTS catalog from normalized items")

    search_p = sub.add_parser("search", help="full-text search")
    search_p.add_argument("query", help="search query")
    search_p.add_argument("--semantic", "-s", action="store_true", help="semantic (embedding-based) search")
    search_p.add_argument("--sort", choices=("relevance", "score", "newest", "oldest"), default="relevance",
                          help="sort order: relevance (default), score (knowledge score), newest, oldest")
    _add_json_flag(search_p)

    links_p = sub.add_parser("links", help="show outgoing cross-links for an item")
    links_p.add_argument("item_id", metavar="ITEM_ID", help="item identifier")
    _add_json_flag(links_p)

    score_p = sub.add_parser("score", help="knowledge scores and communities")
    score_p.add_argument("--clusters", action="store_true", help="show emergent topic clusters")
    _add_json_flag(score_p)

    analyze_p = sub.add_parser("analyze", help="gap/coverage analysis report")
    _add_json_flag(analyze_p)

    check_p = sub.add_parser("check", help="integrity and health check")
    check_p.add_argument("--verbose", action="store_true", help="show individual errors and warnings")

    log_p = sub.add_parser("log", help="view activity log")
    log_p.add_argument("--json", action="store_true", help="output as JSON")
    log_p.add_argument("--tail", action="store_true", help="show only the most recent entries")
    log_p.add_argument("-n", type=int, metavar="N", dest="limit", help="limit to N entries")
    log_p.add_argument("--since", metavar="ISO", help="only entries after this timestamp")

    sub.add_parser("stats", help="knowledge base statistics")

    ask_p = sub.add_parser("ask", help="ask a quick question over the knowledge base (simple RAG)")
    ask_p.add_argument("query", help="natural language question")
    ask_p.add_argument("--model", metavar="MODEL", help="Ollama model name (default: phi3:mini)")

    research_p = sub.add_parser("research", help="deep research: search + synthesize with citations")
    research_p.add_argument("query", help="research question or topic")
    research_p.add_argument("--model", metavar="MODEL", help="Ollama model name")
    research_p.add_argument("--depth", choices=("balanced", "deep"), default="balanced",
                            help="research depth (deep = more sources)")

    synth_p = sub.add_parser("synthesize", help="synthesize a topic briefing from all relevant items")
    synth_p.add_argument("topic", help="topic to synthesize")
    synth_p.add_argument("--model", metavar="MODEL", help="Ollama model name")

    daily_p = sub.add_parser("daily", help="generate a daily briefing from recent activity")
    daily_p.add_argument("--model", metavar="MODEL", help="Ollama model name")
    daily_p.add_argument("--days", type=int, default=1, help="how many days back to look")

    relate_p = sub.add_parser("relate", help="find hidden connections between items")
    relate_p.add_argument("items", nargs="+", metavar="ITEM_ID", help="two or more item IDs")
    relate_p.add_argument("--model", metavar="MODEL", help="Ollama model name")

    completion_p = sub.add_parser("completion", help="generate shell completions")
    completion_p.add_argument("shell", choices=("bash", "zsh", "fish"), help="shell type")

    watch_p = sub.add_parser("watch", help="watch directories for changes and auto-ingest")
    watch_p.add_argument("directories", nargs="+", metavar="DIR", help="directories to watch")

    migrate_p = sub.add_parser("migrate", help="backfill contributor_chain for pre-chain items")
    migrate_p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    sub.add_parser("mcp", help="run the stdio MCP server")
    sub.add_parser("web", help="run the local HTTP presentation server")
    restart_p = sub.add_parser("restart", help="restart the web server")
    restart_p.add_argument("--host", help="host to bind")
    restart_p.add_argument("--port", type=int, help="port to bind")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace_root = resolve_workspace(args.workspace)
    os.environ["TEXTSTRATA_WORKSPACE"] = str(workspace_root)
    os.environ.setdefault("MARKBASE_WORKSPACE", str(workspace_root))
    config = load_cascading_config(workspace_root)
    apply_config_environment(config)

    if args.command == "ingest":
        return cmd_ingest(args.files)
    if args.command == "init":
        return cmd_init()
    if args.command == "doctor":
        return cmd_doctor(json_output=args.json)
    if args.command == "control":
        return cmd_control(args.action, dry_run=args.dry_run)
    if args.command == "vault-import":
        return cmd_vault_import(args.path, overwrite=args.overwrite)
    if args.command == "vault-export":
        return cmd_vault_export(args.path)
    if args.command == "preview":
        return cmd_preview(args.file, json_output=args.json)
    if args.command == "render":
        return cmd_render(args.item_id, fmt=args.format, out=args.out)
    if args.command == "rebuild":
        return cmd_rebuild()
    if args.command == "search":
        return cmd_search(args.query, json_output=args.json, semantic=args.semantic, sort=args.sort)
    if args.command == "links":
        return cmd_links(args.item_id, json_output=args.json)
    if args.command == "score":
        return cmd_score(json_output=args.json, show_clusters=args.clusters)
    if args.command == "analyze":
        return cmd_analyze(json_output=args.json)
    if args.command == "check":
        return cmd_check(verbose=getattr(args, "verbose", False))
    if args.command == "log":
        return cmd_log(tail=args.tail, limit=args.limit, since=args.since, json_output=args.json)
    if args.command == "stats":
        return cmd_stats()
    if args.command == "ask":
        return cmd_ask(args.query, model=getattr(args, "model", None))
    if args.command == "research":
        return cmd_research(args.query, model=getattr(args, "model", None), depth=args.depth)
    if args.command == "synthesize":
        return cmd_synthesize(args.topic, model=getattr(args, "model", None))
    if args.command == "daily":
        return cmd_daily(model=getattr(args, "model", None), days=args.days)
    if args.command == "relate":
        return cmd_relate(args.items, model=getattr(args, "model", None))
    if args.command == "completion":
        return cmd_completion(args.shell)
    if args.command == "watch":
        return cmd_watch(args.directories)
    if args.command == "migrate":
        return cmd_migrate(dry_run=getattr(args, "dry_run", False))
    if args.command == "mcp":
        return cmd_mcp()
    if args.command == "web":
        return cmd_web()
    if args.command == "restart":
        return cmd_restart(
            host=args.host or os.environ.get("TEXTSTRATA_HOST") or os.environ.get("FABRIC_HOST", "0.0.0.0"),
            port=args.port or int(os.environ.get("TEXTSTRATA_PORT") or os.environ.get("FABRIC_PORT", "8700")),
            workspace_root=workspace_root,
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
