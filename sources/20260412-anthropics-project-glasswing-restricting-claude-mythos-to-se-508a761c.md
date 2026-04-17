---
type: webpage
url: "https://simonwillison.net/2026/Apr/7/project-glasswing#atom-everything"
title: Anthropic's Project Glasswing - restricting Claude Mythos to security researchers - sounds necessary to me
collected_at: "2026-04-12T11:14:43.746894+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Anthropic's Project Glasswing - restricting Claude Mythos to security researchers - sounds necessary to me

> <p>Anthropic <em>didn't</em> release their latest model, Claude Mythos (<a href="https://www-cdn.anthropic.com/53566bf5440a10affd749724787c8913a2ae0841.pdf">system card PDF</a>), today. They have instead made it available to a very restricted set of preview partners under their newly announced <a href="https://www.anthropic.com/glasswing">Project Glasswing</a>.</p>
<p>The model is a general purpose model, similar to Claude Opus 4.6, but Anthropic claim that its cyber-security research abilities are strong enough that they need to give the software industry as a whole time to prepare.</p>
<blockquote>
<p>Mythos Preview has already found thousands of high-severity vulnerabilities, including some in <em>every major operating system and web browser</em>. Given the rate of AI progress, it will not be long before such capabilities proliferate, potentially beyond actors who are committed to deploying them safely.</p>
<p>[...]</p>
<p>Project Glasswing partners will receive access to Claude Mythos Preview to find and fix vulnerabilities or weaknesses in their foundational systems—systems that represent a very large portion of the world’s shared cyberattack surface. We anticipate this work will focus on tasks like local vulnerability detection, black box testing of binaries, securing endpoints, and penetration testing of systems.</p>
</blockquote>

<p>There's a great deal more technical detail in <a href="https://red.anthropic.com/2026/mythos-preview/"> Assessing Claude Mythos Preview’s cybersecurity capabilities</a> on the Anthropic Red Team blog:</p>

<blockquote><p>In one case, Mythos Preview wrote a web browser exploit that chained together four vulnerabilities, writing a complex <a href="https://en.wikipedia.org/wiki/JIT_spraying ">JIT heap spray</a> that escaped both renderer and OS sandboxes. It autonomously obtained local privilege escalation exploits on Linux and other operating systems by exploiting subtle race conditions and KASLR-bypasses. And it autonomously wrote a remote code execution exploit on FreeBSD's NFS server that granted full root access to unauthenticated users by splitting a 20-gadget ROP chain over multiple packets.</p></blockquote>
<p>Plus this comparison with Claude 4.6 Opus:</p>
<blockquote><p>Our internal evaluations showed that Opus 4.6 generally had a near-0% success rate at autonomous exploit development. But Mythos Preview is in a different league. For example, Opus 4.6 turned the vulnerabilities it had found in Mozilla’s Firefox 147 JavaScript engine—all patched in Firefox 148—into JavaScript shell exploits only two times out of several hundred attempts. We re-ran this experiment as a benchmark for Mythos Preview, which developed working exploits 181 times, and achieved register control on 29 more.</p>
</blockquote>

