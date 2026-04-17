---
type: youtube
url: "https://www.youtube.com/watch?v=PaCmpygFfXo"
title: "The spelled-out intro to language modeling: building makemore"
collected_at: "2026-04-12T11:14:44.712151+00:00"
status: pending
tags:
  - ai-research
origin: subscription
---
# The spelled-out intro to language modeling: building makemore

> We implement a bigram character-level language model, which we will further complexify in followup videos into a modern Transformer language model, like GPT. In this video, the focus is on (1) introducing torch.Tensor and its subtleties and use in efficiently evaluating neural networks and (2) the overall framework of language modeling that includes model training, sampling, and the evaluation of a loss (e.g. the negative log likelihood for classification).

Links:
- makemore on github: https://github.com/karpathy/makemore
- jupyter notebook I built in this video: https://github.com/karpathy/nn-zero-to-hero/blob/master/lectures/makemore/makemore_part1_bigrams.ipynb
- my website: https://karpathy.ai
- my twitter: https://twitter.com/karpathy
- (new) Neural Networks: Zero to Hero series Discord channel: https://discord.gg/3zy8kqD9Cp , for people who'd like to chat more and go beyond youtube comments

Useful links for practice:
- Python + Numpy tutorial from CS231n https://cs231n.github.io/python-numpy-tutorial/ . We use torch.tensor instead of numpy.array in this video. Their design (e.g. broadcasting, data types, etc.) is so similar that practicing one is basically practicing the other, just be careful with some of the APIs - how various functions are named, what arguments they take, etc. - these details can vary.
- PyTorch tutorial on Tensor https://pytorch.org/tutorials/beginner/basics/tensorqs_tutorial.html
- Another PyTorch intro to Tensor https://pytorch.org/tutorials/beginner/nlp/pytorch_tutorial.html

Exercises:
E01: train a trigram language model, i.e. take two characters as an input to predict the 3rd one. Feel free to use either counting or a neural net. Evaluate the loss; Did it improve over a bigram model?
E02: split up the dataset randomly into 80% train set, 10% dev set, 10% test set. Train the bigram and trigram models only on the training set. Evaluate them on dev and test splits. What can you see?
E03: use the dev set to tune the strength of smoothing (or regularization) for the trigram model - i.e. try many possibilities and see which one works best based on the dev set loss. What patterns can you see in the train and dev set loss as you tune this strength? Take the best setting of the smoothing and evaluate on the test set once and at the end. How good of a loss do you achieve?
E04: we saw that our 1-hot vectors merely select a row of W, so producing these vectors explicitly feels wasteful. Can you delete our use of F.one_hot in favor of simply indexing into rows of W?
E05: look up and use F.cross_entropy instead. You should achieve the same result. Can you think of why we'd prefer to use F.cross_entropy instead?
E06: meta-exercise! Think of a fun/interesting exercise and complete it.

Chapters:
00:00:00 intro
00:03:03 reading and exploring the dataset
00:06:24 exploring the bigrams in the dataset
00:09:24 counting bigrams in a python dictionary
00:12:45 counting bigrams in a 2D torch tensor ("training the model")
00:18:19 visualizing the bigram tensor
00:20:54 deleting spurious (S) and (E) tokens in favor of a single . token
00:24:02 sampling from the model
00:36:17 efficiency! vectorized normalization of the rows, tensor broadcasting 
00:50:14 loss function (the negative log likelihood of the data under our model)
01:00:50 model smoothing with fake counts
01:02:57 PART 2: the neural network approach: intro
01:05:26 creating the bigram dataset for the neural net
01:10:01 feeding integers into neural nets? one-hot encodings
01:13:53 the "neural net": one linear layer of neurons implemented with matrix multiplication
01:18:46 transforming neural net outputs into probabilities: the softmax
01:26:17 summary, preview to next steps, reference to micrograd
01:35:49 vectorized loss
01:38:36 backward and update, in PyTorch
01:42:55 putting everything together
01:47:49 note 1: one-hot encoding really just selects a row of the next Linear layer's weight matrix
01:50:18 note 2: model smoothing as regularization loss
01:54:31 sampling from the neural net
01:56:16 conclusion

Source: https://www.youtube.com/watch?v=PaCmpygFfXo
