/**
 * LLM Wiki — Compile Judgment Types
 *
 * When the LLM compiles collected sources into wiki pages, it must first
 * judge whether to CREATE a new page or UPDATE an existing one.
 *
 * This module defines the structured output format for that judgment,
 * including confidence scoring and reasoning traces.
 *
 * Design decisions:
 * - Discriminated union on `action` field (CreatePage | UpdatePage)
 * - Confidence score (0–1) lets the user set approval thresholds
 * - `reasoning` array provides transparent LLM decision traces
 * - `matchedPages` in UpdatePage shows which existing pages were considered
 * - Obsidian-native: paths are vault-relative, use [[wikilink]] format
 * - No "skip" action — if content is irrelevant, it stays in sources/
 */

import { z } from "zod";

// ============================================================================
// Confidence Score
// ============================================================================

/**
 * How confident the LLM is in this judgment (0–1).
 *
 * Suggested thresholds (user-configurable):
 * - >= 0.8: auto-approve (high confidence)
 * - 0.5–0.8: show preview, ask for confirmation
 * - < 0.5: flag for manual review
 */
export const ConfidenceScore = z.number().min(0).max(1);
export type ConfidenceScore = z.infer<typeof ConfidenceScore>;

// ============================================================================
// Reasoning — transparent decision trace
// ============================================================================

/**
 * A single reasoning step in the judgment process.
 * Provides an audit trail for why the LLM chose create vs update.
 */
export const JudgmentReasoning = z.object({
  /** What aspect was evaluated */
  factor: z.string().describe(
    "The evaluation factor, e.g. 'topic_overlap', 'title_similarity', " +
    "'content_freshness', 'existing_page_coverage'"
  ),

  /** What the LLM concluded for this factor */
  conclusion: z.string().describe(
    "Brief conclusion for this factor. " +
    "E.g., 'Existing page covers 60% of source topics'"
  ),

  /**
   * How much this factor influenced the final decision.
   * Higher = more influential.
   */
  weight: z.number().min(0).max(1).default(0.5),
});
export type JudgmentReasoning = z.infer<typeof JudgmentReasoning>;

// ============================================================================
// Matched Page — existing pages considered during judgment
// ============================================================================

/**
 * An existing wiki page that was compared against during the judgment.
 * Only relevant for UpdatePage judgments, but may appear in CreatePage
 * to explain why existing pages were rejected as update targets.
 */
export const MatchedPage = z.object({
  /** Vault-relative path (e.g., "topics/ai-research/transformers.md") */
  path: z.string(),

  /** Page title from frontmatter or filename */
  title: z.string(),

  /**
   * Semantic similarity between source content and this page (0–1).
   * Based on topic overlap, not literal text matching.
   */
  similarity: ConfidenceScore,

  /** Why this page was or wasn't chosen as the update target */
  reason: z.string().optional(),
});
export type MatchedPage = z.infer<typeof MatchedPage>;

// ============================================================================
// CreatePage Judgment
// ============================================================================

/**
 * LLM judges that a NEW wiki page should be created.
 *
 * This happens when:
 * - No existing page covers the source's topic sufficiently
 * - The source introduces a genuinely new concept/topic
 * - Existing pages are tangentially related but not a good merge target
 */
export const CreatePageJudgment = z.object({
  /** Discriminator */
  action: z.literal("create"),

  /** Confidence that creating is the right action (0–1) */
  confidence: ConfidenceScore,

  /**
   * Proposed vault-relative path for the new page.
   * Follows lens's wikiDirectory setting.
   *
   * Example: "topics/ai-research/mixture-of-experts.md"
   */
  targetPath: z.string().describe(
    "Vault-relative path for the new page. " +
    "Must end in .md and follow the lens's wikiDirectory convention."
  ),

  /** Proposed page title (used in frontmatter `title:`) */
  proposedTitle: z.string().min(1),

  /**
   * Existing pages that were considered but rejected as update targets.
   * Empty if no related pages exist in the vault.
   */
  consideredPages: z.array(MatchedPage).default([]),

  /**
   * Step-by-step reasoning for the create decision.
   * Minimum 1 reasoning step required.
   */
  reasoning: z.array(JudgmentReasoning).min(1),

  /**
   * Suggested [[wikilinks]] to existing pages that should be
   * cross-referenced from the new page.
   */
  suggestedLinks: z.array(z.string()).default([]).describe(
    "Wikilink targets (without [[ ]]) to existing pages. " +
    "E.g., ['transformers', 'attention-mechanism']"
  ),

  /**
   * Suggested #tags for the new page frontmatter.
   * Merged with the lens's defaultTags at compile time.
   */
  suggestedTags: z.array(z.string()).default([]),

  /** ID of the lens driving this compilation */
  lensId: z.string().optional(),

  /** Source file paths (vault-relative, under sources/) that feed this judgment */
  sourceRefs: z.array(z.string()).default([]),
});
export type CreatePageJudgment = z.infer<typeof CreatePageJudgment>;

