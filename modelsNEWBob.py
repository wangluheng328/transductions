# Imports
from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random

import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

from attention import create_mask, MultiplicativeAttention, AdditiveAttention, DotproductAttention, PositionAttention

import sys
import os

# Functions for tracking time
import time
import math

use_cuda = torch.cuda.is_available()

if use_cuda:
    available_device = torch.device('cuda')
else:
    available_device = torch.device('cpu')

# Generic sequential encoder
class EncoderRNN(nn.Module):
    def __init__(self, vocab_size, hidden_size, recurrent_unit, num_layers=1, max_length=30, dropout=0):
        super(EncoderRNN, self).__init__()
        self.num_layers = num_layers
        self.hidden_size = hidden_size
        self.rnn_type = recurrent_unit
        self.max_length = max_length
        self.dropout = nn.Dropout(p=dropout)

        self.embedding = nn.Embedding(vocab_size, hidden_size)

        if recurrent_unit == "SRN":
                self.rnn = nn.RNN(hidden_size, hidden_size, num_layers = num_layers, dropout = dropout)
        elif recurrent_unit == "GRU":
                self.rnn = nn.GRU(hidden_size, hidden_size, num_layers = num_layers, dropout = dropout)
        elif recurrent_unit == "LSTM":
                self.rnn = nn.LSTM(hidden_size, hidden_size, num_layers = num_layers, dropout = dropout)
        else:
                print("Invalid recurrent unit type")

    def forward(self, batch):

        embedded_source = self.dropout(self.embedding(batch))
        outputs, final_hiddens = self.rnn(embedded_source)
        if self.rnn_type == 'LSTM':
            final_hiddens = final_hiddens[0] # ignore cell state
        final_output = outputs[-1]
        #only return the last timestep's h vectors for the last encoder layer 
        #Note that doing this should make the values of final_output and final_hiddens the same!
        final_hiddens = final_hiddens[-1]

        return final_output, final_hiddens, outputs

