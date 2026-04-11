/**
 * LLM Wiki — Onboarding Interview Questions
 *
 * Static question set used by the `/setup` command to configure a user's
 * knowledge wiki. The interview gathers:
 *   1. Knowledge domains / interests → seed initial Lenses
 *   2. Source preferences → which collectors to configure
 *   3. Output format preferences → compilation settings
 *   4. Vault configuration → where to put things
 *
 * Design decisions:
 * - Questions are ordered for progressive disclosure (easy → specific)
 * - Each question has a `key` for programmatic access and a `followUp`
 *   function the LLM can use to ask clarifying sub-questions
 * - `parseHint` guides the LLM on how to structure the answer into config
 * - Questions are i18n-ready (en default, can add ko/ja later)
 * - Skip logic via `condition` — some questions only appear based on prior answers
 */

// ============================================================================
// Types
// ============================================================================

/** Supported answer input types for interview questions */
export type AnswerType =
  | "free-text"       // Open-ended natural language
  | "single-select"   // Pick one from options
  | "multi-select"    // Pick multiple from options
  | "confirm"         // Yes/no
  | "path";           // File system path

/** A single interview question */
export interface InterviewQuestion {
  /** Unique key for programmatic reference */
  key: string;

  /** The category/phase this question belongs to */
  phase: "interests" | "sources" | "output" | "vault";

  /** The question text shown to the user */
  question: string;

  /** Shorter description for context */
  description: string;

  /** Expected answer type */
  answerType: AnswerType;

  /** Predefined options (for select types) */
  options?: string[];

  /** Default value if user skips */
  defaultValue?: string | string[] | boolean;

  /**
   * Hint for the LLM on how to parse the user's answer into
   * structured config (Lens, Subscription, or vault settings).
   */
  parseHint: string;

  /**
   * Example answer to help the user understand what's expected.
   */
  example: string;

  /**
   * Condition for showing this question.
   * References another question's key and expected answer.
   * If absent, the question is always shown.
   */
  condition?: {
    /** Key of the question this depends on */
    dependsOn: string;
    /** Show this question only when the dependency answer includes this value */
    includes: string;
  };

  /** Whether this question is required or can be skipped */
  required: boolean;
}

/** Interview result — answers keyed by question key */
export type InterviewAnswers = Record<string, string | string[] | boolean>;

// ============================================================================
// Questions
// ============================================================================

