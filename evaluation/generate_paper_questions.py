#!/usr/bin/env python3
"""Generate eval Q&A pairs from Growth Lab research papers (PDFs).

Three-pass pipeline:
1. Claim extraction — send PDF to Claude, extract trade/complexity claims
2. Question generation — generate Q&A pairs scoped to trade & economic complexity
3. Category mapping — map generated questions to existing eval categories

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py \
        --papers docs/resources/trade_and_complexity/2022-03-cid-wp-410-namibia-economic-complexity-report.pdf
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py --paper-dir docs/resources/theory/
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py --dry-run
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py --max-questions-per-paper 15
    PYTHONPATH=$(pwd) uv run python evaluation/generate_paper_questions.py --model claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic
from anthropic.types import Message
from dotenv import load_dotenv
from utils import EVALUATION_BASE_DIR, load_json_file, save_json_file

# Load .env from project root so API keys are available
load_dotenv(EVALUATION_BASE_DIR.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_PAPER_DIR = (
    EVALUATION_BASE_DIR.parent / "docs" / "resources" / "trade_and_complexity"
)
_STAGING_FILE = EVALUATION_BASE_DIR / "paper_questions_staging.json"

# Per-1M-token pricing (from src/model_config.py)
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_per_1M, output_per_1M)
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-6-20260217": (3.00, 15.00),
    "claude-opus-4-6-20260204": (5.00, 25.00),
}
_DEFAULT_PRICING = (1.00, 5.00)


@dataclass
class UsageTracker:
    """Accumulate token usage and estimate cost across API calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    _by_pass: dict[str, dict[str, int | str]] = field(default_factory=dict)

    def record(self, message: Message, pass_name: str = "", model: str = "") -> None:
        """Record usage from an Anthropic API response."""
        self.input_tokens += message.usage.input_tokens
        self.output_tokens += message.usage.output_tokens
        self.api_calls += 1
        if pass_name:
            entry = self._by_pass.setdefault(
                pass_name, {"input": 0, "output": 0, "model": model}
            )
            entry["input"] += message.usage.input_tokens
            entry["output"] += message.usage.output_tokens

    def cost_usd(self) -> float:
        """Estimate total cost in USD (sum of per-pass costs)."""
        total = 0.0
        for tok in self._by_pass.values():
            inp_price, out_price = _PRICING.get(str(tok.get("model", "")), _DEFAULT_PRICING)
            total += (tok["input"] * inp_price + tok["output"] * out_price) / 1_000_000
        return total

    def summary(self) -> str:
        """Return a human-readable cost summary."""
        lines = [
            f"  API calls:     {self.api_calls}",
            f"  Input tokens:  {self.input_tokens:,}",
            f"  Output tokens: {self.output_tokens:,}",
            f"  Est. cost:     ${self.cost_usd():.4f}",
        ]
        if self._by_pass:
            lines.append("  By pass:")
            for name, tok in self._by_pass.items():
                model = str(tok.get("model", ""))
                inp_price, out_price = _PRICING.get(model, _DEFAULT_PRICING)
                pass_cost = (
                    tok["input"] * inp_price + tok["output"] * out_price
                ) / 1_000_000
                model_short = model.split("-")[1] if "-" in model else model
                lines.append(
                    f"    {name} ({model_short}): {tok['input']:,} in / "
                    f"{tok['output']:,} out (${pass_cost:.4f})"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Category list (loaded once from eval_questions.json)
# ---------------------------------------------------------------------------


def _load_categories() -> list[dict]:
    """Load category definitions from eval_questions.json."""
    data = load_json_file(EVALUATION_BASE_DIR / "eval_questions.json")
    return data["categories"]


def _get_next_question_id() -> int:
    """Find the next available question ID across eval and staging files."""
    max_id = 0

    # Check eval_questions.json
    eval_path = EVALUATION_BASE_DIR / "eval_questions.json"
    if eval_path.exists():
        data = load_json_file(eval_path)
        for q in data.get("questions", []):
            max_id = max(max_id, q["id"])

    # Check staging file
    if _STAGING_FILE.exists():
        data = load_json_file(_STAGING_FILE)
        for q in data.get("questions", []):
            max_id = max(max_id, q["id"])

    return max_id + 1


# ---------------------------------------------------------------------------
# Pass 1: Claim Extraction
# ---------------------------------------------------------------------------

_CLAIM_EXTRACTION_PROMPT = """\
You are an expert analyst of international trade and economic complexity research.

Analyze this Growth Lab research paper and extract claims that make \
**factual statements about international trade and economic complexity**.

**OUT OF SCOPE — do NOT extract claims that are not about trade and economic complexity \
, such as these topics:**
- Infrastructure constraints (land, electricity, water, ports, roads, logistics)
- Governance, institutions, regulation, business environment, corruption
- Labor markets, wages, employment, human capital, education
- Fiscal policy, taxation, public spending, debt
- FDI flows, investment climate, capital markets
- Health, demographics, urbanization, migration
- Normative policy recommendations ("the government should...")
- Theoretical/methodological explanations (how ECI is calculated, what RCA means)
- Theoretical principles or conceptual frameworks (e.g., "countries diversify into \
nearby products" — this is theory, not an empirical claim)
- Data source descriptions or methodology adjustments

The key filter: **Does this claim make an empirical, verifiable statement about trade \
or economic complexity — with specific countries, products, numbers, or time periods?** \
If yes, extract it. If it is a theoretical principle, conceptual explanation, or about \
a non-trade topic, skip it.

For each claim, extract:
- `text`: The full claim as stated in the paper
- `supporting_quote`: The exact quote or passage from the paper
- `data_points`: Structured data points mentioned (metric, value, year, country, product \
where applicable)
- `category_hint`: A rough topic area (e.g., "export composition", "ECI ranking", \
"diversification", "trade partners", "growth trends", "product space", "RCA", \
"trade balance", "services trade")
- `complexity_level`: "medium" if it involves a single comparison or trend, "hard" if it \
involves multi-step reasoning, multiple countries, or surprising/counterintuitive findings

Also assess the paper's overall relevance to trade and economic complexity:
- `trade_relevance`: "high" if the paper is primarily about trade/complexity analysis, \
"medium" if trade data supports a broader argument, "low" if trade is tangential

Respond with JSON in this exact format:
{
  "paper_title": "...",
  "paper_year": "...",
  "country_focus": ["..."],
  "trade_relevance": "high" | "medium" | "low",
  "claims": [
    {
      "text": "...",
      "supporting_quote": "...",
      "data_points": [
        {"metric": "...", "value": "...", "year": "...", "country": "...", "product": "..."}
      ],
      "category_hint": "...",
      "complexity_level": "medium" | "hard"
    }
  ]
}

Extract as many trade/complexity claims as the paper contains. A paper focused on economic \
complexity may have 20-30 claims. A growth diagnostics paper with some trade data may have \
only 3-5 trade-relevant claims. Return an empty claims array if no trade/complexity claims \
are found."""


def _pdf_page_count(pdf_path: Path) -> int | None:
    """Return the number of pages in a PDF, or None if we can't determine it."""
    try:
        import pypdf

        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages)
    except Exception:
        # If pypdf isn't available or fails, return None (proceed cautiously)
        return None