<p>Saying "our model is too dangerous to release" is a great way to build buzz around a new model, but in this case I expect their caution is warranted.</p>
<p>Just a few days (<a href="https://simonwillison.net/2026/Apr/3/">last Friday</a>) ago I started a new <a href="https://simonwillison.net/tags/ai-security-research/">ai-security-research</a> tag on this blog to acknowledge an uptick in credible security professionals pulling the alarm on how good modern LLMs have got at vulnerability research.</p>
<p><a href="https://www.theregister.com/2026/03/26/greg_kroahhartman_ai_kernel/">Greg Kroah-Hartman</a> of the Linux kernel:</p>
<blockquote>
<p>Months ago, we were getting what we called 'AI slop,' AI-generated security reports that were obviously wrong or low quality. It was kind of funny. It didn't really worry us.</p>
<p>Something happened a month ago, and the world switched. Now we have real reports. All open source projects have real reports that are made with AI, but they're good, and they're real.</p>
</blockquote>
<p><a href="https://mastodon.social/@bagder/116336957584445742">Daniel Stenberg</a> of <code>curl</code>:</p>
<blockquote>
<p>The challenge with AI in open source security has transitioned from an AI slop tsunami into more of a ... plain security report tsunami. Less slop but lots of reports. Many of them really good.</p>
<p>I'm spending hours per day on this now. It's intense.</p>
</blockquote>
<p>And Thomas Ptacek published <a href="https://sockpuppet.org/blog/2026/03/30/vulnerability-research-is-cooked/">Vulnerability Research Is Cooked</a>, a post inspired by his <a href="https://securitycryptographywhatever.com/2026/03/25/ai-bug-finding/">podcast conversation</a> with Anthropic's Nicholas Carlini.</p>
<p>Anthropic have a 5 minute <a href="https://www.youtube.com/watch?v=INGOC6-LLv0">talking heads video</a> describing the Glasswing project. Nicholas Carlini appears as one of those talking heads, where he said (highlights mine):</p>
<blockquote>
<p>It has the ability to chain together vulnerabilities. So what this means is you find two vulnerabilities, either of which doesn't really get you very much independently. But this model is able to create exploits out of three, four, or sometimes five vulnerabilities that in sequence give you some kind of very sophisticated end outcome. [...]</p>
<p><strong>I've found more bugs in the last couple of weeks than I found in the rest of my life combined</strong>. We've used the model to scan a bunch of open source code, and the thing that we went for first was operating systems, because this is the code that underlies the entire internet infrastructure. <strong>For OpenBSD, we found a bug that's been present for 27 years, where I can send a couple of pieces of data to any OpenBSD server and crash it</strong>. On Linux, we found a number of vulnerabilities where as a user with no permissions, I can elevate myself to the administrator by just running some binary on my machine. For each of these bugs, we told the maintainers who actually run the software about them, and they went and fixed them and have deployed the patches  patches so that anyone who runs the software is no longer vulnerable to these attacks.</p>
</blockquote>
<p>I found this on the <a href="https://www.openbsd.org/errata78.html">OpenBSD 7.8 errata page</a>:</p>
<blockquote>
<p><strong>025: RELIABILITY FIX: March 25, 2026</strong>  <em>All architectures</em></p>
<p>TCP packets with invalid SACK options could crash the kernel.</p>
<p><a href="https://ftp.openbsd.org/pub/OpenBSD/patches/7.8/common/025_sack.patch.sig">A source code patch exists which remedies this problem.</a></p>
</blockquote>
<p>I tracked that change down in the <a href="https://github.com/openbsd/src">GitHub mirror</a> of the OpenBSD CVS repo (apparently they still use CVS!) and found it <a href="https://github.com/openbsd/src/blame/master/sys/netinet/tcp_input.c#L2461">using git blame</a>:</p>
<p><img src="https://static.simonwillison.net/static/2026/openbsd-27-years.jpg" alt="Screenshot of a Git blame view of C source code around line 2455 showing TCP SACK hole validation logic. Code includes checks using SEQ_GT, SEQ_LT macros on fields like th-&gt;th_ack, tp-&gt;snd_una, sack.start, sack.end, tp-&gt;snd_max, and tp-&gt;snd_holes. Most commits are from 25–27 years ago with messages like &quot;more SACK hole validity testin...&quot; and &quot;knf&quot;, while one recent commit from 3 weeks ago (&quot;Ignore TCP SACK packets wit...&quot;) is highlighted with an orange left border, adding a new guard &quot;if (SEQ_LT(sack.start, tp-&gt;snd_una)) continue;&quot;" style="max-width: 100%;" /></p>
<p>Sure enough, the surrounding code is from 27 years ago.</p>
<p>I'm not sure which Linux vulnerability Nicholas was describing, but it may have been <a href="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id=5133b61aaf437e5f25b1b396b14242a6bb0508e2">this NFS one</a> recently covered <a href="https://mtlynch.io/claude-code-found-linux-vulnerability/">by Michael Lynch
</a>.</p>
<p>There's enough smoke here that I believe there's a fire. It's not surprising to find vulnerabilities in decades-old software, especially given that they're mostly written in C, but what's new is that coding agents run by the latest frontier LLMs are proving tirelessly capable at digging up these issues.</p>
<p>I actually thought to myself on Friday that this sounded like an industry-wide reckoning in the making, and that it might warrant a huge investment of time and money to get ahead of the inevitable barrage of vulnerabilities. Project Glasswing incorporates "$100M in usage credits ... as well as $4M in direct donations to open-source security organizations". Partners include AWS, Apple, Microsoft, Google, and the Linux Foundation. It would be great to see OpenAI involved as well - GPT-5.4 already has a strong reputation for finding security vulnerabilities and they have stronger models on the near horizon.</p>
<p>The bad news for those of us who are <em>not</em> trusted partners is this:</p>
<blockquote>
<p>We do not plan to make Claude Mythos Preview generally available, but our eventual goal is to enable our users to safely deploy Mythos-class models at scale—for cybersecurity purposes, but also for the myriad other benefits that such highly capable models will bring. To do so, we need to make progress in developing cybersecurity (and other) safeguards that detect and block the model’s most dangerous outputs. We plan to launch new safeguards with an upcoming Claude Opus model, allowing us to improve and refine them with a model that does not pose the same level of risk as Mythos Preview.</p>
</blockquote>
<p>I can live with that. I think the security risks really are credible here, and having extra time for trusted teams to get ahead of them is a reasonable trade-off.</p>
    
        <p>Tags: <a href="https://simonwillison.net/tags/security">security</a>, <a href="https://simonwillison.net/tags/thomas-ptacek">thomas-ptacek</a>, <a href="https://simonwillison.net/tags/ai">ai</a>, <a href="https://simonwillison.net/tags/generative-ai">generative-ai</a>, <a href="https://simonwillison.net/tags/llms">llms</a>, <a href="https://simonwillison.net/tags/anthropic">anthropic</a>, <a href="https://simonwillison.net/tags/nicholas-carlini">nicholas-carlini</a>, <a href="https://simonwillison.net/tags/ai-ethics">ai-ethics</a>, <a href="https://simonwillison.net/tags/llm-release">llm-release</a>, <a href="https://simonwillison.net/tags/ai-security-research">ai-security-research</a></p>

Source: https://simonwillison.net/2026/Apr/7/project-glasswing#atom-everything