// ============================================================================
// UpdatePage Judgment
// ============================================================================

/**
 * LLM judges that an EXISTING wiki page should be updated.
 *
 * This happens when:
 * - An existing page's topic substantially overlaps with the source
 * - New information extends or refines an existing page
 * - The source provides updates to a tracked topic
 */
export const UpdatePageJudgment = z.object({
  /** Discriminator */
  action: z.literal("update"),

  /** Confidence that updating is the right action (0–1) */
  confidence: ConfidenceScore,

  /**
   * Vault-relative path of the page to update.
   * Must be an existing file in the vault.
   *
   * Example: "topics/ai-research/transformers.md"
   */
  targetPath: z.string().describe(
    "Vault-relative path of the existing page to update."
  ),

  /** Title of the existing page being updated */
  existingTitle: z.string(),

  /**
   * How the page should be updated.
   *
   * - "extend": Add new sections or expand existing ones
   * - "revise": Update outdated information with newer data
   * - "append": Add new content at the end (e.g., timeline entries)
   * - "refine": Improve clarity/accuracy without major structural changes
   */
  updateMode: z.enum(["extend", "revise", "append", "refine"]),

  /**
   * Which sections of the existing page are affected.
   * Empty = the LLM will determine during compilation.
   */
  affectedSections: z.array(z.string()).default([]).describe(
    "Heading names of sections to update. E.g., ['Architecture', 'Performance']"
  ),

  /**
   * The best-matching page and any runner-up candidates.
   * First element is the chosen target.
   */
  matchedPages: z.array(MatchedPage).min(1),

  /**
   * Step-by-step reasoning for the update decision.
   * Minimum 1 reasoning step required.
   */
  reasoning: z.array(JudgmentReasoning).min(1),

  /**
   * New [[wikilinks]] to add during the update.
   * Links already present in the page are excluded.
   */
  newLinks: z.array(z.string()).default([]).describe(
    "New wikilink targets to add. E.g., ['mixture-of-experts']"
  ),

  /**
   * New #tags to add to the page frontmatter.
   * Tags already present are excluded.
   */
  newTags: z.array(z.string()).default([]),

  /** ID of the lens driving this compilation */
  lensId: z.string().optional(),

  /** Source file paths (vault-relative, under sources/) that feed this judgment */
  sourceRefs: z.array(z.string()).default([]),
});
export type UpdatePageJudgment = z.infer<typeof UpdatePageJudgment>;

// ============================================================================
// Union — the compile judgment result
// ============================================================================

/**
 * Discriminated union: the LLM's judgment on how to compile a source.
 * Use `action` field to narrow: "create" | "update".
 *
 * Example usage:
 * ```ts
 * const judgment = CompileJudgment.parse(llmOutput);
 * if (judgment.action === "create") {
 *   // judgment.targetPath — new page to create
 *   // judgment.proposedTitle — page title
 * } else {
 *   // judgment.targetPath — existing page to update
 *   // judgment.updateMode — how to update
 * }
 * ```
 */
export const CompileJudgment = z.discriminatedUnion("action", [
  CreatePageJudgment,
  UpdatePageJudgment,
]);
export type CompileJudgment = z.infer<typeof CompileJudgment>;

// ============================================================================
// Batch judgment — multiple sources judged together
// ============================================================================

/**
 * Result of judging multiple sources in a single LLM call.
 * Used when compiling a batch of collected sources for a lens.
 */
export const BatchCompileJudgment = z.object({
  /** The lens these judgments are for */
  lensId: z.string(),

  /** Individual judgments, one per source (or group of related sources) */
  judgments: z.array(CompileJudgment),

  /** Total sources evaluated */
  sourcesEvaluated: z.number().int().min(0),

  /** Sources skipped (irrelevant to this lens) */
  sourcesSkipped: z.number().int().min(0).default(0),

  /** ISO 8601 timestamp of when this judgment was made */
  judgedAt: z.string().datetime(),
});
export type BatchCompileJudgment = z.infer<typeof BatchCompileJudgment>;

// ============================================================================
// Approval thresholds — user-configurable
// ============================================================================

/**
 * User-configurable thresholds for auto-approval of judgments.
 * Stored in the vault's llm-wiki config.
 */
export const ApprovalThresholds = z.object({
  /**
   * Minimum confidence to auto-approve without user review.
   * Judgments below this threshold are queued for manual approval.
   */
  autoApprove: z.number().min(0).max(1).default(0.8),

  /**
   * Below this threshold, the judgment is flagged as low-confidence
   * and requires explicit user confirmation before proceeding.
   */
  requireReview: z.number().min(0).max(1).default(0.5),
}).refine(
  (t: { autoApprove: number; requireReview: number }) => t.autoApprove > t.requireReview,
  { message: "autoApprove threshold must be higher than requireReview" }
);
export type ApprovalThresholds = z.infer<typeof ApprovalThresholds>;
