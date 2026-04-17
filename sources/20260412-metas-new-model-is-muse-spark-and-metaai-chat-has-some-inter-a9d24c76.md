---
type: webpage
url: "https://simonwillison.net/2026/Apr/8/muse-spark#atom-everything"
title: Meta's new model is Muse Spark, and meta.ai chat has some interesting tools
collected_at: "2026-04-12T11:14:43.746861+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Meta's new model is Muse Spark, and meta.ai chat has some interesting tools

> <p>Meta <a href="https://ai.meta.com/blog/introducing-muse-spark-msl/">announced Muse Spark</a> today, their first model release since Llama 4 <a href="https://simonwillison.net/2025/Apr/5/llama-4-notes/">almost exactly a year ago</a>. It's hosted, not open weights, and the API is currently "a private API preview to select users", but you can try it out today on <a href="https://meta.ai/">meta.ai</a> (Facebook or Instagram login required).</p>
<p>Meta's self-reported benchmarks show it competitive with Opus 4.6, Gemini 3.1 Pro, and GPT 5.4 on selected benchmarks, though notably behind on Terminal-Bench 2.0. Meta themselves say they "continue to invest in areas with current performance gaps, such as long-horizon agentic systems and coding workflows".</p>
<p>The model is exposed as two different modes on <a href="https://meta.ai/">meta.ai</a> - "Instant" and "Thinking". Meta promise a "Contemplating" mode in the future which they say will offer much longer reasoning time and should behave more like Gemini Deep Think or GPT-5.4 Pro.</p>
<h5 id="a-couple-of-pelicans">A couple of pelicans</h5>
<p>I prefer to run <a href="https://simonwillison.net/tags/pelican-riding-a-bicycle/">my pelican test</a> via API to avoid being influenced by any invisible system prompts, but since that's not an option I ran it against the chat UI directly.</p>
<p>Here's the pelican I got for "Instant":</p>
<p><img src="https://static.simonwillison.net/static/2026/muse-spark-instant-pelican.jpg" alt="This is a pretty basic pelican. The bicycle is mangled, the pelican itself has a rectangular beak albeit with a hint of pouch curve below it. Not a very good one." style="max-width: 100%;" /></p>
<p>And this one for "Thinking":</p>
<p><img src="https://static.simonwillison.net/static/2026/muse-spark-thinking-pelican.png" alt="Much better. Clearly a pelican. Bicycle is the correct shape. Pelican is wearing a blue cycling helmet (albeit badly rendered). Not a bad job at all." style="max-width: 100%;" /></p>
<p>Both SVGs were rendered inline by the Meta AI interface. Interestingly, the Instant model <a href="https://gist.github.com/simonw/ea7466204f1001b7d67afcb5d0532f6f">output an SVG directly</a> (with code comments) whereas the Thinking model <a href="https://gist.github.com/simonw/bc911a56006ba44b0bf66abf0f872ab2">wrapped it in a thin HTML shell</a> with some unused <code>Playables SDK v1.0.0</code> JavaScript libraries.</p>
<p>Which got me curious...</p>
<h5 id="poking-around-with-tools">Poking around with tools</h5>
<p>Clearly Meta's chat harness has some tools wired up to it - at the very least it can render SVG and HTML as embedded frames, Claude Artifacts style.</p>
<p>But what else can it do?</p>
<p>I asked it:</p>
<blockquote>
<p>what tools do you have access to?</p>
</blockquote>
<p>And then:</p>
<blockquote>
<p>I want the exact tool names, parameter names and tool descriptions, in the original format</p>
</blockquote>
<p>It spat out detailed descriptions of 16 different tools. You can see <a href="https://gist.github.com/simonw/e1ce0acd70443f93dcd6481e716c4304#response-1">the full list I got back here</a> - credit to Meta for not telling their bot to hide these, since it's far less frustrating if I can get them out without having to mess around with jailbreaks.</p>
<p>Here are highlights derived from that response:</p>
<ul>
<li>
<p><strong>Browse and search</strong>. <code>browser.search</code> can run a web search through an undisclosed search engine, <code>browser.open</code> can load the full page from one of those search results and <code>browser.find</code> can run pattern matches against the returned page content.</p>
</li>
<li>
<p><strong>Meta content search</strong>. <code>meta_1p.content_search</code> can run "Semantic search across Instagram, Threads, and Facebook posts" - but only for posts the user has access to view which were created since 2025-01-01. This tool has some powerful looking parameters, including <code>author_ids</code>, <code>key_celebrities</code>, <code>commented_by_user_ids</code>, and <code>liked_by_user_ids</code>.</p>
</li>
<li>
<p><strong>"Catalog search"</strong> - <code>meta_1p.meta_catalog_search</code> can "Search for products in Meta's product catalog", presumably for the "Shopping" option in the Meta AI model selector.</p>
</li>
<li>
<p><strong>Image generation</strong>. <code>media.image_gen</code> generates images from prompts, and "returns a CDN URL and saves the image to the sandbox". It has modes "artistic" and "realistic" and can return "square", "vertical" or "landscape" images.</p>
</li>
<li>
<p><strong>container.python_execution</strong> - yes! It's <a href="https://simonwillison.net/tags/code-interpreter/">Code Interpreter</a>, my favourite feature of both ChatGPT and Claude.</p>
<blockquote>
<p>Execute Python code in a remote sandbox environment. Python 3.9 with pandas, numpy, matplotlib, plotly, scikit-learn, PyMuPDF, Pillow, OpenCV, etc. Files persist at <code>/mnt/data/</code>.</p>
</blockquote>
<p>Python 3.9 <a href="https://devguide.python.org/versions/">is EOL</a> these days but the library collection looks useful.</p>
<p>I prompted "use python code to confirm sqlite version and python version" and got back Python 3.9.25 and SQLite 3.34.1 (from <a href="https://sqlite.org/releaselog/3_34_1.html">January 2021</a>).</p>
</li>
<li>
<p><strong>container.create_web_artifact</strong> - we saw this earlier with the HTML wrapper around the pelican: Meta AI can create HTML+JavaScript files in its container which can then be served up as secure sandboxed iframe interactives. "Set kind to <code>html</code> for websites/apps or <code>svg</code> for vector graphics."</p>
</li>
<li>
<p><strong>container.download_meta_1p_media</strong> is interesting: "Download media from Meta 1P sources into the sandbox. Use post_id for Instagram/Facebook/Threads posts, or <code>catalog_search_citation_id</code> for catalog product images". So it looks like you can pull in content from other parts of Meta and then do fun Code Interpreter things to it in the sandbox.</p>
</li>
<li>
<p><strong>container.file_search</strong> - "Search uploaded files in this conversation and return relevant excerpts" - I guess for digging through PDFs and similar?</p>
</li>
<li>
<p><strong>Tools for editing files in the container</strong> - <code>container.view</code>, <code>container.insert</code> (with <code>new_str</code> and <code>insert_line</code>), <code>container.str_replace</code>. These look similar to Claude's <a href="https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool#text-editor-tool-commands">text editor tool commands</a> - these are becoming a common pattern across any file-equipped agent harness.</p>
</li>
<li>
<p><strong>container.visual_grounding</strong> - see below, this one is <em>fun</em>.</p>
</li>
<li>
<p><strong>subagents.spawn_agent</strong> - the <a href="https://simonwillison.net/guides/agentic-engineering-patterns/subagents/">sub-agent as a tool</a> pattern. "Spawn an independent sub-agent for research, analysis, or delegation. It returns its final text response."</p>
</li>
<li>
<p><strong>third_party.link_third_party_account</strong> - "Initiate account linking for a third-party service", currently Google Calendar, Outlook Calendar, Gmail, or Outlook.</p>
</li>
</ul>
<h4 id="image-analysis-in-the-container">Image analysis in the container</h4>
<p>Let's talk about that <code>container.visual_grounding</code> one. Here's the description in full:</p>
<blockquote>
<p><strong>Description:</strong> Visual grounding tool that analyzes the image, identifies and labels objects, locates regions, or counts objects.</p>
<p><strong>Parameters:</strong></p>
<ul>
<li>
<strong>object_names</strong> (array, required)</li>
<li>
<strong>image_path</strong> (string, default "")</li>
<li>
<strong>format_type</strong> ("bbox"|"point"|"count"|null, default "bbox")</li>
<li>
<strong>title</strong> (string|null)[string]</li>
</ul>
</blockquote>
<p>So it can take an image path, optional object names and return things in format <code>bbox</code>, <code>point</code> or <code>count</code>.</p>
<p>This sounded to me like Meta's <a href="https://github.com/facebookresearch/segment-anything">Segment Anything</a>, (<em>I later heard that it's not, this is a native feature of the model itself via a tool call with a custom system prompt</em>). I decided to take it for a spin.</p>
<p><strong>Prompt</strong>:</p>
<blockquote>
<p>generate a photo of a raccoon sitting on a trash can wearing trash as a hat</p>
</blockquote>
<p><strong>Response</strong>:</p>
<blockquote>
<p>Here you go — king of the alley, in full regalia:</p>
<p><img src="https://static.simonwillison.net/static/2026/meta-raccoon-trash-hat.jpg" alt="It's a photo of a raccoon wearing a disposable coffee cup and some newspaper and banana peel as a hat. It's pretty good" style="max-width: 100%;" /></p>
<p>Coffee cup crown, banana peel brim, newspaper feather. Peak raccoon fashion. Want me to make a wilder version — maybe night-vision, or wearing sunglasses too?</p>
</blockquote>
<p>My guess is that image generation in Meta AI is powered by their <a href="https://arxiv.org/abs/2309.15807">Emu model</a>, or an updated version of it. This is the same pattern as ChatGPT and Gemini image generation, where images are generated by a tool call (here that's <code>media.image_gen</code>).</p>
<p>The <code>image_gen</code> tool description said that it "saves the image to the sandbox", so my guess was that I'd then be able to run Python code against it. I tried:</p>
<blockquote>
<p>use python OpenCV to analyze that image and find out neat things about it</p>
</blockquote>
<p>Annoyingly it did <em>not</em> show me the code it ran, but it did create this image:</p>
<p><img src="https://static.simonwillison.net/static/2026/raccoon_analysis.png" alt="OpenCV image analysis dashboard titled &quot;OpenCV Analysis: Raccoon Trash Hat&quot; with four panels: top-left shows the original 1280x1280 photo of a raccoon wearing a trash-lid hat with a coffee cup and newspaper on top in an alley; top-right shows a Canny edge detection version with white edges on black background labeled &quot;Canny Edges - 4.6% density&quot;; bottom-left shows &quot;Top 5 Dominant Colors&quot; as swatches with values RGB(20, 19, 24): 35.3%, RGB(43, 43, 47): 34.2%, RGB(84, 80, 79): 18.3%, RGB(139, 135, 131): 6.3%, RGB(210, 213, 208): 5.8%; bottom-right shows &quot;Color Histograms&quot; line chart with Red, Green, and Blue channels plotted from 0-250 on x-axis and 0-35000 on y-axis, peaking around value 25-50." style="max-width: 100%;" /></p>
<p>Pretty neat! We can both generate images and then use them with Python tools.</p>
<p>Next, the <code>visual_grounding</code> tool. Knowing the names of the tools makes it easy to refer to them in prompts:</p>
<blockquote>
<p>Now try the container.visual_grounding tool</p>
</blockquote>
<p>The response (<a href="https://gist.github.com/simonw/72d64b325d33e5882b9818d825551b44">full response here</a>):</p>
<blockquote>
<p>Done — visual_grounding pinpointed every piece of the outfit with pixel-level precision:</p>
<p><img src="https://static.simonwillison.net/static/2026/meta-grounding.jpg" alt="Visual grounding analysis of a raccoon photo titled &quot;Visual Grounding: Found 8 objects&quot; with colored dot annotations on the image marking each detected object: coffee cup (515,205) in yellow, banana peel (569,261) in light yellow, newspaper (399,279) in blue, right eye (461,396) in magenta, left eye (582,397) in pink, nose (514,472) in cyan, raccoon (553,548) in green, and trash can lid (550,892) in red. A legend at the bottom lists all objects with their normalized 0-1000 coordinates. Footer text reads &quot;Coordinates are 0-1000 normalized. visual_grounding returned precise points for each component of the trash hat ensemble.&quot; Watermark reads &quot;Meta AI&quot;." style="max-width: 100%;" /></p>
<p>[...]</p>
<p>Want me to run it in bbox mode next to draw boxes around the fur texture, or count mode to see how many individual whiskers it can detect?</p>
</blockquote>
<p>Once again, Meta AI wrapped the results from the tool in a custom HTML visualization. That's clearly <code>point</code> mode, but how about the others?</p>
<p>I prompted:</p>
<blockquote>
<p>yes draw bounding boxes, can it do masks too?</p>
</blockquote>
<p>Response:</p>
<blockquote>
<p>Yes — bounding boxes work great. Masks? Not directly in <code>visual_grounding</code>; it only returns three formats: point, bbox, and count. No pixel-level segmentation masks.</p>
<p>Here's the bbox output:</p>
<p><img src="https://static.simonwillison.net/static/2026/meta-bbox.jpg" alt="Bounding box object detection visualization titled &quot;Bounding Boxes (visual_grounding)&quot; with subtitle &quot;8 objects detected — coordinates are 0-1000 normalized&quot; showing a raccoon photo with colored rectangular bounding boxes around detected objects: coffee cup in yellow [424,115,610,273] 186×158, banana peel in yellow [472,215,667,291] 195×76, newspaper in blue [305,223,548,315] 243×92, raccoon in green [270,240,892,845] 622×605, raccoon's face in purple [305,283,750,537] 445×254, right eye in magenta [442,379,489,413] 47×34, left eye in magenta [565,379,605,416] 40×37, and trash can lid in red [107,800,1000,998] 893×198. A legend at the bottom shows each object's name, coordinates, and pixel dimensions in colored cards. Watermark reads &quot;Meta AI&quot;." style="max-width: 100%;" /></p>
<p>What you’re seeing:</p>
<ul>
<li>
<strong>raccoon</strong>: big box from (270,240) to (892,845) — covers 62% of the width, basically the whole animal</li>
<li>
<strong>coffee cup</strong>: (424,115)-(610,273) — sits highest, 158px tall in the 0-1000 space</li>
<li>
<strong>banana peel</strong>: (472,215)-(667,291) — overlaps the cup's base, confirming it's draped over the side</li>
<li>
<strong>newspaper</strong>: (305,223)-(548,315) — left ear coverage</li>
<li>
<strong>trash can lid</strong>: (107,800)-(1000,998) — spans almost the full image width at the bottom</li>
<li>
<strong>face</strong>: (305,283)-(750,537) — tight crop around the mask</li>
<li>
<strong>eyes</strong>: both ~40×37 pixel boxes, perfectly level at y=379</li>
</ul>
<p>Notice how the boxes nest: face is inside raccoon, eyes inside face, hat pieces overlap each other but stay above the face box. The tool is doing real object localization, not just guessing centers.</p>
<hr />
<p><strong>About masks:</strong> since visual_grounding doesn't output them, I can fake them with OpenCV in the container. Two quick options:</p>
<ol>
<li>
<strong>GrabCut</strong> — I give it the raccoon bbox, it iteratively segments fur from background and returns a binary mask</li>
<li>
<strong>SAM-style color threshold</strong> — use the bbox plus k-means to isolate the coffee cup or banana peel</li>
</ol>
<p>Want me to generate an actual alpha mask for the raccoon (cut it out from the alley), or for the trash hat pieces separately? Takes about 10 seconds and I can give you a transparent PNG.</p>
</blockquote>
<p>Here's <a href="https://static.simonwillison.net/static/2026/meta-bbox.html">the HTML for that bbox visualization</a>.</p>
<p>Finally I had it dump out the raw JSON for point mode, bbox mode and count mode. You can <a href="https://gist.github.com/simonw/26bfdb22d7d73450c13f49abbf0c251a">see that all here</a>. The count mode is particularly interesting - here's that in full:</p>
<div class="highlight highlight-source-json"><pre>[
  {
    <span class="pl-ent">"object_name"</span>: <span class="pl-s"><span class="pl-pds">"</span>raccoon whisker<span class="pl-pds">"</span></span>,
    <span class="pl-ent">"points"</span>: [
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">403</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">445</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">397</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">458</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">392</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">472</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">391</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">485</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">398</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">497</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">415</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">512</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">618</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">446</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">622</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">461</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">625</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">475</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">628</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">488</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">635</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">501</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">651</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">518</span>}
    ],
    <span class="pl-ent">"count"</span>: <span class="pl-c1">12</span>
  },
  {
    <span class="pl-ent">"object_name"</span>: <span class="pl-s"><span class="pl-pds">"</span>raccoon paw claw<span class="pl-pds">"</span></span>,
    <span class="pl-ent">"points"</span>: [
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">406</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">845</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">424</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">847</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">441</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">842</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">473</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">833</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">596</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">830</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">635</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">844</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">660</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">843</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">679</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">838</span>}
    ],
    <span class="pl-ent">"count"</span>: <span class="pl-c1">8</span>
  },
  {
    <span class="pl-ent">"object_name"</span>: <span class="pl-s"><span class="pl-pds">"</span>trash item on head<span class="pl-pds">"</span></span>,
    <span class="pl-ent">"points"</span>: [
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">514</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">191</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">568</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">263</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">392</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">274</span>}
    ],
    <span class="pl-ent">"count"</span>: <span class="pl-c1">3</span>
  },
  {
    <span class="pl-ent">"object_name"</span>: <span class="pl-s"><span class="pl-pds">"</span>eye<span class="pl-pds">"</span></span>,
    <span class="pl-ent">"points"</span>: [
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">462</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">395</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">582</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">398</span>}
    ],
    <span class="pl-ent">"count"</span>: <span class="pl-c1">2</span>
  },
  {
    <span class="pl-ent">"object_name"</span>: <span class="pl-s"><span class="pl-pds">"</span>ear<span class="pl-pds">"</span></span>,
    <span class="pl-ent">"points"</span>: [
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">359</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">313</span>},
      {<span class="pl-ent">"x"</span>: <span class="pl-c1">680</span>, <span class="pl-ent">"y"</span>: <span class="pl-c1">294</span>}
    ],
    <span class="pl-ent">"count"</span>: <span class="pl-c1">2</span>
  }
]</pre></div>
<p>So Meta AI has the ability to count a raccoon's whiskers baked into the default set of tools.</p>
<p>Which means... it can count pelicans too!</p>
<p><img src="https://static.simonwillison.net/static/2026/meta-count-pelicans.jpg" alt="Screenshot of a Meta AI conversation where the user uploaded a photo of pelicans on rocks and asked &quot;Count the pelicans&quot;. response reads: &quot;That rocky haul-out is packed — I count 25 brown pelicans in your photo. I went bird by bird, including the ones tucked behind rocks, heads turned away, or just peeking out at the edges. They're all California brown pelicans, with those pale yellow heads and long bills, clustered on the riprap right at the waterline. To make it easy to verify, I've numbered each one on the image:&quot; followed by an annotated version of the photo with red numbered circles (1–25) placed on each pelican. The annotated image has a downward chevron and an &quot;Open&quot; button at the bottom right." style="max-width: 100%;" /></p>
<p>Here's that overlay <a href="https://static.simonwillison.net/static/2026/meta-count-pelicans.html">exported as HTML</a>.</p>

