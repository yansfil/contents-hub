---
type: webpage
url: "https://simonwillison.net/2026/Apr/5/building-with-ai#atom-everything"
title: Eight years of wanting, three months of building with AI
collected_at: "2026-04-12T11:14:43.746936+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Eight years of wanting, three months of building with AI

> <p><strong><a href="https://lalitm.com/post/building-syntaqlite-ai/">Eight years of wanting, three months of building with AI</a></strong></p>
Lalit Maganti provides one of my favorite pieces of long-form writing on agentic engineering I've seen in ages.</p>
<p>They spent eight years thinking about and then three months building <a href="https://github.com/lalitMaganti/syntaqlite">syntaqlite</a>, which they describe as "<a href="https://lalitm.com/post/syntaqlite/">high-fidelity devtools that SQLite deserves</a>".</p>
<p>The goal was to provide fast, robust and comprehensive linting and verifying tools for SQLite, suitable for use in language servers and other development tools - a parser, formatter, and verifier for SQLite queries. I've found myself wanting this kind of thing in the past myself, hence my (far less production-ready) <a href="https://simonwillison.net/2026/Jan/30/sqlite-ast-2/">sqlite-ast</a> project from a few months ago.</p>
<p>Lalit had been procrastinating on this project for years, because of the inevitable tedium of needing to work through 400+ grammar rules to help build a parser. That's exactly the kind of tedious work that coding agents excel at!</p>
<p>Claude Code helped get over that initial hump and build the first prototype:</p>
<blockquote>
<p>AI basically let me put aside all my doubts on technical calls, my uncertainty of building the right thing and my reluctance to get started by giving me very concrete problems to work on. Instead of “I need to understand how SQLite’s parsing works”, it was “I need to get AI to suggest an approach for me so I can tear it up and build something better". I work so much better with concrete prototypes to play with and code to look at than endlessly thinking about designs in my head, and AI lets me get to that point at a pace I could not have dreamed about before. Once I took the first step, every step after that was so much easier.</p>
</blockquote>
<p>That first vibe-coded prototype worked great as a proof of concept, but they eventually made the decision to throw it away and start again from scratch. AI worked great for the low level details but did not produce a coherent high-level architecture:</p>
<blockquote>
<p>I found that AI made me procrastinate on key design decisions. Because refactoring was cheap, I could always say “I’ll deal with this later.” And because AI could refactor at the same industrial scale it generated code, the cost of deferring felt low. But it wasn’t: deferring decisions corroded my ability to think clearly because the codebase stayed confusing in the meantime.</p>
</blockquote>
<p>The second attempt took a lot longer and involved a great deal more human-in-the-loop decision making, but the result is a robust library that can stand the test of time.</p>
<p>It's worth setting aside some time to read this whole thing - it's full of non-obvious downsides to working heavily with AI, as well as a detailed explanation of how they overcame those hurdles.</p>
<p>The key idea I took away from this concerns AI's weakness in terms of design and architecture:</p>
<blockquote>
<p>When I was working on something where I didn’t even know what I wanted, AI was somewhere between unhelpful and harmful. The architecture of the project was the clearest case: I spent weeks in the early days following AI down dead ends, exploring designs that felt productive in the moment but collapsed under scrutiny. In hindsight, I have to wonder if it would have been faster just thinking it through without AI in the loop at all.</p>
<p>But expertise alone isn’t enough. Even when I understood a problem deeply, AI still struggled if the task had no objectively checkable answer. Implementation has a right answer, at least at a local level: the code compiles, the tests pass, the output matches what you asked for. Design doesn’t. We’re still arguing about OOP decades after it first took off.</p>
</blockquote>

    <p><small></small>Via <a href="https://news.ycombinator.com/item?id=47648828">Hacker News</a></small></p>


    <p>Tags: <a href="https://simonwillison.net/tags/sqlite">sqlite</a>, <a href="https://simonwillison.net/tags/ai">ai</a>, <a href="https://simonwillison.net/tags/generative-ai">generative-ai</a>, <a href="https://simonwillison.net/tags/llms">llms</a>, <a href="https://simonwillison.net/tags/ai-assisted-programming">ai-assisted-programming</a>, <a href="https://simonwillison.net/tags/vibe-coding">vibe-coding</a>, <a href="https://simonwillison.net/tags/agentic-engineering">agentic-engineering</a></p>

Source: https://simonwillison.net/2026/Apr/5/building-with-ai#atom-everything
