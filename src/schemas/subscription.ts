/**
 * LLM Wiki — Subscription Type Schemas
 *
 * Defines all source subscription types including the novel "natural-language"
 * subscription where users describe what they want to follow in plain language.
 *
 * Design decisions:
 * - Extracted from Nudget's subscription model, simplified for personal use
 * - No server-side infra — Claude Code orchestrates everything locally
 * - SQLite for state, Obsidian vault for output
 * - Natural-language subscriptions are first-class alongside URL-based ones
 */

import { z } from "zod";

// Re-export Lens types from dedicated module
export { LensSchema, type Lens, LensId } from "./lens";

// ============================================================================
// Source Types
// ============================================================================

/**
 * How content is discovered and fetched.
 * Maps loosely to Nudget's LookupStrategy + FetchMethod but simplified
 * for local-first personal use.
 */
export const SourceType = z.enum([
  "rss",             // RSS/Atom feed URL
  "youtube",         // YouTube channel or playlist
  "twitter",         // Twitter/X profile or search
  "webpage",         // Generic webpage (CSS selector or content-diff)
  "natural-language", // LLM-powered: user describes what to follow in plain text
]);
export type SourceType = z.infer<typeof SourceType>;

// ============================================================================
// Subscription Status
// ============================================================================

export const SubscriptionStatus = z.enum([
  "active",   // Actively collecting
  "paused",   // User paused
  "broken",   // Collection failed, needs attention
]);
export type SubscriptionStatus = z.infer<typeof SubscriptionStatus>;

// ============================================================================
// Collection Schedule
// ============================================================================

/**
 * Preset schedule aliases — simple enum for common patterns.
 * Kept for backward compatibility and quick setup.
 */
export const CollectionSchedule = z.enum([
  "hourly",
  "daily",
  "weekly",
  "manual",   // Only collect when user triggers
]);
export type CollectionSchedule = z.infer<typeof CollectionSchedule>;

/**
 * Cron expression pattern (5-field standard cron).
 *
 * Format: "minute hour day-of-month month day-of-week"
 *
 * Examples:
 *   - "0 * * * *" every hour on the hour
 *   - "0,30 * * * *" every 30 minutes
 *   - "0 9 * * 1" every Monday at 9:00 AM
 *   - "0 0 * * *" daily at midnight
 *
 * Validates basic structure (5 space-separated fields) but does NOT
 * validate semantic correctness (that is done at runtime by the scheduler).
 */
export const CronExpression = z.string()
  .regex(
    /^(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)$/,
    "Must be a valid 5-field cron expression (e.g., '*/30 * * * *')"
  )
  .describe("Standard 5-field cron expression: min hour dom month dow");
export type CronExpression = z.infer<typeof CronExpression>;

/**
 * Interval-based schedule — run every N minutes.
 * Simpler alternative to cron for straightforward periodic polling.
 */
export const IntervalMinutes = z.number()
  .int()
  .min(5, "Minimum interval is 5 minutes")
  .max(10080, "Maximum interval is 10080 minutes (1 week)")
  .describe("Polling interval in minutes (5–10080)");
export type IntervalMinutes = z.infer<typeof IntervalMinutes>;

/**
 * Full schedule configuration — supports three modes:
 *
 * 1. **Preset** (default): Simple enum — "hourly", "daily", "weekly", "manual"
 * 2. **Cron**: Standard 5-field cron expression for precise scheduling
 * 3. **Interval**: Run every N minutes for simple periodic polling
 *
 * Only one of `preset`, `cron`, or `intervalMinutes` should be set.
 * Resolution priority: cron > intervalMinutes > preset.
 *
 * Maps to SQLite `schedules` table:
 *   preset ->interval_minutes (hourly=60, daily=1440, weekly=10080, manual=NULL)
 *   cron ->cron_expr column
 *   intervalMinutes ->interval_minutes column
 */
export const ScheduleConfig = z.object({
  /** Preset schedule alias — used when cron/interval are not set */
  preset: CollectionSchedule.default("daily"),

  /** Cron expression — takes priority over preset and interval */
  cron: CronExpression.optional(),

  /** Interval in minutes — takes priority over preset */
  intervalMinutes: IntervalMinutes.optional(),
}).refine(
  (data) => {
    // If both cron and intervalMinutes are set, that's ambiguous
    if (data.cron && data.intervalMinutes) {
      return false;
    }
    return true;
  },
  { message: "Cannot set both 'cron' and 'intervalMinutes' — use one or the other" }
);
export type ScheduleConfig = z.infer<typeof ScheduleConfig>;

/**
 * Map preset schedule to interval minutes.
 * Used by the scheduler to compute next_run_at.
 */
export const PRESET_INTERVAL_MINUTES: Record<CollectionSchedule, number | null> = {
  hourly: 60,
  daily: 1440,
  weekly: 10080,
  manual: null,  // No automatic scheduling
};