<p><em><strong>Update</strong>: Meta's <a href="https://twitter.com/jacktripleu/status/2042050863800447387">Jack Wu confirms</a> that these tools are part of the new harness they launched alongside the new model.</em></p>

<h4 id="maybe-open-weights-in-the-future-">Maybe open weights in the future?</h4>
<p>On Twitter <a href="https://twitter.com/alexandr_wang/status/2041909388852748717">Alexandr Wang said</a>:</p>
<blockquote>
<p>this is step one. bigger models are already in development with infrastructure scaling to match. private api preview open to select partners today, with plans to open-source future versions.</p>
</blockquote>
<p>I really hope they do go back to open-sourcing their models. Llama 3.1/3.2/3.3 were excellent laptop-scale model families, and the introductory blog post for Muse Spark had this to say about efficiency:</p>
<blockquote>
<p>[...] we can reach the same capabilities with over an order of magnitude less compute than our previous model, Llama 4 Maverick. This improvement also makes Muse Spark significantly more efficient than the leading base models available for comparison.</p>
</blockquote>
<p>So are Meta back in the frontier model game? <a href="https://twitter.com/ArtificialAnlys/status/2041913043379220801">Artificial Analysis</a> think so - they scored Meta Spark at 52, "behind only Gemini 3.1 Pro, GPT-5.4, and Claude Opus 4.6". Last year's Llama 4 Maverick and Scout scored 18 and 13 respectively.</p>
<p>I'm waiting for API access - while the tool collection on <a href="https://meta.ai/">meta.ai</a> is quite strong the real test of a model like this is still what we can build on top of it.</p>
    
        <p>Tags: <a href="https://simonwillison.net/tags/facebook">facebook</a>, <a href="https://simonwillison.net/tags/ai">ai</a>, <a href="https://simonwillison.net/tags/generative-ai">generative-ai</a>, <a href="https://simonwillison.net/tags/llms">llms</a>, <a href="https://simonwillison.net/tags/code-interpreter">code-interpreter</a>, <a href="https://simonwillison.net/tags/llm-tool-use">llm-tool-use</a>, <a href="https://simonwillison.net/tags/meta">meta</a>, <a href="https://simonwillison.net/tags/pelican-riding-a-bicycle">pelican-riding-a-bicycle</a>, <a href="https://simonwillison.net/tags/llm-reasoning">llm-reasoning</a>, <a href="https://simonwillison.net/tags/llm-release">llm-release</a></p>

Source: https://simonwillison.net/2026/Apr/8/muse-spark#atom-everything
