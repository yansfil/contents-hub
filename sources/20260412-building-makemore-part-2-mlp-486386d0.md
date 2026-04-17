---
type: youtube
url: "https://www.youtube.com/watch?v=TCH_1BHY58I"
title: "Building makemore Part 2: MLP"
collected_at: "2026-04-12T11:14:44.712139+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# Building makemore Part 2: MLP

> We implement a multilayer perceptron (MLP) character-level language model. In this video we also introduce many basics of machine learning (e.g. model training, learning rate tuning, hyperparameters, evaluation, train/dev/test splits, under/overfitting, etc.).

Links:
- makemore on github: https://github.com/karpathy/makemore
- jupyter notebook I built in this video: https://github.com/karpathy/nn-zero-to-hero/blob/master/lectures/makemore/makemore_part2_mlp.ipynb
- collab notebook (new)!!!: https://colab.research.google.com/drive/1YIfmkftLrz6MPTOO9Vwqrop2Q5llHIGK?usp=sharing
- Bengio et al. 2003 MLP language model paper (pdf): https://www.jmlr.org/papers/volume3/bengio03a/bengio03a.pdf
- my website: https://karpathy.ai
- my twitter: https://twitter.com/karpathy
- (new) Neural Networks: Zero to Hero series Discord channel: https://discord.gg/3zy8kqD9Cp , for people who'd like to chat more and go beyond youtube comments

Useful links:
- PyTorch internals ref http://blog.ezyang.com/2019/05/pytorch-internals/

Exercises:
- E01: Tune the hyperparameters of the training to beat my best validation loss of 2.2
- E02: I was not careful with the intialization of the network in this video. (1) What is the loss you'd get if the predicted probabilities at initialization were perfectly uniform? What loss do we achieve? (2) Can you tune the initialization to get a starting loss that is much more similar to (1)?
- E03: Read the Bengio et al 2003 paper (link above), implement and try any idea from the paper. Did it work?

Chapters:
00:00:00 intro
00:01:48 Bengio et al. 2003 (MLP language model) paper walkthrough
00:09:03 (re-)building our training dataset
00:12:19 implementing the embedding lookup table
00:18:35 implementing the hidden layer + internals of torch.Tensor: storage, views
00:29:15 implementing the output layer
00:29:53 implementing the negative log likelihood loss
00:32:17 summary of the full network
00:32:49 introducing F.cross_entropy and why
00:37:56 implementing the training loop, overfitting one batch
00:41:25 training on the full dataset, minibatches
00:45:40 finding a good initial learning rate
00:53:20 splitting up the dataset into train/val/test splits and why
01:00:49 experiment: larger hidden layer
01:05:27 visualizing the character embeddings
01:07:16 experiment: larger embedding size
01:11:46 summary of our final code, conclusion
01:13:24 sampling from the model
01:14:55 google collab (new!!) notebook advertisement

Source: https://www.youtube.com/watch?v=TCH_1BHY58I
