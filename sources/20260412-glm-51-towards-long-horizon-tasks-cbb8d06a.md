---
type: webpage
url: "https://simonwillison.net/2026/Apr/7/glm-51#atom-everything"
title: "GLM-5.1: Towards Long-Horizon Tasks"
collected_at: "2026-04-12T11:14:43.746888+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# GLM-5.1: Towards Long-Horizon Tasks

> <p><strong><a href="https://z.ai/blog/glm-5.1">GLM-5.1: Towards Long-Horizon Tasks</a></strong></p>
Chinese AI lab Z.ai's latest model is a giant 754B parameter 1.51TB (on <a href="https://huggingface.co/zai-org/GLM-5.1">Hugging Face</a>) MIT-licensed monster - the same size as their previous GLM-5 release, and sharing the <a href="https://huggingface.co/papers/2602.15763">same paper</a>.</p>
<p>It's available <a href="https://openrouter.ai/z-ai/glm-5.1">via OpenRouter</a> so I asked it to draw me a pelican:</p>
<pre><code>llm install llm-openrouter
llm -m openrouter/z-ai/glm-5.1 'Generate an SVG of a pelican on a bicycle'
</code></pre>
<p>And something new happened... unprompted, the model <a href="https://gist.github.com/simonw/af7170f54256cc007ef28a8721564be8">decided to give me</a> an HTML page that included both the SVG and a separate set of CSS animations!</p>
<p>The SVG was excellent, and might be my new favorite from an open weights model:</p>
<p><img alt="The bicycle is red and has a frame the correct shape and wheels with spokes. The pelican is a perky little fella." src="https://static.simonwillison.net/static/2026/glm-5.1-pelican.png" /></p>
<p>But the animation <a href="https://gisthost.github.io/?73bb6808b18c2482f66e5f082c75f36e">broke it</a>:</p>
<p><img alt="Animation - the wheels and pedals rotate, the clouds move... and the pelican has vanished, but there is a little blob bobbing up and down in the top left corner." src="https://static.simonwillison.net/static/2026/glm-5.1-broken-light-lossy.gif" /></p>
<p>That's the pelican, floating up in the top left corner.</p>
<p>I usually don't do follow-up prompts for the pelican test, but in this case I made an exception:</p>
<pre><code>llm -c 'the animation is a bit broken, the pelican ends up positioned off the screen at the top right'
</code></pre>
<p>GLM 5.1 replied:</p>
<blockquote>
<p>The issue is that CSS <code>transform</code> animations on SVG elements override the SVG <code>transform</code> attribute used for positioning, causing the pelican to lose its placement and fly off to the top-right. The fix is to separate positioning (SVG attribute) from animation (inner group) and use <code>&lt;animateTransform&gt;</code> for SVG rotations since it handles coordinate systems correctly.</p>
</blockquote>
<p>And spat out <a href="https://static.simonwillison.net/static/2026/glm-5.1-pelican-fixed.html">fresh HTML</a> which fixed the problem!</p>
<p><img alt="Now everything is right - the bicycle rotates correctly, the pelican sits on it and bobs up and down, and its lower beak moves slightly as well." src="https://static.simonwillison.net/static/2026/glm-5.1-pelican-fixed-medium-lossy.gif" /></p>
<p>I particularly like the animation of the beak, which is described in the SVG comments like so:</p>
<div class="highlight highlight-text-xml-svg"><pre><span class="pl-c"><span class="pl-c">&lt;!--</span> Pouch (lower beak) with wobble <span class="pl-c">--&gt;</span></span>
&lt;<span class="pl-ent">g</span>&gt;
    &lt;<span class="pl-ent">path</span> <span class="pl-e">d</span>=<span class="pl-s"><span class="pl-pds">"</span>M42,-58 Q43,-50 48,-42 Q55,-35 62,-38 Q70,-42 75,-60 L42,-58 Z<span class="pl-pds">"</span></span> <span class="pl-e">fill</span>=<span class="pl-s"><span class="pl-pds">"</span>url(#pouchGrad)<span class="pl-pds">"</span></span> <span class="pl-e">stroke</span>=<span class="pl-s"><span class="pl-pds">"</span>#b06008<span class="pl-pds">"</span></span> <span class="pl-e">stroke-width</span>=<span class="pl-s"><span class="pl-pds">"</span>1<span class="pl-pds">"</span></span> <span class="pl-e">opacity</span>=<span class="pl-s"><span class="pl-pds">"</span>0.9<span class="pl-pds">"</span></span>/&gt;
    &lt;<span class="pl-ent">path</span> <span class="pl-e">d</span>=<span class="pl-s"><span class="pl-pds">"</span>M48,-50 Q55,-46 60,-52<span class="pl-pds">"</span></span> <span class="pl-e">fill</span>=<span class="pl-s"><span class="pl-pds">"</span>none<span class="pl-pds">"</span></span> <span class="pl-e">stroke</span>=<span class="pl-s"><span class="pl-pds">"</span>#c06a08<span class="pl-pds">"</span></span> <span class="pl-e">stroke-width</span>=<span class="pl-s"><span class="pl-pds">"</span>0.8<span class="pl-pds">"</span></span> <span class="pl-e">opacity</span>=<span class="pl-s"><span class="pl-pds">"</span>0.6<span class="pl-pds">"</span></span>/&gt;
    &lt;<span class="pl-ent">animateTransform</span> <span class="pl-e">attributeName</span>=<span class="pl-s"><span class="pl-pds">"</span>transform<span class="pl-pds">"</span></span> <span class="pl-e">type</span>=<span class="pl-s"><span class="pl-pds">"</span>scale<span class="pl-pds">"</span></span>
    <span class="pl-e">values</span>=<span class="pl-s"><span class="pl-pds">"</span>1,1; 1.03,0.97; 1,1<span class="pl-pds">"</span></span> <span class="pl-e">dur</span>=<span class="pl-s"><span class="pl-pds">"</span>0.75s<span class="pl-pds">"</span></span> <span class="pl-e">repeatCount</span>=<span class="pl-s"><span class="pl-pds">"</span>indefinite<span class="pl-pds">"</span></span>
    <span class="pl-e">additive</span>=<span class="pl-s"><span class="pl-pds">"</span>sum<span class="pl-pds">"</span></span>/&gt;