export const INTERVIEW_QUESTIONS: readonly InterviewQuestion[] = [
  // ── Phase 1: Interests ──────────────────────────────────────────────
  {
    key: "interests",
    phase: "interests",
    question:
      "What topics or domains are you interested in tracking? List as many as you like.",
    description:
      "These become your initial Lenses — the categories that organize your wiki.",
    answerType: "free-text",
    parseHint:
      "Extract distinct topic areas. Each becomes a Lens with id (slug), name, " +
      "description, and keywords. E.g., 'AI and LLMs, frontend dev, indie hacking' → " +
      "3 lenses: ai-llm, frontend-dev, indie-hacking.",
    example:
      "AI research and LLMs, React/frontend ecosystem, startup growth strategies, " +
      "Korean tech industry",
    required: true,
  },
  {
    key: "interest_depth",
    phase: "interests",
    question:
      "For each topic, how deep do you want to go? " +
      "(e.g., surface-level news vs. deep technical analysis)",
    description:
      "This shapes the compile instructions for each Lens.",
    answerType: "free-text",
    parseHint:
      "Map depth preference to compileInstructions per lens. " +
      "'Deep technical' → detailed prose with references. " +
      "'Surface news' → concise summaries with links.",
    example:
      "AI: deep technical with paper references. " +
      "Frontend: practical tips and code examples. " +
      "Startups: high-level summaries.",
    required: false,
    defaultValue: "balanced — mix of summaries and deeper analysis",
  },
  {
    key: "language_preference",
    phase: "interests",
    question:
      "What language(s) should your wiki be written in? " +
      "And do you want to collect content in any specific language?",
    description:
      "Controls both collection language filter and wiki output language.",
    answerType: "free-text",
    parseHint:
      "Extract: (1) wiki output language for compileInstructions, " +
      "(2) collection language filter for subscriptions. " +
      "E.g., 'Write in Korean, collect in English and Korean' → " +
      "compileInstructions includes '한국어로 작성', subscription language filters set.",
    example:
      "Write wiki in English. Collect content in English and Korean.",
    required: false,
    defaultValue: "English",
  },

  // ── Phase 2: Sources ────────────────────────────────────────────────
  {
    key: "source_types",
    phase: "sources",
    question:
      "What types of sources do you want to collect from? Select all that apply.",
    description:
      "Determines which collectors to configure.",
    answerType: "multi-select",
    options: [
      "RSS feeds (blogs, newsletters, publications)",
      "YouTube channels/playlists",
      "Twitter/X accounts",
      "Webpages (monitor for changes)",
      "Natural language (describe what to follow, LLM finds it)",
    ],
    parseHint:
      "Map selections to SourceType values: " +
      "RSS→'rss', YouTube→'youtube', Twitter→'twitter', " +
      "Webpages→'webpage', Natural language→'natural-language'. " +
      "These determine which follow-up questions to ask.",
    example: "RSS feeds, YouTube channels, Natural language",
    required: true,
  },
  {
    key: "rss_feeds",
    phase: "sources",
    question:
      "List any RSS feeds or blogs you'd like to follow. " +
      "You can provide URLs or just names (we'll find the feeds).",
    description:
      "Initial RSS subscriptions to create.",
    answerType: "free-text",
    parseHint:
      "Extract feed URLs or blog names. For names without URLs, " +
      "note them for LLM-assisted feed discovery during setup. " +
      "Create one RssSubscription per feed.",
    example:
      "https://simonwillison.net/atom/everything/, " +
      "Hacker News best, The Pragmatic Engineer, ByteByteGo",
    required: false,
    condition: {
      dependsOn: "source_types",
      includes: "RSS feeds",
    },
  },
  {
    key: "youtube_channels",
    phase: "sources",
    question:
      "Which YouTube channels or playlists would you like to track?",
    description:
      "Initial YouTube subscriptions.",
    answerType: "free-text",
    parseHint:
      "Extract channel names or URLs. " +
      "Create one YoutubeSubscription per channel/playlist.",
    example:
      "3Blue1Brown, Fireship, https://youtube.com/@ThePrimeTimeagen",
    required: false,
    condition: {
      dependsOn: "source_types",
      includes: "YouTube",
    },
  },
  {
    key: "twitter_accounts",
    phase: "sources",
    question:
      "Which Twitter/X accounts would you like to follow?",
    description:
      "Initial Twitter subscriptions.",
    answerType: "free-text",
    parseHint:
      "Extract @handles or profile URLs. " +
      "Create one TwitterSubscription per account. " +
      "Default includeReplies=false.",
    example: "@kaborobot, @swyx, @aiaborot",
    required: false,
    condition: {
      dependsOn: "source_types",
      includes: "Twitter",
    },
  },
  {
    key: "webpages",
    phase: "sources",
    question:
      "Any specific webpages you'd like to monitor for changes? " +
      "(e.g., changelog pages, documentation, job boards)",
    description:
      "Initial webpage subscriptions.",
    answerType: "free-text",
    parseHint:
      "Extract URLs. Determine mode: " +
      "changelog/docs → 'content-diff', " +
      "listing pages → 'list'. " +
      "Create one WebpageSubscription per URL.",
    example:
      "https://platform.openai.com/docs/changelog (content-diff), " +
      "https://news.ycombinator.com/best (list)",
    required: false,
    condition: {
      dependsOn: "source_types",
      includes: "Webpages",
    },
  },
  {
    key: "natural_language_queries",
    phase: "sources",
    question:
      "Describe in your own words what you'd like the LLM to find for you. " +
      "Be as specific or broad as you want.",
    description:
      "These become NaturalLanguageSubscriptions — the LLM actively searches for matching content.",
    answerType: "free-text",
    parseHint:
      "Each distinct query becomes a NaturalLanguageSubscription. " +
      "Extract: query text, searchHints (if domains mentioned), " +
      "freshness preference, language. " +
      "E.g., 'latest RAG papers from arxiv' → " +
      "{query: 'latest RAG papers', searchHints: ['arxiv.org'], freshness: 'latest'}.",
    example:
      "Find me the latest papers on retrieval-augmented generation. " +
      "Also track discussions about Claude Code tips on Reddit and Twitter.",
    required: false,
    condition: {
      dependsOn: "source_types",
      includes: "Natural language",
    },
  },
  {
    key: "collection_frequency",
    phase: "sources",
    question:
      "How often should we collect new content?",
    description:
      "Default collection schedule for all subscriptions.",
    answerType: "single-select",
    options: ["hourly", "daily", "weekly", "manual (only when I ask)"],
    defaultValue: "daily",
    parseHint:
      "Map to CollectionSchedule: hourly/daily/weekly/manual. " +
      "This becomes the default; individual subscriptions can override.",
    example: "daily",
    required: false,
  },

  // ── Phase 3: Output Preferences ─────────────────────────────────────
  {
    key: "compile_strategy",
    phase: "output",
    question:
      "How should collected sources be turned into wiki pages?",
    description:
      "Default compile strategy for your Lenses.",
    answerType: "single-select",
    options: [
      "merge — Synthesize multiple sources into unified topic pages (recommended)",
      "per-source — Each source becomes its own wiki page",
      "append — Add new findings to existing pages over time",
    ],
    defaultValue: "merge",
    parseHint:
      "Map to CompileStrategy: 'merge'|'per-source'|'append'. " +
      "This is the default; each Lens can override.",
    example: "merge",
    required: false,
  },
  {
    key: "wiki_style",
    phase: "output",
    question:
      "What writing style should the wiki use?",
    description:
      "Becomes part of the global compileInstructions.",
    answerType: "free-text",
    parseHint:
      "Use answer as base compileInstructions applied to all Lenses. " +
      "Merge with per-lens depth preferences from interest_depth.",
    example:
      "Clear and concise. Use bullet points for key takeaways. " +
      "Include [[wikilinks]] to related concepts. " +
      "Add source URLs at the bottom.",
    required: false,
    defaultValue:
      "Clear, well-structured prose with [[wikilinks]] to related topics. " +
      "Include key takeaways and source references.",
  },
  {
    key: "tagging_preference",
    phase: "output",
    question:
      "Do you have a preferred tagging system? " +
      "(e.g., existing Obsidian tags you use)",
    description:
      "Seeds the defaultTags for Lenses and guides tag generation.",
    answerType: "free-text",
    parseHint:
      "Extract tag conventions: prefix patterns (e.g., 'type/' or 'status/'), " +
      "existing tags to reuse, tag style (kebab-case, camelCase). " +
      "Apply to Lens defaultTags and compilation instructions.",
    example:
      "I use #type/article, #type/video, #status/reading, " +
      "and topic tags like #ai, #frontend, #startup",
    required: false,
    defaultValue: "auto — LLM generates relevant #tags based on content",
  },

  // ── Phase 4: Vault Configuration ────────────────────────────────────
  {
    key: "vault_path",
    phase: "vault",
    question:
      "Where is your Obsidian vault? Provide the full path.",
    description:
      "The root directory of your Obsidian vault where wiki pages will be created.",
    answerType: "path",
    parseHint:
      "Validate path exists. Store in .llm-wiki.json as vault_path. " +
      "Expand ~ to home directory.",
    example: "~/obsidian/my-knowledge-base",
    required: true,
  },
  {
    key: "existing_vault",
    phase: "vault",
    question:
      "Is this an existing vault with content, or a fresh vault?",
    description:
      "Determines whether to scan for existing structure and conventions.",
    answerType: "single-select",
    options: [
      "Existing vault with content",
      "Fresh/empty vault",
    ],
    parseHint:
      "If existing: scan vault for folder structure, tag conventions, " +
      "and existing topics to avoid duplication. " +
      "If fresh: use defaults from other answers.",
    example: "Existing vault with content",
    required: true,
  },
  {
    key: "wiki_directory",
    phase: "vault",
    question:
      "Where should wiki pages be created within your vault? " +
      "(relative path from vault root)",
    description:
      "Base directory for compiled wiki output.",
    answerType: "free-text",
    parseHint:
      "Store as base wikiDirectory in config. " +
      "Each Lens will create a subdirectory under this. " +
      "E.g., 'wiki' → wiki/ai-research/, wiki/frontend-dev/.",
    example: "wiki",
    required: false,
    defaultValue: "wiki",
  },
];