_MAX_PDF_PAGES = 100


async def extract_claims(
    pdf_path: Path,
    client: AsyncAnthropic,
    model: str,
    tracker: UsageTracker | None = None,
) -> dict:
    """Send PDF to Claude and extract structured claims."""
    page_count = _pdf_page_count(pdf_path)
    if page_count is not None and page_count > _MAX_PDF_PAGES:
        raise ValueError(
            f"PDF has {page_count} pages (max {_MAX_PDF_PAGES}). Skipping."
        )

    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    message = await client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {"type": "text", "text": _CLAIM_EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    if tracker:
        tracker.record(message, "claim_extraction", model=model)

    # Extract JSON from response
    text = message.content[0].text
    return _parse_json_response(text)


# ---------------------------------------------------------------------------
# Pass 2: Question Generation
# ---------------------------------------------------------------------------

_QUESTION_GENERATION_PROMPT = """\
You are generating evaluation questions for a trade and economic complexity Q&A system \
called "Ask Atlas" (powered by the Atlas of Economic Complexity). Below are claims about \
trade and economic complexity extracted from a Growth Lab research paper.

Your job: generate Q&A pairs that a user of this system might naturally ask. The system \
answers natural language questions about international trade and economic complexity.

**SCOPE — only generate questions about trade or economic complexity metrics and \
implications**

**DO NOT generate questions that are NOT verifiable emprical questions about trade \
and economic complexity, such as these topics:**
- Infrastructure (land, electricity, water, ports, roads)
- Governance, institutions, regulation, business environment
- Labor, wages, employment, education, human capital
- Fiscal policy, investment climate, FDI
- Policy recommendations or normative judgments (however, policy recommendations \
    are fine if they are a natural extension of a certain empirical verifiable claim)

**Question quality guidelines:**
- Phrase questions as a real user would ask — conversational, not academic
- NEVER reference the paper — no "According to the paper..." or "The study found..."
- Prioritize surprising or non-obvious findings from the paper — insights a user \
wouldn't arrive at without the research
- Prioritize analytical depth: comparisons, trends, multi-step reasoning
- Avoid simple single-number lookups like "What is X's ECI?" — aim for synthesis
- Each question must be self-contained

**How many questions to generate:**
You decide based on how much trade/complexity content the claims contain:
- Paper with trade_relevance "high": generate up to {max_questions} questions
- Paper with trade_relevance "medium": generate 3-8 questions (only the best ones)
- Paper with trade_relevance "low": generate 0-3 questions (only if truly trade-relevant)
- Generate 0 questions if none of the claims warrant a trade/complexity question
It is completely fine to return an empty array.

For each Q&A pair, provide:
- `question`: The question text
- `answer`: A comprehensive answer synthesized from the claims
- `supporting_quotes`: List of direct quotes from the paper supporting the answer
- `data_points`: Structured data points used in the answer
- `difficulty`: "medium" for single-comparison or single-trend questions, \
"hard" for multi-step, multi-country, or counterintuitive findings
- `confidence`: "high" if the answer is directly stated in the paper with specific numbers, \
"medium" if it requires some inference from the paper's data

Claims from the paper:
{claims_json}

Paper metadata:
- Title: {paper_title}
- Year: {paper_year}
- Country focus: {country_focus}
- Trade relevance: {trade_relevance}

Respond with JSON:
{{
  "qa_pairs": [
    {{
      "question": "...",
      "answer": "...",
      "supporting_quotes": ["..."],
      "data_points": [{{"metric": "...", "value": "...", "year": "...", "country": "...", \
"product": "..."}}],
      "difficulty": "medium" | "hard",
      "confidence": "high" | "medium"
    }}
  ]
}}"""


async def generate_questions(
    claims: dict,
    client: AsyncAnthropic,
    model: str,
    max_questions: int,
    tracker: UsageTracker | None = None,
) -> list[dict]:
    """Generate Q&A pairs from extracted claims."""
    prompt = _QUESTION_GENERATION_PROMPT.format(
        claims_json=json.dumps(claims["claims"], indent=2),
        paper_title=claims.get("paper_title", "Unknown"),
        paper_year=claims.get("paper_year", "Unknown"),
        country_focus=", ".join(claims.get("country_focus", [])),
        trade_relevance=claims.get("trade_relevance", "medium"),
        max_questions=max_questions,
    )

    message = await client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    if tracker:
        tracker.record(message, "question_generation", model=model)

    text = message.content[0].text
    result = _parse_json_response(text)
    return result.get("qa_pairs", [])


# ---------------------------------------------------------------------------
# Pass 3: Category Mapping
# ---------------------------------------------------------------------------

_CATEGORY_MAPPING_PROMPT = """\
You are classifying evaluation questions into categories. For each question below, \
assign the single best-fit category from the list provided.

Available categories:
{categories_json}

Questions to classify:
{questions_json}

For each question, respond with JSON:
{{
  "mappings": [
    {{
      "question_index": 0,
      "category_id": "...",
      "confidence": "high" | "medium"
    }}
  ]
}}

If no existing category fits well, use the closest match and set confidence to "medium".
Prefer the non-"cp_" and non-"explore_" category variants (e.g., use "economic_complexity" \
over "cp_economic_complexity") unless the question is specifically about Atlas country page \
or explore page features."""


async def map_categories(
    questions: list[dict],
    categories: list[dict],
    client: AsyncAnthropic,
    model: str,
    tracker: UsageTracker | None = None,
) -> list[str]:
    """Map generated questions to existing eval categories."""
    prompt = _CATEGORY_MAPPING_PROMPT.format(
        categories_json=json.dumps(categories, indent=2),
        questions_json=json.dumps(
            [{"index": i, "question": q["question"]} for i, q in enumerate(questions)],
            indent=2,
        ),
    )

    message = await client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    if tracker:
        tracker.record(message, "category_mapping", model=model)

    text = message.content[0].text
    result = _parse_json_response(text)

    # Build index → category_id mapping
    mapping = {}
    for m in result.get("mappings", []):
        mapping[m["question_index"]] = m["category_id"]

    # Return ordered list of category IDs
    return [mapping.get(i, "complex_stress_test") for i in range(len(questions))]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> dict:
    """Extract JSON from an LLM response that may contain markdown fences."""
    # Try to find JSON within code fences first
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    # If no fences, try to find the outermost JSON object
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Find first { and last } as fallback
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1:
            return json.loads(text[first_brace : last_brace + 1])
        raise


def _build_question_entry(
    qa: dict,
    question_id: int,
    category_id: str,
    paper_title: str,
    paper_year: str,
    pdf_filename: str,
) -> dict:
    """Build a question entry for the staging file."""
    return {
        "id": question_id,
        "category_id": category_id,
        "difficulty": qa.get("difficulty", "medium"),
        "text": qa["question"],
        "source": "growth_lab_paper",
        "source_paper": paper_title,
        "source_pdf": pdf_filename,
        "paper_ground_truth": {
            "answer": qa["answer"],
            "supporting_quotes": qa.get("supporting_quotes", []),
            "data_points": qa.get("data_points", []),
            "confidence": qa.get("confidence", "medium"),
            "paper_title": paper_title,
            "paper_year": paper_year,
        },
    }


def _save_paper_research_ground_truth(
    question_id: int, qa: dict, paper_meta: dict
) -> Path:
    """Save paper research ground truth to results/{qid}/ground_truth/paper_research.json."""
    gt_path = (
        EVALUATION_BASE_DIR
        / "results"
        / str(question_id)
        / "ground_truth"
        / "paper_research.json"
    )
    gt_data = {
        "question_id": str(question_id),
        "source": "growth_lab_paper",
        "timestamp": datetime.now(UTC).isoformat(),
        "paper_title": paper_meta.get("paper_title", "Unknown"),
        "paper_year": paper_meta.get("paper_year", "Unknown"),
        "source_paper": paper_meta.get("pdf_filename", "Unknown"),
        "research_answer": qa["answer"],
        "supporting_quotes": qa.get("supporting_quotes", []),
        "data_points": qa.get("data_points", []),
        "confidence": qa.get("confidence", "medium"),
    }
    save_json_file(gt_path, gt_data)
    return gt_path


# ---------------------------------------------------------------------------
# Main pipeline: process one paper
# ---------------------------------------------------------------------------


async def process_paper(
    pdf_path: Path,
    client: AsyncAnthropic,
    model: str,
    categories: list[dict],
    max_questions: int,
    tracker: UsageTracker | None = None,
    claim_model: str | None = None,
    qa_model: str | None = None,
) -> list[dict]:
    """Run the full three-pass pipeline on a single PDF.

    Returns question entries WITHOUT IDs assigned. The caller assigns IDs
    after all papers are processed (to support parallel execution).

    Args:
        pdf_path: Path to the PDF file.
        client: Anthropic API client.
        model: Default model to use for all passes.
        categories: Category definitions from eval_questions.json.
        max_questions: Maximum number of Q&A pairs (LLM decides actual count).
        tracker: Optional usage tracker for cost estimation.
        claim_model: Override model for Pass 1 (claim extraction). Defaults to model.
        qa_model: Override model for Pass 2+3 (question gen + category mapping).

    Returns:
        List of generated question entries (without IDs).
    """
    p1_model = claim_model or model
    p23_model = qa_model or model

    pdf_filename = pdf_path.name
    log.info(f"Processing: {pdf_filename}")

    # Pass 1: Claim extraction
    log.info(f"  Pass 1: Extracting claims from {pdf_filename} ({p1_model})...")
    try:
        claims = await extract_claims(pdf_path, client, p1_model, tracker)
    except Exception as e:
        log.error(f"  Claim extraction failed for {pdf_filename}: {e}")
        return []

    paper_title = claims.get("paper_title", "Unknown")
    trade_relevance = claims.get("trade_relevance", "unknown")
    num_claims = len(claims.get("claims", []))
    log.info(
        f"  Extracted {num_claims} claims "
        f"(title: {paper_title}, "
        f"year: {claims.get('paper_year', 'N/A')}, "
        f"relevance: {trade_relevance})"
    )

    if num_claims == 0:
        log.info(f"  No trade/complexity claims found in {pdf_filename}, skipping.")
        return []

    # Pass 2: Question generation (LLM decides how many based on relevance)
    log.info(f"  Pass 2: Generating Q&A pairs (up to {max_questions}, {p23_model})...")
    try:
        qa_pairs = await generate_questions(
            claims, client, p23_model, max_questions, tracker
        )
    except Exception as e:
        log.error(f"  Question generation failed for {pdf_filename}: {e}")
        return []

    log.info(f"  Generated {len(qa_pairs)} Q&A pairs")

    if not qa_pairs:
        log.info(f"  LLM generated 0 questions for {pdf_filename} (low relevance).")
        return []

    # Pass 3: Category mapping
    log.info(f"  Pass 3: Mapping categories ({p23_model})...")
    try:
        category_ids = await map_categories(
            qa_pairs, categories, client, p23_model, tracker
        )
    except Exception as e:
        log.error(f"  Category mapping failed for {pdf_filename}: {e}")
        # Fallback: assign all to complex_stress_test
        category_ids = ["complex_stress_test"] * len(qa_pairs)

    # Build output entries WITHOUT IDs (caller assigns IDs later)
    paper_meta = {
        "paper_title": paper_title,
        "paper_year": claims.get("paper_year", "Unknown"),
        "pdf_filename": pdf_filename,
    }

    entries = []
    for qa, cat_id in zip(qa_pairs, category_ids):
        entry = _build_question_entry(
            qa=qa,
            question_id=-1,  # placeholder — assigned by caller
            category_id=cat_id,
            paper_title=paper_title,
            paper_year=paper_meta["paper_year"],
            pdf_filename=pdf_filename,
        )
        # Stash raw Q&A data and paper_meta for ground truth saving later
        entry["_qa_raw"] = qa
        entry["_paper_meta"] = paper_meta
        entries.append(entry)

    log.info(f"  Done: {len(entries)} questions generated from {pdf_filename}")
    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_TEST_FILE = EVALUATION_BASE_DIR / "paper_questions_test.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate eval Q&A pairs from Growth Lab research papers"
    )
    parser.add_argument(
        "--papers",
        nargs="+",
        type=Path,
        help="One or more specific PDF paths to process",
    )
    parser.add_argument(
        "--paper-dir",
        type=Path,
        default=_DEFAULT_PAPER_DIR,
        help=f"Directory to scan for PDFs (default: {_DEFAULT_PAPER_DIR})",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=(
            "Save results to a separate test file (paper_questions_test.json) "
            "instead of the staging file. Overwrites on each run."
        ),
    )
    parser.add_argument(
        "--max-questions-per-paper",
        type=int,
        default=20,
        help="Maximum Q&A pairs per paper; LLM decides actual count (default: 20)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=_DEFAULT_MODEL,
        help=f"Default model for all passes (default: {_DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--claim-model",
        type=str,
        default=None,
        help="Model for Pass 1 (claim extraction / PDF reading). Overrides --model.",
    )
    parser.add_argument(
        "--qa-model",
        type=str,
        default=None,
        help="Model for Pass 2+3 (question generation + category mapping). Overrides --model.",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=None,
        help="Starting question ID (default: auto-detect next available)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output file (default: {_STAGING_FILE}, or test file with --test)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent paper processing (default: 3)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    model = args.model
    is_test = args.test

    # Determine output path
    if args.output:
        output_path = args.output
    elif is_test:
        output_path = _TEST_FILE
    else:
        output_path = _STAGING_FILE

    claim_model = args.claim_model
    qa_model = args.qa_model

    log.info("=" * 60)
    log.info("Paper Q&A Generator")
    log.info(f"  Default model: {model}")
    if claim_model:
        log.info(f"  Pass 1 model:  {claim_model}")
    if qa_model:
        log.info(f"  Pass 2+3 model: {qa_model}")
    log.info(f"  Concurrency:   {args.concurrency}")
    if is_test:
        log.info(f"  Mode:  TEST (saving to {output_path.name})")
    else:
        log.info(f"  Mode:  STAGING (appending to {output_path.name})")
    log.info("=" * 60)

    # Resolve PDF list
    if args.papers:
        pdf_paths = [p.resolve() for p in args.papers]
        for p in pdf_paths:
            if not p.exists():
                log.error(f"PDF not found: {p}")
                return
    else:
        paper_dir = args.paper_dir.resolve()
        if not paper_dir.exists():
            log.error(f"Paper directory not found: {paper_dir}")
            return
        pdf_paths = sorted(paper_dir.glob("*.pdf"))

    if not pdf_paths:
        log.info("No PDF files found. Done.")
        return

    log.info(f"Found {len(pdf_paths)} PDF(s) to process:")
    for p in pdf_paths:
        log.info(f"  {p.name}")

    # Load categories
    categories = _load_categories()
    log.info(f"Loaded {len(categories)} eval categories")

    # Process all papers concurrently
    client = AsyncAnthropic()
    tracker = UsageTracker()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def _process_one(pdf_path: Path) -> list[dict]:
        async with semaphore:
            return await process_paper(
                pdf_path=pdf_path,
                client=client,
                model=model,
                categories=categories,
                max_questions=args.max_questions_per_paper,
                tracker=tracker,
                claim_model=claim_model,
                qa_model=qa_model,
            )

    # Launch all papers in parallel, collect results
    paper_results = await asyncio.gather(
        *[_process_one(pdf_path) for pdf_path in pdf_paths]
    )

    # Flatten and assign sequential IDs
    start_id = args.start_id or _get_next_question_id()
    all_entries: list[dict] = []
    current_id = start_id
    for entries in paper_results:
        for entry in entries:
            entry["id"] = current_id
            if is_test:
                # Test mode: don't write individual ground truth files
                entry.pop("_qa_raw")
                entry.pop("_paper_meta")
            else:
                _save_paper_research_ground_truth(
                    current_id, entry.pop("_qa_raw"), entry.pop("_paper_meta")
                )
            all_entries.append(entry)
            current_id += 1

    if not all_entries:
        log.info("No questions generated. Done.")
        log.info("Cost summary:")
        log.info(tracker.summary())
        return

    # Save results
    if is_test:
        # Test mode: overwrite the test file each run
        save_json_file(output_path, {"questions": all_entries})
    else:
        # Staging mode: append to existing file
        if output_path.exists():
            staging_data = load_json_file(output_path)
        else:
            staging_data = {"questions": []}
        staging_data["questions"].extend(all_entries)
        save_json_file(output_path, staging_data)

    log.info(f"Saved {len(all_entries)} questions to {output_path}")

    # Print summary of generated questions
    log.info("=" * 60)
    log.info(f"Generated {len(all_entries)} questions from {len(pdf_paths)} paper(s):")
    for entry in all_entries:
        log.info(
            f"  Q{entry['id']} [{entry['category_id']}] ({entry['difficulty']}) "
            f"— {entry['text'][:100]}"
        )
    log.info("Cost summary:")
    log.info(tracker.summary())
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
