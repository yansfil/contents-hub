---
type: youtube
url: "https://www.youtube.com/watch?v=q8SA3rM6ckI"
title: "Building makemore Part 4: Becoming a Backprop Ninja"
collected_at: "2026-04-12T11:14:44.712116+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Building makemore Part 4: Becoming a Backprop Ninja

> We take the 2-layer MLP (with BatchNorm) from the previous video and backpropagate through it manually without using PyTorch autograd's loss.backward(): through the cross entropy loss, 2nd linear layer, tanh, batchnorm, 1st linear layer, and the embedding table. Along the way, we get a strong intuitive understanding about how gradients flow backwards through the compute graph and on the level of efficient Tensors, not just individual scalars like in micrograd. This helps build competence and intuition around how neural nets are optimized and sets you up to more confidently innovate on and debug modern neural networks.

!!!!!!!!!!!!
I recommend you work through the exercise yourself but work with it in tandem and whenever you are stuck unpause the video and see me give away the answer. This video is not super intended to be simply watched. The exercise is here:
https://colab.research.google.com/drive/1WV2oi2fh9XXyldh02wupFQX0wh5ZC-z-?usp=sharing
!!!!!!!!!!!!

Links:
- makemore on github: https://github.com/karpathy/makemore
- jupyter notebook I built in this video: https://github.com/karpathy/nn-zero-to-hero/blob/master/lectures/makemore/makemore_part4_backprop.ipynb
- collab notebook: https://colab.research.google.com/drive/1WV2oi2fh9XXyldh02wupFQX0wh5ZC-z-?usp=sharing
- my website: https://karpathy.ai
- my twitter: https://twitter.com/karpathy
- our Discord channel: https://discord.gg/3zy8kqD9Cp

Supplementary links:
- Yes you should understand backprop: https://karpathy.medium.com/yes-you-should-understand-backprop-e2f06eab496b
- BatchNorm paper: https://arxiv.org/abs/1502.03167
- Bessel’s Correction: http://math.oxford.emory.edu/site/math117/besselCorrection/
- Bengio et al. 2003 MLP LM https://www.jmlr.org/papers/volume3/bengio03a/bengio03a.pdf 

Chapters:
00:00:00 intro: why you should care & fun history
00:07:26 starter code
00:13:01 exercise 1: backproping the atomic compute graph
01:05:17 brief digression: bessel’s correction in batchnorm
01:26:31 exercise 2: cross entropy loss backward pass
01:36:37 exercise 3: batch norm layer backward pass
01:50:02 exercise 4: putting it all together
01:54:24 outro

Source: https://www.youtube.com/watch?v=q8SA3rM6ckI