/**
 * Resolve a ScheduleConfig to its effective interval in minutes.
 * Returns null for manual schedules or cron-based (cron uses its own next-run logic).
 */
export function resolveIntervalMinutes(config: ScheduleConfig): number | null {
  if (config.cron) return null; // Cron uses its own computation
  if (config.intervalMinutes) return config.intervalMinutes;
  return PRESET_INTERVAL_MINUTES[config.preset];
}

// ============================================================================
// Base Subscription — shared fields across all types
// ============================================================================

const BaseSubscription = z.object({
  /** UUID — auto-generated by SQLite */
  id: z.string().uuid().optional(),
  /** Human-readable subscription name */
  name: z.string().min(1),
  /** Source type discriminator */
  type: SourceType,
  /** Active/paused/broken */
  status: SubscriptionStatus.default("active"),
  /**
   * How often to collect — supports three modes:
   *
   * Simple usage (backward compatible):
   *   schedule: { preset: "daily" }
   *
   * Cron-based:
   *   schedule: { preset: "daily", cron: "0 9 * * 1-5" }  // weekdays at 9am
   *
   * Interval-based:
   *   schedule: { preset: "daily", intervalMinutes: 120 }  // every 2 hours
   */
  schedule: ScheduleConfig.default({ preset: "daily" }),
  /** Lens this subscription feeds into */
  lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
  /** User-provided tags applied to all collected sources */
  tags: z.array(z.string()).default([]),
  /** Error message when status=broken */
  errorMessage: z.string().optional(),
  /** ISO 8601 timestamps */
  lastCollectedAt: z.string().datetime().optional(),
  createdAt: z.string().datetime().optional(),
  updatedAt: z.string().datetime().optional(),
});

// ============================================================================
// Type-specific subscription configs
// ============================================================================

/** RSS/Atom feed subscription */
export const RssSubscription = BaseSubscription.extend({
  type: z.literal("rss"),
  /** Feed URL */
  url: z.string().url(),
});
export type RssSubscription = z.infer<typeof RssSubscription>;

/** YouTube channel or playlist subscription */
export const YoutubeSubscription = BaseSubscription.extend({
  type: z.literal("youtube"),
  /** Channel or playlist URL */
  url: z.string().url(),
  /** Resolved channel ID (auto-detected) */
  channelId: z.string().optional(),
});
export type YoutubeSubscription = z.infer<typeof YoutubeSubscription>;

/** Twitter/X profile subscription */
export const TwitterSubscription = BaseSubscription.extend({
  type: z.literal("twitter"),
  /** Profile URL or @handle */
  handle: z.string().min(1),
  /** Include replies? */
  includeReplies: z.boolean().default(false),
});
export type TwitterSubscription = z.infer<typeof TwitterSubscription>;

/** Generic webpage monitoring (list page or content-diff) */
export const WebpageSubscription = BaseSubscription.extend({
  type: z.literal("webpage"),
  /** Page URL to monitor */
  url: z.string().url(),
  /** CSS selector for content area (optional — auto-detect if absent) */
  selector: z.string().optional(),
  /** Monitor mode */
  mode: z.enum(["list", "content-diff"]).default("list"),
});
export type WebpageSubscription = z.infer<typeof WebpageSubscription>;

// ============================================================================
// Natural-Language Subscription — the novel type
// ============================================================================

/**
 * A subscription defined by a natural-language query rather than a URL.
 *
 * The LLM interprets the query to:
 * 1. Determine appropriate search strategies (web search, specific sites, RSS discovery)
 * 2. Collect relevant sources matching the user's intent
 * 3. Filter and rank results based on relevance
 *
 * Examples:
 *   - "Latest research on transformer architecture improvements"
 *   - "React Server Components production experiences and gotchas"
 *   - "Indie hacker revenue milestones and growth strategies"
 *   - "Korean startup funding news in 2024"
 *
 * This is what makes llm-wiki different from a plain RSS reader —
 * the LLM acts as a personal research assistant that actively seeks
 * content matching your interests.
 */
export const NaturalLanguageSubscription = BaseSubscription.extend({
  type: z.literal("natural-language"),

  /** The user's description of what they want to follow */
  query: z.string().min(1).describe(
    "Plain-language description of the content interest. " +
    "E.g., 'AI papers on retrieval-augmented generation'"
  ),

  /**
   * Optional hints for where to look.
   * The LLM uses these as starting points but may discover additional sources.
   */
  searchHints: z.array(z.string()).default([]).describe(
    "Domains, sites, or keywords to prioritize. " +
    "E.g., ['arxiv.org', 'huggingface', 'AI conference proceedings']"
  ),

  /**
   * Content freshness preference.
   * Guides the LLM on how recent content should be.
   */
  freshness: z.enum([
    "latest",    // Only very recent (< 1 week)
    "recent",    // Past month
    "anytime",   // No time constraint
  ]).default("recent"),

  /**
   * Maximum sources to collect per run.
   * Prevents over-collection on broad queries.
   */
  maxPerRun: z.number().int().min(1).max(50).default(10),

  /**
   * Language preference for collected content.
   * null = any language.
   */
  language: z.enum(["en", "ko", "ja", "zh"]).optional().describe(
    "Preferred content language. Omit for any language."
  ),

  /**
   * LLM-generated refinement of the query after first collection.
   * The system may refine the query to improve relevance over time.
   * Users can review and approve refinements.
   */
  refinedQuery: z.string().optional().describe(
    "System-refined version of the query for better precision. " +
    "Set after first collection run, user can override."
  ),
});
export type NaturalLanguageSubscription = z.infer<typeof NaturalLanguageSubscription>;

