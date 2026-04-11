/**
 * Tests for subscription type schemas — validates Zod schemas
 * with realistic examples for each subscription type.
 */

import { describe, expect, it } from "vitest";
import {
  CollectionSchedule,
  CreateNaturalLanguageSubscription,
  CreateSubscription,
  LensSchema,
  NaturalLanguageSubscription,
  RssSubscription,
  SourceType,
  Subscription,
  SubscriptionRow,
  SubscriptionStatus,
  TwitterSubscription,
  WebpageSubscription,
  YoutubeSubscription,
} from "../src/schemas/subscription";

// ============================================================================
// Enums
// ============================================================================

describe("SourceType", () => {
  it("accepts valid source types", () => {
    expect(SourceType.parse("rss")).toBe("rss");
    expect(SourceType.parse("youtube")).toBe("youtube");
    expect(SourceType.parse("twitter")).toBe("twitter");
    expect(SourceType.parse("webpage")).toBe("webpage");
    expect(SourceType.parse("natural-language")).toBe("natural-language");
  });

  it("rejects invalid source types", () => {
    expect(() => SourceType.parse("email")).toThrow();
    expect(() => SourceType.parse("")).toThrow();
  });
});

describe("SubscriptionStatus", () => {
  it("accepts active/paused/broken", () => {
    expect(SubscriptionStatus.parse("active")).toBe("active");
    expect(SubscriptionStatus.parse("paused")).toBe("paused");
    expect(SubscriptionStatus.parse("broken")).toBe("broken");
  });
});

describe("CollectionSchedule", () => {
  it("accepts all schedule types", () => {
    expect(CollectionSchedule.parse("hourly")).toBe("hourly");
    expect(CollectionSchedule.parse("daily")).toBe("daily");
    expect(CollectionSchedule.parse("weekly")).toBe("weekly");
    expect(CollectionSchedule.parse("manual")).toBe("manual");
  });
});

// ============================================================================
// Lens
// ============================================================================

describe("LensSchema", () => {
  it("parses a valid lens", () => {
    const lens = LensSchema.parse({
      id: "ai-research",
      name: "AI Research",
      description: "Papers, blog posts, and discussions about AI/ML",
      defaultTags: ["ai", "research"],
    });
    expect(lens.id).toBe("ai-research");
    expect(lens.defaultTags).toEqual(["ai", "research"]);
  });

  it("rejects non-slug IDs", () => {
    expect(() =>
      LensSchema.parse({ id: "AI Research", name: "AI Research" })
    ).toThrow();
  });

  it("applies default empty tags", () => {
    const lens = LensSchema.parse({ id: "misc", name: "Miscellaneous" });
    expect(lens.defaultTags).toEqual([]);
  });
});

// ============================================================================
// Natural-Language Subscription (the novel type)
// ============================================================================

describe("NaturalLanguageSubscription", () => {
  const validNL = {
    name: "AI RAG Research",
    type: "natural-language" as const,
    query: "Latest research on retrieval-augmented generation for LLMs",
    searchHints: ["arxiv.org", "huggingface.co", "semantic scholar"],
    freshness: "recent" as const,
    maxPerRun: 10,
    language: "en" as const,
  };

  it("parses a complete natural-language subscription", () => {
    const result = NaturalLanguageSubscription.parse(validNL);
    expect(result.type).toBe("natural-language");
    expect(result.query).toContain("retrieval-augmented generation");
    expect(result.searchHints).toHaveLength(3);
    expect(result.maxPerRun).toBe(10);
    expect(result.status).toBe("active"); // default
    expect(result.schedule).toEqual({ preset: "daily" }); // default
  });

  it("applies sensible defaults for minimal input", () => {
    const result = NaturalLanguageSubscription.parse({
      name: "Quick Follow",
      type: "natural-language",
      query: "React Server Components production experiences",
    });
    expect(result.freshness).toBe("recent");
    expect(result.maxPerRun).toBe(10);
    expect(result.searchHints).toEqual([]);
    expect(result.status).toBe("active");
    expect(result.schedule).toEqual({ preset: "daily" });
    expect(result.tags).toEqual([]);
  });

  it("rejects empty query", () => {
    expect(() =>
      NaturalLanguageSubscription.parse({
        name: "Empty",
        type: "natural-language",
        query: "",
      })
    ).toThrow();
  });

  it("enforces maxPerRun bounds (1-50)", () => {
    expect(() =>
      NaturalLanguageSubscription.parse({
        ...validNL,
        maxPerRun: 0,
      })
    ).toThrow();

    expect(() =>
      NaturalLanguageSubscription.parse({
        ...validNL,
        maxPerRun: 100,
      })
    ).toThrow();

    const result = NaturalLanguageSubscription.parse({
      ...validNL,
      maxPerRun: 50,
    });
    expect(result.maxPerRun).toBe(50);
  });

  it("allows refinedQuery after first collection", () => {
    const result = NaturalLanguageSubscription.parse({
      ...validNL,
      refinedQuery:
        "RAG (retrieval-augmented generation) research papers 2024, " +
        "focusing on dense retrieval, chunking strategies, and hybrid search",
    });
    expect(result.refinedQuery).toContain("dense retrieval");
  });

  it("supports Korean query", () => {
    const result = NaturalLanguageSubscription.parse({
      name: "한국 스타트업 뉴스",
      type: "natural-language",
      query: "한국 스타트업 시리즈A 이상 투자 뉴스",
      language: "ko",
      searchHints: ["platum.kr", "thevc.kr"],
    });
    expect(result.query).toContain("스타트업");
    expect(result.language).toBe("ko");
  });
});

