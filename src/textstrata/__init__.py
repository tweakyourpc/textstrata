"""TextStrata — a machine-first knowledge substrate.

The markdown is the storage format; the textstrata is the system. This package is
 the substrate: typed content, deterministic ingestion, policy enforcement,
deterministic cross-linking, a rebuildable retrieval catalog, and presentation
skins. Presentation skins (Hugo, TUI, web) sit on top and may restyle but never
change meaning, link targets, accessibility order, policy, or retrieval
metadata.
"""

from .catalog import Catalog, SearchHit
from .ingest import IngestResult, ingest_file, ingest_text
from .linking import Link, LinkPolicy, build_links, link_collisions, links_for
from .models import (
    ContentType,
    TextStrataItem,
    HandlingMode,
    PreservationMode,
    Provenance,
)
from .analyze import analyze, print_report
from .presentation import CONSOLE_SKIN, PAPER_SKIN, RenderContext, Skin, render_hugo_item, render_hugo_page, render_item_html, render_library_index, render_text, render_tui_item
from .similarity import (
    KnowledgeScore,
    SimilarityPolicy,
    SimilarityEdge,
    build_similarity_edges,
    build_tfidf,
    score_corpus,
)
from .store import TextStrataStore
from .validate import ValidationResult, validate
from .vocabulary import (
    SynonymProposal,
    canonical_tokens,
    infer_synonyms,
    load_synonyms,
    stem,
)

from . import activity, embeddings, vocabulary
__version__ = "0.5.5"

__all__ = [
    "Catalog", "SearchHit",
    "IngestResult", "ingest_file", "ingest_text",
    "Link", "LinkPolicy", "build_links", "link_collisions", "links_for",
    "ContentType", "TextStrataItem", "HandlingMode", "PreservationMode", "Provenance",
    "TextStrataStore",
    "ValidationResult", "validate",
    "Skin", "RenderContext", "PAPER_SKIN", "CONSOLE_SKIN", "render_hugo_item", "render_hugo_page", "render_item_html", "render_library_index", "render_text", "render_tui_item",
    "KnowledgeScore", "SimilarityPolicy", "SimilarityEdge", "build_similarity_edges", "build_tfidf", "score_corpus",
    "SynonymProposal", "canonical_tokens", "infer_synonyms", "load_synonyms", "stem",
    "__version__",
]