// ============================================================================
// Union — any subscription type
// ============================================================================

/**
 * Discriminated union of all subscription types.
 * Use `type` field to narrow.
 */
export const Subscription = z.discriminatedUnion("type", [
  RssSubscription,
  YoutubeSubscription,
  TwitterSubscription,
  WebpageSubscription,
  NaturalLanguageSubscription,
]);
export type Subscription = z.infer<typeof Subscription>;

// ============================================================================
// Create DTOs — input schemas for creating subscriptions
// ============================================================================

/**
 * Schedule input for create/update operations.
 * Accepts either a simple preset string or a full ScheduleConfig object.
 *
 * Simple: schedule: "daily"
 * Full:   schedule: { preset: "daily", cron: "0 9 * * 1-5" }
 */
export const ScheduleInput = z.union([
  CollectionSchedule.transform((preset) => ({ preset } as ScheduleConfig)),
  ScheduleConfig,
]);
export type ScheduleInput = z.infer<typeof ScheduleInput>;

/** Create a new natural-language subscription (minimal required fields) */
export const CreateNaturalLanguageSubscription = z.object({
  name: z.string().min(1),
  query: z.string().min(1),
  searchHints: z.array(z.string()).optional(),
  freshness: z.enum(["latest", "recent", "anytime"]).optional(),
  maxPerRun: z.number().int().min(1).max(50).optional(),
  language: z.enum(["en", "ko", "ja", "zh"]).optional(),
  schedule: ScheduleInput.optional(),
  lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
  tags: z.array(z.string()).optional(),
});
export type CreateNaturalLanguageSubscription = z.infer<typeof CreateNaturalLanguageSubscription>;

/** Create any subscription type — used by the /subscribe command */
export const CreateSubscription = z.discriminatedUnion("type", [
  CreateNaturalLanguageSubscription.extend({ type: z.literal("natural-language") }),
  z.object({
    type: z.literal("rss"),
    name: z.string().min(1),
    url: z.string().url(),
    schedule: ScheduleInput.optional(),
    lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
    tags: z.array(z.string()).optional(),
  }),
  z.object({
    type: z.literal("youtube"),
    name: z.string().min(1),
    url: z.string().url(),
    schedule: ScheduleInput.optional(),
    lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
    tags: z.array(z.string()).optional(),
  }),
  z.object({
    type: z.literal("twitter"),
    name: z.string().min(1),
    handle: z.string().min(1),
    includeReplies: z.boolean().optional(),
    schedule: ScheduleInput.optional(),
    lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
    tags: z.array(z.string()).optional(),
  }),
  z.object({
    type: z.literal("webpage"),
    name: z.string().min(1),
    url: z.string().url(),
    selector: z.string().optional(),
    mode: z.enum(["list", "content-diff"]).optional(),
    schedule: ScheduleInput.optional(),
    lensId: z.string().regex(/^[a-z0-9-]+$/).optional(),
    tags: z.array(z.string()).optional(),
  }),
]);
export type CreateSubscription = z.infer<typeof CreateSubscription>;

// ============================================================================
// SQLite row type — flat representation for DB storage
// ============================================================================

/**
 * SQLite row representation.
 * Type-specific fields are stored as JSON in the `config` column.
 * Schedule is stored as three separate columns for efficient querying.
 */
export const SubscriptionRow = z.object({
  id: z.string().uuid(),
  name: z.string(),
  type: SourceType,
  status: SubscriptionStatus,
  /** Preset schedule alias (always stored, used as fallback) */
  schedule_preset: CollectionSchedule,
  /** Cron expression — takes priority when set */
  schedule_cron: z.string().nullable(),
  /** Interval in minutes — takes priority over preset when set */
  schedule_interval_minutes: z.number().int().nullable(),
  lens_id: z.string().nullable(),
  tags: z.string().describe("JSON-serialized string[]"),
  /** Type-specific config as JSON string */
  config: z.string().describe(
    "JSON object with type-specific fields: " +
    "rss ->{url}, youtube ->{url, channelId}, " +
    "twitter ->{handle, includeReplies}, " +
    "webpage ->{url, selector, mode}, " +
    "natural-language ->{query, searchHints, freshness, maxPerRun, language, refinedQuery}"
  ),
  error_message: z.string().nullable(),
  last_collected_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type SubscriptionRow = z.infer<typeof SubscriptionRow>;
