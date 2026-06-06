# Executive Summary Prompt

You are writing the top section of a daily briefing. The reader should
understand the strongest cross-topic insight before reading the Lens sections.

## Inputs

- topic_count: {topic_count}
- item_count: {item_count}

You receive the already-written per-Lens narratives below. Synthesize them;
do not re-list every item. Look for cross-Lens themes, surprising tensions,
and the most useful quote or insight.

## Per-Lens Narratives

{group_narratives_block}

## Output Rules

- Output markdown only. No preamble and no code fence.
- Start exactly with `📌 오늘의 핵심` when writing Korean, or
  `📌 Today's Highlights` when writing English.
- Then write one blank line and 2-3 sentences.
- Mention the total topic_count and item_count naturally.
- Include one memorable quote in quotation marks if one appears in the Lens
  narratives. Do not invent a quote.
- Make the reader want to continue into the sections below.
- Match the dominant language of the Lens narratives.
