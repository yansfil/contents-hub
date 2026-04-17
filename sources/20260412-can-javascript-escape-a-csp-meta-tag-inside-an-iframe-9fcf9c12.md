---
type: webpage
url: "https://simonwillison.net/2026/Apr/3/test-csp-iframe-escape#atom-everything"
title: Can JavaScript Escape a CSP Meta Tag Inside an Iframe?
collected_at: "2026-04-12T11:14:43.746995+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Can JavaScript Escape a CSP Meta Tag Inside an Iframe?

> <p><strong>Research:</strong> <a href="https://github.com/simonw/research/tree/main/test-csp-iframe-escape#readme">Can JavaScript Escape a CSP Meta Tag Inside an Iframe?</a></p>
    <p>In trying to build my own version of Claude Artifacts I got curious about options for applying CSP headers to content in sandboxed iframes without using a separate domain to host the files. Turns out you can inject <code>&lt;meta http-equiv="Content-Security-Policy"...&gt;</code> tags at the top of the iframe content and they'll be obeyed even if subsequent untrusted JavaScript tries to manipulate them.</p>
    
        <p>Tags: <a href="https://simonwillison.net/tags/iframes">iframes</a>, <a href="https://simonwillison.net/tags/security">security</a>, <a href="https://simonwillison.net/tags/javascript">javascript</a>, <a href="https://simonwillison.net/tags/content-security-policy">content-security-policy</a>, <a href="https://simonwillison.net/tags/sandboxing">sandboxing</a></p>

Source: https://simonwillison.net/2026/Apr/3/test-csp-iframe-escape#atom-everything