// ============================================================================
// Helpers
// ============================================================================

/** Get all questions for a specific phase */
export function getQuestionsByPhase(
  phase: InterviewQuestion["phase"]
): InterviewQuestion[] {
  return INTERVIEW_QUESTIONS.filter((q) => q.phase === phase);
}

/** Get a question by its key */
export function getQuestion(key: string): InterviewQuestion | undefined {
  return INTERVIEW_QUESTIONS.find((q) => q.key === key);
}

/**
 * Determine which questions to show based on current answers.
 * Evaluates skip conditions and returns the filtered list.
 */
export function getActiveQuestions(
  answers: InterviewAnswers
): InterviewQuestion[] {
  return INTERVIEW_QUESTIONS.filter((q) => {
    if (!q.condition) return true;

    const depAnswer = answers[q.condition.dependsOn];
    if (depAnswer === undefined) return false;

    // For multi-select answers (arrays), check if the dependency value is included
    if (Array.isArray(depAnswer)) {
      return depAnswer.some((a) => a.includes(q.condition!.includes));
    }

    // For string answers, check substring match
    if (typeof depAnswer === "string") {
      return depAnswer.includes(q.condition.includes);
    }

    return false;
  });
}

/** Phase display order and labels */
export const INTERVIEW_PHASES = [
  { key: "interests", label: "🎯 Your Interests", order: 1 },
  { key: "sources", label: "📡 Content Sources", order: 2 },
  { key: "output", label: "📝 Wiki Output", order: 3 },
  { key: "vault", label: "🗂️ Vault Setup", order: 4 },
] as const;

/** Total number of required questions */
export const REQUIRED_QUESTION_COUNT = INTERVIEW_QUESTIONS.filter(
  (q) => q.required
).length;
