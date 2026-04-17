---
type: youtube
url: "https://www.youtube.com/watch?v=VMj-3S1tku0"
title: "The spelled-out intro to neural networks and backpropagation: building micrograd"
collected_at: "2026-04-12T11:14:44.712188+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# The spelled-out intro to neural networks and backpropagation: building micrograd

> This is the most step-by-step spelled-out explanation of backpropagation and training of neural networks. It only assumes basic knowledge of Python and a vague recollection of calculus from high school.

Links:
- micrograd on github: https://github.com/karpathy/micrograd
- jupyter notebooks I built in this video: https://github.com/karpathy/nn-zero-to-hero/tree/master/lectures/micrograd
- my website: https://karpathy.ai
- my twitter: https://twitter.com/karpathy
- "discussion forum": nvm, use youtube comments below for now :)
- (new) Neural Networks: Zero to Hero series Discord channel: https://discord.gg/3zy8kqD9Cp , for people who'd like to chat more and go beyond youtube comments

Exercises:
you should now be able to complete the following google collab, good luck!:
https://colab.research.google.com/drive/1FPTx1RXtBfc4MaTkf7viZZD4U2F9gtKN?usp=sharing

Chapters:
00:00:00 intro
00:00:25 micrograd overview
00:08:08 derivative of a simple function with one input
00:14:12 derivative of a function with multiple inputs
00:19:09 starting the core Value object of micrograd and its visualization
00:32:10 manual backpropagation example #1: simple expression
00:51:10 preview of a single optimization step
00:52:52 manual backpropagation example #2: a neuron
01:09:02 implementing the backward function for each operation
01:17:32 implementing the backward function for a whole expression graph
01:22:28 fixing a backprop bug when one node is used multiple times
01:27:05 breaking up a tanh, exercising with more operations
01:39:31 doing the same thing but in PyTorch: comparison
01:43:55 building out a neural net library (multi-layer perceptron) in micrograd
01:51:04 creating a tiny dataset, writing the loss function
01:57:56 collecting all of the parameters of the neural net
02:01:12 doing gradient descent optimization manually, training the network
02:14:03 summary of what we learned, how to go towards modern neural nets
02:16:46 walkthrough of the full code of micrograd on github
02:21:10 real stuff: diving into PyTorch, finding their backward pass for tanh
02:24:39 conclusion
02:25:20 outtakes :)

Source: https://www.youtube.com/watch?v=VMj-3S1tku0
