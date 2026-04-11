/**
 * LLM Wiki — Lens Data Model
 *
 * A Lens is a user-defined interest category / perspective that shapes
 * how collected sources are compiled into wiki pages.
 *
 * Design decisions:
 * - Lenses live as YAML files in the vault: `lenses/<id>.yml`
 * - Each lens can reference multiple subscriptions (sources)
 * - `keywords` guide the LLM during compilation (semantic matching)
 * - `compileInstructions` give the user full control over LLM output
 * - Obsidian-native: `wikiDirectory` maps to vault subdirectory,
 *   `defaultTags` become #tags, wiki pages use [[wikilinks]]
 * - YAML chosen over JSON for human-editability in Obsidian
 */

import { z } from "zod";

// ============================================================================
// Lens ID — slug format
// ============================================================================

/** Lowercase slug: letters, digits, hyphens only */
export const LensId = z
  .string()
  .regex(/^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/, {
    message: "Lens ID must be a lowercase slug (e.g. 'ai-research', 'frontend')",
  });
export type LensId = z.infer<typeof LensId>;

// ============================================================================
// Compile Strategy — how sources are turned into wiki pages
// ============================================================================

/**
 * Controls how the LLM processes collected sources for this lens.
 *
 * - "merge": Multiple sources are synthesized into a single wiki page
 *   per topic (default — produces richer, interconnected wiki entries)
 * - "per-source": Each source becomes its own wiki page
 *   (better for reference-heavy lenses like paper reviews)
 * - "append": New content is appended to existing wiki pages
 *   (good for running logs like "weekly AI news")
 */
export const CompileStrategy = z.enum(["merge", "per-source", "append"]);
export type CompileStrategy = z.infer<typeof CompileStrategy>;

// ============================================================================
// Lens Schema — full data model
// ============================================================================

export const LensSchema = z.object({
  /** Unique slug identifier (e.g., "ai-research", "frontend-ecosystem") */
  id: LensId,

  /** Human-readable display name */
  name: z.string().min(1),

  /**
   * What this lens covers — guides the LLM during compilation.
   * A good description helps the LLM decide:
   * 1. Which sources are relevant to this lens
   * 2. What angle/depth to use when writing wiki pages
   * 3. How to link new content to existing wiki pages
   */
  description: z.string().optional(),

  /**
   * Semantic keywords for matching sources to this lens.
   * Used by the LLM to determine if a source belongs under this lens,
   * even if not explicitly tagged by the user.
   *
   * Example: lens "ai-research" might have keywords:
   * ["machine learning", "neural network", "transformer", "LLM", "deep learning"]
   */
  keywords: z.array(z.string()).default([]),

  /**
   * Obsidian #tags auto-applied to all wiki pages compiled under this lens.
   * These appear in the frontmatter `tags:` field of generated wiki pages.
   *
   * Example: ["ai", "research"] → pages get `tags: [ai, research]`
   */
  defaultTags: z.array(z.string()).default([]),

  /**
   * Vault subdirectory where compiled wiki pages are placed.
   * Relative to vault root. Defaults to the lens ID.
   *
   * Example: "topics/ai-research" → pages go to `<vault>/topics/ai-research/`
   */
  wikiDirectory: z.string().optional(),

  /**
   * How sources are compiled into wiki pages.
   * See CompileStrategy for details.
   */
  compileStrategy: CompileStrategy.default("merge"),

  /**
   * Free-form instructions for the LLM when compiling under this lens.
   * Gives users full control over the output style.
   *
   * Examples:
   * - "Write in academic style with citations"
   * - "Keep it practical — focus on code examples and gotchas"
   * - "Summarize in Korean, include original English terms in parentheses"
   * - "Compare with existing approaches mentioned in [[comparison-frameworks]]"
   */
  compileInstructions: z.string().optional(),

  /**
   * IDs of subscriptions that feed into this lens.
   * A subscription can belong to multiple lenses.
   * If empty, sources are matched by keywords only.
   */
  sourceIds: z.array(z.string()).default([]),

  /**
   * Priority for compilation ordering.
   * Lower = compiled first. Default 0.
   * Useful when one lens's output is referenced by another.
   */
  priority: z.number().int().min(0).default(0),

  /** Whether this lens is currently active for compilation */
  enabled: z.boolean().default(true),

  /** ISO 8601 timestamps */
  createdAt: z.string().datetime().optional(),
  updatedAt: z.string().datetime().optional(),
});
export type Lens = z.infer<typeof LensSchema>;

// ============================================================================
// Create / Update DTOs
// ============================================================================

/** Minimal input for creating a new lens */
export const CreateLens = z.object({
  id: LensId,
  name: z.string().min(1),
  description: z.string().optional(),
  keywords: z.array(z.string()).optional(),
  defaultTags: z.array(z.string()).optional(),
  wikiDirectory: z.string().optional(),
  compileStrategy: CompileStrategy.optional(),
  compileInstructions: z.string().optional(),
  sourceIds: z.array(z.string()).optional(),
  priority: z.number().int().min(0).optional(),
});
export type CreateLens = z.infer<typeof CreateLens>;

/** Partial update — all fields optional except id */
export const UpdateLens = CreateLens.partial().required({ id: true });
export type UpdateLens = z.infer<typeof UpdateLens>;

// ============================================================================
// YAML Serialization Format
// ============================================================================

/**
 * Lens files are stored as YAML in the vault: `lenses/<id>.yml`
 *
 * Example `lenses/ai-research.yml`:
 * ```yaml
 * id: ai-research
 * name: AI Research
 * description: >
 *   Latest developments in artificial intelligence,
 *   focusing on LLM architectures and training techniques.
 * keywords:
 *   - machine learning
 *   - transformer
 *   - LLM
 *   - deep learning
 *   - neural network
 * defaultTags:
 *   - ai
 *   - research
 * wikiDirectory: topics/ai-research
 * compileStrategy: merge
 * compileInstructions: >
 *   Write in clear technical prose. Include paper references
 *   where applicable. Link to related concepts with [[wikilinks]].
 * sourceIds:
 *   - 550e8400-e29b-41d4-a716-446655440001
 *   - 550e8400-e29b-41d4-a716-446655440002
 * priority: 0
 * enabled: true
 * createdAt: "2024-01-15T10:30:00Z"
 * updatedAt: "2024-01-20T14:00:00Z"
 * ```
 *
 * YAML conventions:
 * - Use `>` for multiline strings (description, compileInstructions)
 * - Lists use `- item` notation
 * - Timestamps in ISO 8601 with quotes
 * - File name matches lens ID: `<id>.yml`
 * - No anchors or aliases — keep it simple for Obsidian users
 */

/** YAML field order for consistent serialization */
export const LENS_YAML_FIELD_ORDER = [
  "id",
  "name",
  "description",
  "keywords",
  "defaultTags",
  "wikiDirectory",
  "compileStrategy",
  "compileInstructions",
  "sourceIds",
  "priority",
  "enabled",
  "createdAt",
  "updatedAt",
] as const;

/**
 * Directory name for lens YAML files inside the vault.
 * `<vault>/lenses/ai-research.yml`
 */
export const LENSES_DIR = "lenses";

/** File extension for lens files */
export const LENS_FILE_EXT = ".yml";

/**
 * Get the lens file path relative to vault root.
 * @example getLensFilePath("ai-research") → "lenses/ai-research.yml"
 */
export function getLensFilePath(lensId: string): string {
  return `${LENSES_DIR}/${lensId}${LENS_FILE_EXT}`;
}