# Generic sequential decoder
class DecoderRNN(nn.Module):
    def __init__(self, hidden_size, vocab, encoder_vocab, recurrent_unit, embedding_size=None, attention_type=None, num_layers=1, dropout=0, max_length=30):
        super(DecoderRNN, self).__init__()
        self.vocab = vocab
        self.vocab_size = len(vocab)
        self.eos_index = self.vocab.stoi['<eos>']
        self.pad_index = self.vocab.stoi['<pad>']
        self.encoder_vocab = encoder_vocab
        self.hidden_size = hidden_size
        self.embedding_size = hidden_size if embedding_size == None else embedding_size
        self.embedding = nn.Embedding(self.vocab_size, hidden_size)
        self.num_layers = num_layers
        self.max_length = max_length
        self.attention_type = attention_type
        self.recurrent_unit_type = recurrent_unit

        self.embedding = nn.Embedding(self.vocab_size, self.hidden_size)
        self.dropout = nn.Dropout(dropout)
        self.out = nn.Linear(self.hidden_size, self.vocab_size)

        if recurrent_unit == "SRN":
                self.rnn = nn.RNNCell(self.embedding_size + (hidden_size if attention_type else 0), 
                                      hidden_size)
        elif recurrent_unit == "GRU":
                self.rnn = nn.GRUCell(self.embedding_size + (hidden_size if attention_type else 0),
                                      hidden_size)
        elif recurrent_unit == "LSTM":
                self.rnn = nn.LSTMCell(self.embedding_size + (hidden_size if attention_type else 0),
                                      hidden_size)
        else:
                print("Invalid recurrent unit type")

        # location-based attention
        if attention_type == "location":
                self.attn = PositionAttention(hidden_size, max_length = self.max_length)
        # additive/content-based (Bahdanau) attention
        if attention_type == "additive": 
                self.attn = AdditiveAttention(hidden_size)
        #multiplicative (key-value) attention
        if attention_type == "multiplicative":
                self.attn = MultiplicativeAttention(hidden_size)
        #dot product attention
        if attention_type == "dotproduct":
                self.attn = DotproductAttention()

    def forwardStep(self, x, h, encoder_outputs, source_mask):
        x = self.embedding(x)
        #Apply ReLU to embedded input?
        rnn_input = F.relu(x)
        
        if self.attention_type:
            #use h alone for attention key in case we're dealing with LSTM
            if isinstance(h,tuple):
                hidden = h[0]
            else:
                hidden = h
            a = self.attn(encoder_outputs, hidden, source_mask) 
            #attn_weights = [batch size, src len]
            a = a.unsqueeze(1)
            #a = [batch size, 1, src len]
            encoder_outputs = encoder_outputs.permute(1,0,2)
            #encoder_hiddens = [batch size, src len, enc hid dim]
            weighted_encoder_outputs = torch.bmm(a, encoder_outputs)
            #weighted_encoder_outputs = [batch size, 1, enc hid dim]
            weighted_encoder_outputs = weighted_encoder_outputs.squeeze(1)
            #weighted_encoder_rep = [batch size, enc hid dim]
            rnn_input = torch.cat((rnn_input, weighted_encoder_outputs), dim=1)
        else:
             a = torch.zeros(encoder_outputs.shape[1], 1, encoder_outputs.shape[0])
        
        batch_size = rnn_input.shape[0] 
        state = self.rnn(rnn_input, h)
        h = state[0] if isinstance(state,tuple) else state #For LSTM pass only h to output
        y = self.out(h)
        return y, state, a.squeeze(1)

    # Perform the full forward pass
    def forward(self, h0, x0, encoder_outputs, source, target=None, tf_ratio=0.5, evaluation=False):
        batch_size = encoder_outputs.shape[1]
        outputs = torch.zeros(self.max_length, batch_size, self.vocab_size)
        decoder_hiddens = torch.zeros(self.max_length, batch_size, self.hidden_size)
        attention = torch.zeros(self.max_length, batch_size, encoder_outputs.shape[0])

        #if we're evaluating, never use teacher forcing
        if (evaluation or not(torch.is_tensor(target))):
            tf_ratio=0.0
            gen_length = self.max_length
        else: 
            #if we're doing teacher forcing, don't generate past the length of the target
            gen_length = target.shape[0]
        
        source_mask = create_mask(source, self.encoder_vocab)
        
        #initialize x and h to given initial values. 
        # Assumes that the first position in target is <bos> and can be ignored.
        x, h = torch.tensor([self.vocab.stoi[x] for x in x0]), h0
        output_complete_flag = torch.zeros(batch_size, dtype=torch.bool)
        for i in range(gen_length): 
            if self.recurrent_unit_type == "LSTM":
                if not(isinstance(h0,tuple)):
                    c0 = torch.zeros(batch_size, self.hidden_size)
                    h = (h0, c0)
            y, h, a = self.forwardStep(x, h, encoder_outputs, source_mask)
            outputs[i] = y
            attention[i] = a
            decoder_hiddens[i] = h if self.recurrent_unit_type is not "LSTM" else h[0]
            if (evaluation | (random.random() > tf_ratio)):
                x = y.argmax(dim=1)
            else:
                x = target[i]  
            #stop if all of the examples in the batch include eos or pad
            output_complete_flag += ((x == self.eos_index) | (x == self.pad_index))
            if all(output_complete_flag):
                break
        return outputs[:gen_length]#, decoder_hiddens[:i+1], attention[:i+1]