// ============================================================================
// URL-based subscriptions
// ============================================================================

describe("RssSubscription", () => {
  it("parses a valid RSS subscription", () => {
    const result = RssSubscription.parse({
      name: "Hacker News",
      type: "rss",
      url: "https://hnrss.org/frontpage",
    });
    expect(result.type).toBe("rss");
    expect(result.url).toContain("hnrss.org");
  });

  it("rejects invalid URLs", () => {
    expect(() =>
      RssSubscription.parse({ name: "Bad", type: "rss", url: "not-a-url" })
    ).toThrow();
  });
});

describe("YoutubeSubscription", () => {
  it("parses a YouTube subscription", () => {
    const result = YoutubeSubscription.parse({
      name: "3Blue1Brown",
      type: "youtube",
      url: "https://youtube.com/@3blue1brown",
    });
    expect(result.type).toBe("youtube");
  });
});

describe("TwitterSubscription", () => {
  it("parses a Twitter subscription with defaults", () => {
    const result = TwitterSubscription.parse({
      name: "Andrej Karpathy",
      type: "twitter",
      handle: "@karpathy",
    });
    expect(result.type).toBe("twitter");
    expect(result.includeReplies).toBe(false);
  });
});

describe("WebpageSubscription", () => {
  it("parses a webpage subscription with selector", () => {
    const result = WebpageSubscription.parse({
      name: "TechCrunch AI",
      type: "webpage",
      url: "https://techcrunch.com/category/artificial-intelligence/",
      selector: "article.post-block",
      mode: "list",
    });
    expect(result.type).toBe("webpage");
    expect(result.selector).toBe("article.post-block");
  });

  it("defaults mode to list", () => {
    const result = WebpageSubscription.parse({
      name: "Blog",
      type: "webpage",
      url: "https://example.com/blog",
    });
    expect(result.mode).toBe("list");
  });
});

// ============================================================================
// Discriminated Union
// ============================================================================

describe("Subscription (discriminated union)", () => {
  it("narrows to natural-language type", () => {
    const sub = Subscription.parse({
      type: "natural-language",
      name: "AI News",
      query: "Latest AI news and breakthroughs",
    });
    if (sub.type === "natural-language") {
      expect(sub.query).toContain("AI news");
    }
  });

  it("narrows to rss type", () => {
    const sub = Subscription.parse({
      type: "rss",
      name: "Feed",
      url: "https://example.com/feed.xml",
    });
    if (sub.type === "rss") {
      expect(sub.url).toContain("feed.xml");
    }
  });

  it("rejects unknown types", () => {
    expect(() =>
      Subscription.parse({ type: "email", name: "Bad", query: "x" })
    ).toThrow();
  });
});

// ============================================================================
// Create DTOs
// ============================================================================

describe("CreateNaturalLanguageSubscription", () => {
  it("validates minimal creation input", () => {
    const result = CreateNaturalLanguageSubscription.parse({
      name: "ML Papers",
      query: "Machine learning papers on efficient inference",
    });
    expect(result.name).toBe("ML Papers");
    expect(result.query).toContain("efficient inference");
    // Optional fields should be undefined
    expect(result.searchHints).toBeUndefined();
    expect(result.freshness).toBeUndefined();
  });

  it("validates full creation input", () => {
    const result = CreateNaturalLanguageSubscription.parse({
      name: "Frontend News",
      query: "React, Next.js, and Vite ecosystem updates",
      searchHints: ["react.dev", "nextjs.org"],
      freshness: "latest",
      maxPerRun: 5,
      language: "en",
      schedule: "daily",
      lensId: "frontend",
      tags: ["react", "nextjs"],
    });
    expect(result.schedule).toEqual({ preset: "daily" });
    expect(result.tags).toEqual(["react", "nextjs"]);
  });
});

describe("CreateSubscription", () => {
  it("accepts natural-language creation", () => {
    const result = CreateSubscription.parse({
      type: "natural-language",
      name: "Test",
      query: "test query",
    });
    expect(result.type).toBe("natural-language");
  });

  it("accepts rss creation", () => {
    const result = CreateSubscription.parse({
      type: "rss",
      name: "Test Feed",
      url: "https://example.com/feed.xml",
    });
    expect(result.type).toBe("rss");
  });

  it("accepts twitter creation", () => {
    const result = CreateSubscription.parse({
      type: "twitter",
      name: "Test User",
      handle: "@testuser",
    });
    expect(result.type).toBe("twitter");
  });
});

// ============================================================================
// SQLite Row
// ============================================================================

describe("SubscriptionRow", () => {
  it("validates a full SQLite row", () => {
    const row = SubscriptionRow.parse({
      id: "550e8400-e29b-41d4-a716-446655440000",
      name: "AI RAG Research",
      type: "natural-language",
      status: "active",
      schedule_preset: "daily",
      schedule_cron: null,
      schedule_interval_minutes: null,
      lens_id: "ai-research",
      tags: JSON.stringify(["ai", "rag"]),
      config: JSON.stringify({
        query: "Latest research on RAG for LLMs",
        searchHints: ["arxiv.org"],
        freshness: "recent",
        maxPerRun: 10,
      }),
      error_message: null,
      last_collected_at: null,
      created_at: "2024-01-15T10:00:00Z",
      updated_at: "2024-01-15T10:00:00Z",
    });
    expect(row.type).toBe("natural-language");
    expect(JSON.parse(row.config)).toHaveProperty("query");
    expect(JSON.parse(row.tags)).toEqual(["ai", "rag"]);
  });
});