&lt;/<span class="pl-ent">g</span>&gt;</pre></div>

<p><strong>Update</strong>: On Bluesky <a href="https://bsky.app/profile/charles.capps.me/post/3miwrn42mjc2t">@charles.capps.me suggested</a> a "NORTH VIRGINIA OPOSSUM ON AN E-SCOOTER" and...</p>
<p><img alt="This is so great. It's dark, the possum is clearly a possum, it's riding an escooter, lovely animation, tail bobbing up and down, caption says NORTH VIRGINIA OPOSSUM, CRUISING THE COMMONWEALTH SINCE DUSK - only glitch is that it occasionally blinks and the eyes fall off the face" src="https://static.simonwillison.net/static/2026/glm-possum-escooter.gif.gif" /></p>
<p>The HTML+SVG comments on that one include <code>/* Earring sparkle */, &lt;!-- Opossum fur gradient --&gt;, &lt;!-- Distant treeline silhouette - Virginia pines --&gt;,  &lt;!-- Front paw on handlebar --&gt;</code> - here's <a href="https://gist.github.com/simonw/1864b89f5304eba03c3ded4697e156c4">the transcript</a> and the <a href="https://static.simonwillison.net/static/2026/glm-possum-escooter.html">HTML result</a>.


    <p>Tags: <a href="https://simonwillison.net/tags/css">css</a>, <a href="https://simonwillison.net/tags/svg">svg</a>, <a href="https://simonwillison.net/tags/ai">ai</a>, <a href="https://simonwillison.net/tags/generative-ai">generative-ai</a>, <a href="https://simonwillison.net/tags/llms">llms</a>, <a href="https://simonwillison.net/tags/pelican-riding-a-bicycle">pelican-riding-a-bicycle</a>, <a href="https://simonwillison.net/tags/llm-release">llm-release</a>, <a href="https://simonwillison.net/tags/ai-in-china">ai-in-china</a>, <a href="https://simonwillison.net/tags/glm">glm</a></p>

Source: https://simonwillison.net/2026/Apr/7/glm-51#atom-everything