# GRU modified such that its hidden states are not bounded
class UnsquashedGRU(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(UnsquashedGRU, self).__init__()

        self.hidden_size = hidden_size
        self.input_size = input_size

        self.wr = nn.Linear(hidden_size + input_size, hidden_size)
        self.wz = nn.Linear(hidden_size + input_size, hidden_size)
        self.wv = nn.Linear(hidden_size + input_size, hidden_size)
        self.wx = nn.Linear(input_size, hidden_size)
        self.urh = nn.Linear(hidden_size, hidden_size)


    def forward(self, input, hidden):
        hx = hidden
        input_plus_hidden = torch.cat((input, hx), 2)
        r_t = F.sigmoid(self.wr(input_plus_hidden))
        z_t = F.sigmoid(self.wz(input_plus_hidden))
        v_t = F.sigmoid(self.wv(input_plus_hidden))
        h_tilde = F.tanh(self.wx(input) + self.urh(r_t * hx))
        h_t = z_t * hx + v_t * h_tilde

        return h_t, h_t


# CumMax function for use in the ON-LSTM
class CumMax(nn.Module):
        def __init__(self):
                super(CumMax, self).__init__()

        def forward(self, input):
                return torch.cumsum(nn.Softmax(dim=2)(input), 2)

# Ordered Neurons LSTM recurrent unit
class ONLSTM(nn.Module):
        def __init__(self, input_size, hidden_size):
                super(ONLSTM, self).__init__()

                self.hidden_size = hidden_size
                self.input_size = input_size

                self.wi = nn.Linear(hidden_size + input_size, hidden_size)
                self.wf = nn.Linear(hidden_size + input_size, hidden_size)
                self.wg = nn.Linear(hidden_size + input_size, hidden_size)
                self.wo = nn.Linear(hidden_size + input_size, hidden_size)
                self.wftilde = nn.Linear(hidden_size + input_size, hidden_size)
                self.witilde = nn.Linear(hidden_size + input_size, hidden_size)

        def forward(self, input, hidden):
                hx, cx = hidden
                input_plus_hidden = torch.cat((input, hx), 2)

                f_t = F.sigmoid(self.wf(input_plus_hidden))
                i_t = F.sigmoid(self.wi(input_plus_hidden))
                o_t = F.sigmoid(self.wo(input_plus_hidden))
                c_hat_t = F.tanh(self.wg(input_plus_hidden))

                f_tilde_t = CumMax()(self.wftilde(input_plus_hidden))
                i_tilde_t = 1 - CumMax()(self.witilde(input_plus_hidden))

                omega_t = f_tilde_t * i_tilde_t
                f_hat_t = f_t * omega_t + (f_tilde_t - omega_t)
                i_hat_t = i_t * omega_t + (i_tilde_t - omega_t)

                cx = f_hat_t * cx + i_hat_t * c_hat_t
                hx = o_t * F.tanh(cx)

                return hx, (hx, cx)

# LSTM modified so that both its hidden and cell states are bounded
class SquashedLSTM(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(SquashedLSTM, self).__init__()

        self.hidden_size = hidden_size
        self.input_size = input_size

        self.wi = nn.Linear(hidden_size + input_size, hidden_size)
        self.wf = nn.Linear(hidden_size + input_size, hidden_size)
        self.wg = nn.Linear(hidden_size + input_size, hidden_size)
        self.wo = nn.Linear(hidden_size + input_size, hidden_size)


    def forward(self, input, hidden):
        hx, cx = hidden
        input_plus_hidden = torch.cat((input, hx), 2)
        i_t = F.sigmoid(self.wi(input_plus_hidden))
        f_t = F.sigmoid(self.wf(input_plus_hidden))
        g_t = F.tanh(self.wg(input_plus_hidden))
        o_t = F.sigmoid(self.wo(input_plus_hidden))

        sum_fi = f_t + i_t

        cx = (f_t * cx + i_t * g_t)/sum_fi
        hx = o_t * F.tanh(cx)

        return hx, (hx, cx)

# Tree-based encoder
# This implements the Tree-GRU of Chen et al. 2017, described
# in section 3.2 of this paper: https://arxiv.org/pdf/1707.05436.pdf
class TreeEncoderRNN(nn.Module):
    def __init__(self, vocab_size, hidden_size):
        super(TreeEncoderRNN, self).__init__()
        self.hidden_size = hidden_size
        emb_size = hidden_size
        self.emb_size = hidden_size
        self.embedding = nn.Embedding(vocab_size, emb_size)


        self.l_rl = nn.Linear(hidden_size, hidden_size)
        self.r_rl = nn.Linear(hidden_size, hidden_size)

        self.l_rr = nn.Linear(hidden_size, hidden_size)
        self.r_rr = nn.Linear(hidden_size, hidden_size)

        self.l_zl = nn.Linear(hidden_size, hidden_size)
        self.r_zl = nn.Linear(hidden_size, hidden_size)


        self.l_zr = nn.Linear(hidden_size, hidden_size)
        self.r_zr = nn.Linear(hidden_size, hidden_size)

        self.l_z = nn.Linear(hidden_size, hidden_size)
        self.r_z = nn.Linear(hidden_size, hidden_size)

        self.l = nn.Linear(hidden_size, hidden_size)
        self.r = nn.Linear(hidden_size, hidden_size)


    def forward(self, training_set):
        input_seq = training_set[0]
        tree = training_set[2]
        embedded_seq = []

        for elt in input_seq:
            embedded_seq.append(self.embedding(Variable(torch.LongTensor([elt])).to(device=available_device)).unsqueeze(0))

        # current_level starts out as a list of word embeddings for the words in the sequence
        # Then, the tree (which is passed as an input - the element at index 2 of training_set) is used to 
        # determine which 2 things in current_level should be merged together to form a new, single unit
        # Those 2 things are replaced with their merged version in current_level, and that process repeats, 
        # with each time step merging at least one pair of adjacent elements in current_level to create a 
        # single new element, until current_level only contains one element. This element is then a single 
        # embedding for the whole tree, and it is what is returned.
        current_level = embedded_seq
        for level in tree:
            next_level = []

            for node in level:

                if len(node) == 1:
                    next_level.append(current_level[node[0]])
                    continue
                left = node[0]
                right = node[1]


                r_l = nn.Sigmoid()(self.l_rl(current_level[left]) + self.r_rl(current_level[right]))
                r_r = nn.Sigmoid()(self.l_rr(current_level[left]) + self.r_rr(current_level[right]))
                z_l = nn.Sigmoid()(self.l_zl(current_level[left]) + self.r_zl(current_level[right]))
                z_r = nn.Sigmoid()(self.l_zr(current_level[left]) + self.r_zr(current_level[right]))
                z = nn.Sigmoid()(self.l_z(current_level[left]) + self.r_z(current_level[right]))
                h_tilde = nn.Tanh()(self.l(r_l * current_level[left]) + self.r(r_r * current_level[right]))
                hidden = z_l * current_level[left] + z_r * current_level[right] + z * h_tilde

                next_level.append(hidden)

            current_level = next_level

        return current_level[0], current_level[0], current_level[0]

# Tree-based decoder
# This implements the binary tree decoder of Chen et al. 2018, described in
# section 3.2 of this paper: http://papers.nips.cc/paper/7521-tree-to-tree-neural-networks-for-program-translation.pdf
# The only difference is that we have implemented it as a GRU instead of an LSTM, but this is
# a straightforward modification of their setup
class TreeDecoderRNN(nn.Module):
    def __init__(self, vocab_size, hidden_size):
        super(TreeDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.word_out = nn.Linear(hidden_size, vocab_size)
        self.rnn_l = nn.GRU(hidden_size, hidden_size)
        self.rnn_r = nn.GRU(hidden_size, hidden_size)

    def forward(self, hidden, encoder_outputs, training_set, tf_ratio=0.5, evaluation=False): 
        encoding = hidden
        tree = training_set[3]

        tree_to_use = tree[::-1][1:]

        current_layer = [encoding]

        # This works in revers of the tree encoder: start with a single vector encoding, then 
        # output 2 children from it, and repeat until the whole tree has been generated
        for layer in tree_to_use:
            next_layer = []
            for index, node in enumerate(layer):
                if len(node) == 1:
                    next_layer.append(current_layer[index])
                else:

                    output, left = self.rnn_l(Variable(torch.zeros(1,1,self.hidden_size)).to(device=available_device), current_layer[index])
                    output, right = self.rnn_r(Variable(torch.zeros(1,1,self.hidden_size)).to(device=available_device), current_layer[index])

                    next_layer.append(left)
                    next_layer.append(right)
            current_layer = next_layer

        # Apply a linear layer to each leaf embedding to determine what word is at that leaf
        words_out = []
        for elt in current_layer:
            words_out.append(nn.LogSoftmax()(self.word_out(elt).view(-1).unsqueeze(0)))

        return words_out


class TridentDecoder(nn.Module):
    def __init__(self, arity, vocab_size, hidden_size, max_depth, null_placement="pre"):
        super(TridentDecoder, self).__init__()

        self.arity = arity
        self.null_placement = null_placement

        self.hidden_size = hidden_size
        self.vocab_size = vocab_size + 1 # first one is for null
        self.max_depth = max_depth

        self.hidden2vocab = nn.Sequential(nn.Linear(self.hidden_size, 2*self.hidden_size), nn.Sigmoid(), nn.Linear(2*self.hidden_size, self.vocab_size))
        self.to_children = nn.Sequential(nn.Linear(self.hidden_size, 2*self.hidden_size), nn.Sigmoid(), nn.Linear(2*self.hidden_size, self.arity * self.hidden_size))

    @property
    def null_ix(self):
        return 0

    def forward(self, root_hidden, training_set):
        if self.training:
            # assumption: every non-terminal node either has 3 children which are all non-terminals, or is the parent of one terminal node
            tree = training_set[3]
            raw_scores = torch.stack(list(self.forward_train_helper(root_hidden, tree)))
            return F.log_softmax(raw_scores, dim=1)
        else:
            raw_scores = torch.stack(list(self.forward_eval_helper(root_hidden)))
            return F.log_softmax(raw_scores, dim=1)

    def forward_train_helper(self, root_hidden, root):
        production = self.hidden2vocab(root_hidden)
        if len(root) > 1:
            assert len(root) == self.arity

            assert self.null_placement == "pre"
            yield production
            children_hidden = self._hidden2children(root_hidden)
            for child_ix in range(self.arity):
                yield from self.forward_train_helper(children_hidden[child_ix], root[child_ix])
        else:
            yield production

    def forward_eval_helper(self, root_hidden, depth=0):
        production = self.hidden2vocab(root_hidden)

        if (torch.argmax(production) == self.null_ix) and (depth <= self.max_depth):
            # we chose NOT to output a word here... recurse more
            children_hidden = self._hidden2children(root_hidden)
            for child_ix in range(self.arity):
                yield from self.forward_eval_helper(children_hidden[child_ix], depth+1)
        else:
            yield production

    def _hidden2children(self, hidden):
        return torch.split(self.to_children(hidden), self.hidden_size, dim=-1)

class GRUTridentDecoder(nn.Module):
    def __init__(self, arity, vocab_size, hidden_size, max_depth, null_placement="pre"):
        super(GRUTridentDecoder, self).__init__()

        self.arity = arity
        self.null_placement = null_placement

        self.hidden_size = hidden_size
        self.vocab_size = vocab_size + 1 # first one is for null
        self.max_depth = max_depth

        self.hidden2vocab = nn.Sequential(nn.Linear(self.hidden_size, 2*self.hidden_size), nn.Sigmoid(), nn.Linear(2*self.hidden_size, self.vocab_size))
        
        # IDEA: in the future, have just one GRU for all the children but feed in a different input for each instead of always using this same dumb vector 
        self.null_vector = torch.zeros(self.hidden_size, requires_grad=False).unsqueeze(0)
        self.per_child_cell = nn.ModuleList()
        for _ in range(self.arity):
            self.per_child_cell.append(nn.GRUCell(self.hidden_size, self.hidden_size))
        
    @property
    def null_ix(self):
        return 0

    def forward(self, root_hidden, training_set):
        root_hidden = root_hidden.unsqueeze(0)
        if self.training:
            # assumption: every non-terminal node either has 3 children which are all non-terminals, or is the parent of one terminal node
            tree = training_set[3]
            raw_scores = torch.stack(list(self.forward_train_helper(root_hidden, tree)))
            return F.log_softmax(raw_scores, dim=1)
        else:
            raw_scores = torch.stack(list(self.forward_eval_helper(root_hidden)))
            return F.log_softmax(raw_scores, dim=1)

    def forward_train_helper(self, root_hidden, root):
        production = self.hidden2vocab(root_hidden)[0]
        if len(root) > 1:
            assert len(root) == self.arity

            assert self.null_placement == "pre"
            yield production
            for child_ix in range(self.arity):
                child_hidden = self.per_child_cell[child_ix](self.null_vector, root_hidden)
                yield from self.forward_train_helper(child_hidden, root[child_ix])
        else:
            yield production

    def forward_eval_helper(self, root_hidden, depth=0):
        production = self.hidden2vocab(root_hidden)[0]
        if (torch.argmax(production) == self.null_ix) and (depth <= self.max_depth):
            # we chose NOT to output a word here... recurse more
            for child_ix in range(self.arity):
                child_hidden = self.per_child_cell[child_ix](self.null_vector, root_hidden)
                yield from self.forward_eval_helper(child_hidden, depth+1)
        else:
            yield production

class AltGRUTridentDecoder(nn.Module):
    def __init__(self, arity, vocab_size, hidden_size, max_depth, null_placement="pre"):
        super(GRUTridentDecoder, self).__init__()

        self.arity = arity
        self.null_placement = null_placement

        self.hidden_size = hidden_size
        self.vocab_size = vocab_size + 1 # first one is for null
        self.max_depth = max_depth

        self.hidden2vocab = nn.Sequential(nn.Linear(self.hidden_size, 2*self.hidden_size), nn.Sigmoid(), nn.Linear(2*self.hidden_size, self.vocab_size))
        
        # IDEA: in the future, have just one GRU for all the children but feed in a different input for each instead of always using this same dumb vector 
        self.null_vector = torch.zeros(self.hidden_size, requires_grad=False).unsqueeze(0)
        self.child_toks = nn.ModuleList()
        for _ in range(self.arity):
            pass
            #self.child_toks.append()
        
    @property
    def null_ix(self):
        return 0

    def forward(self, root_hidden, training_set):
        root_hidden = root_hidden.unsqueeze(0)
        if self.training:
            # assumption: every non-terminal node either has 3 children which are all non-terminals, or is the parent of one terminal node
            tree = training_set[3]
            raw_scores = torch.stack(list(self.forward_train_helper(root_hidden, tree)))
            return F.log_softmax(raw_scores, dim=1)
        else:
            raw_scores = torch.stack(list(self.forward_eval_helper(root_hidden)))
            return F.log_softmax(raw_scores, dim=1)

    def forward_train_helper(self, root_hidden, root):
        production = self.hidden2vocab(root_hidden)[0]
        if len(root) > 1:
            assert len(root) == self.arity

            assert self.null_placement == "pre"
            yield production
            for child_ix in range(self.arity):
                child_hidden = self.per_child_cell[child_ix](self.null_vector, root_hidden)
                yield from self.forward_train_helper(child_hidden, root[child_ix])
        else:
            yield production

    def forward_eval_helper(self, root_hidden, depth=0):
        production = self.hidden2vocab(root_hidden)[0]
        if (torch.argmax(production) == self.null_ix) and (depth <= self.max_depth):
            # we chose NOT to output a word here... recurse more
            for child_ix in range(self.arity):
                child_hidden = self.per_child_cell[child_ix](self.null_vector, root_hidden)
                yield from self.forward_eval_helper(child_hidden, depth+1)
        else:
            yield production

class Seq2Seq(nn.Module):
    def __init__(self, encoder, decoder):
        super(Seq2Seq, self).__init__()
        self.encoder = encoder
        self.decoder = decoder
        
    def forward(self, training_pair):
        encoder_output, encoder_hidden, encoder_outputs = self.encoder(training_pair)
        decoder_hidden = encoder_hidden[0,0,:]
        decoder_outputs = self.decoder(decoder_hidden, training_pair)
        return decoder_outputs