---
type: webpage
url: "https://simonwillison.net/2026/Apr/7/sqlite-wal-docker-containers#atom-everything"
title: SQLite WAL Mode Across Docker Containers Sharing a Volume
collected_at: "2026-04-12T11:14:43.746906+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# SQLite WAL Mode Across Docker Containers Sharing a Volume

> <p><strong>Research:</strong> <a href="https://github.com/simonw/research/tree/main/sqlite-wal-docker-containers#readme">SQLite WAL Mode Across Docker Containers Sharing a Volume</a></p>
    <p>Inspired by <a href="https://news.ycombinator.com/item?id=47637353">this conversation</a> on Hacker News about whether two SQLite processes in separate Docker containers that share the same volume might run into problems due to WAL shared memory. The answer is that everything works fine - Docker containers on the same host and filesystem share the same shared memory in a way that allows WAL to collaborate as it should.</p>
    
        <p>Tags: <a href="https://simonwillison.net/tags/docker">docker</a>, <a href="https://simonwillison.net/tags/sqlite">sqlite</a></p>

Source: https://simonwillison.net/2026/Apr/7/sqlite-wal-docker-containers#atom-everything
