# https://pytorch.org/tutorials/beginner/chatbot_tutorial.html

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import torch
from torch.jit import script, trace
import torch.nn as nn
from torch import optim
import torch.nn.functional as F
import csv
import random
import re
import os
import unicodedata
import codecs
from io import open
import itertools
import math
import argparse
import sys

USE_CUDA = torch.cuda.is_available()
device = torch.device("cuda" if USE_CUDA else "cpu")


# Default word tokens
PAD_token = 0  # Used for padding short sentences
SOS_token = 1  # Start-of-sentence token
EOS_token = 2  # End-of-sentence token

class Voc:
    def __init__(self, name):
        self.name = name
        self.trimmed = False
        self.word2index = {}
        self.word2count = {}
        self.index2word = {PAD_token: "PAD", SOS_token: "SOS", EOS_token: "EOS"}
        self.num_words = 3  # Count SOS, EOS, PAD

    def addSentence(self, sentence):
        for word in sentence.split(' '):
            self.addWord(word)

    def addWord(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.num_words
            self.word2count[word] = 1
            self.index2word[self.num_words] = word
            self.num_words += 1
        else:
            self.word2count[word] += 1

    # Remove words below a certain count threshold
    def trim(self, min_count):
        if self.trimmed:
            return
        self.trimmed = True

        keep_words = []

        for k, v in self.word2count.items():
            if v >= min_count:
                keep_words.append(k)

        print('keep_words {} / {} = {:.4f}'.format(
            len(keep_words), len(self.word2index), len(keep_words) / len(self.word2index)
        ))

        # Reinitialize dictionaries
        self.word2index = {}
        self.word2count = {}
        self.index2word = {PAD_token: "PAD", SOS_token: "SOS", EOS_token: "EOS"}
        self.num_words = 3 # Count default tokens

        for word in keep_words:
            self.addWord(word)

class EncoderRNN(nn.Module):
    def __init__(self, hidden_size, embedding, n_layers=1, dropout=0):
        super(EncoderRNN, self).__init__()
        self.n_layers = n_layers
        self.hidden_size = hidden_size
        self.embedding = embedding

        # Initialize GRU; the input_size and hidden_size params are both set to 'hidden_size'
        #   because our input size is a word embedding with number of features == hidden_size
        self.gru = nn.GRU(hidden_size, hidden_size, n_layers,
                          dropout=(0 if n_layers == 1 else dropout), bidirectional=True)

    def forward(self, input_seq, input_lengths, hidden=None):
        # Convert word indexes to embeddings
        embedded = self.embedding(input_seq)
        # Pack padded batch of sequences for RNN module
        packed = nn.utils.rnn.pack_padded_sequence(embedded, input_lengths)
        # Forward pass through GRU
        outputs, hidden = self.gru(packed, hidden)
        # Unpack padding
        outputs, _ = nn.utils.rnn.pad_packed_sequence(outputs)
        # Sum bidirectional GRU outputs
        outputs = outputs[:, :, :self.hidden_size] + outputs[:, : ,self.hidden_size:]
        # Return output and final hidden state
        return outputs, hidden

# Luong attention layer
class Attn(nn.Module):
    def __init__(self, method, hidden_size):
        super(Attn, self).__init__()
        self.method = method
        if self.method not in ['dot', 'general', 'concat']:
            raise ValueError(self.method, "is not an appropriate attention method.")
        self.hidden_size = hidden_size
        if self.method == 'general':
            self.attn = nn.Linear(self.hidden_size, hidden_size)
        elif self.method == 'concat':
            self.attn = nn.Linear(self.hidden_size * 2, hidden_size)
            self.v = nn.Parameter(torch.FloatTensor(hidden_size))

    def dot_score(self, hidden, encoder_output):
        return torch.sum(hidden * encoder_output, dim=2)

    def general_score(self, hidden, encoder_output):
        energy = self.attn(encoder_output)
        return torch.sum(hidden * energy, dim=2)

    def concat_score(self, hidden, encoder_output):
        energy = self.attn(torch.cat((hidden.expand(encoder_output.size(0), -1, -1),
                                                    encoder_output), 2)).tanh()
        return torch.sum(self.v * energy, dim=2)

    def forward(self, hidden, encoder_outputs):
        # Calculate the attention weights (energies) based on the given method
        if self.method == 'general':
            attn_energies = self.general_score(hidden, encoder_outputs)
        elif self.method == 'concat':
            attn_energies = self.concat_score(hidden, encoder_outputs)
        elif self.method == 'dot':
            attn_energies = self.dot_score(hidden, encoder_outputs)

        # Transpose max_length and batch_size dimensions
        attn_energies = attn_energies.t()

        # Return the softmax normalized probability scores (with added dimension)
        return F.softmax(attn_energies, dim=1).unsqueeze(1)

class LuongAttnDecoderRNN(nn.Module):
    def __init__(self, attn_model, embedding, hidden_size, output_size, n_layers=1, dropout=0.1):
        super(LuongAttnDecoderRNN, self).__init__()

        # Keep for reference
        self.attn_model = attn_model
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.n_layers = n_layers
        self.dropout = dropout

        # Define layers
        self.embedding = embedding
        self.embedding_dropout = nn.Dropout(dropout)
        self.gru = nn.GRU(hidden_size, hidden_size, n_layers, dropout=(0 if n_layers == 1 else dropout))
        self.concat = nn.Linear(hidden_size * 2, hidden_size)
        self.out = nn.Linear(hidden_size, output_size)

        self.attn = Attn(attn_model, hidden_size)

    def forward(self, input_step, last_hidden, encoder_outputs):
        # Note: we run this one step (word) at a time
        # Get embedding of current input word
        embedded = self.embedding(input_step)
        embedded = self.embedding_dropout(embedded)
        # Forward through unidirectional GRU
        rnn_output, hidden = self.gru(embedded, last_hidden)
        # Calculate attention weights from the current GRU output
        attn_weights = self.attn(rnn_output, encoder_outputs)
        # Multiply attention weights to encoder outputs to get new "weighted sum" context vector
        context = attn_weights.bmm(encoder_outputs.transpose(0, 1))
        # Concatenate weighted context vector and GRU output using Luong eq. 5
        rnn_output = rnn_output.squeeze(0)
        context = context.squeeze(1)
        concat_input = torch.cat((rnn_output, context), 1)
        concat_output = torch.tanh(self.concat(concat_input))
        # Predict next word using Luong eq. 6
        output = self.out(concat_output)
        output = F.softmax(output, dim=1)
        # Return output and final hidden state
        return output, hidden

class GreedySearchDecoder(nn.Module):
    def __init__(self, encoder, decoder):
        super(GreedySearchDecoder, self).__init__()
        self.encoder = encoder
        self.decoder = decoder

        USE_CUDA = torch.cuda.is_available()
        self.device = torch.device("cuda" if USE_CUDA else "cpu")


    def forward(self, input_seq, input_length, max_length):
        # Forward input through encoder model
        encoder_outputs, encoder_hidden = self.encoder(input_seq, input_length)
        # Prepare encoder's final hidden layer to be first hidden input to the decoder
        decoder_hidden = encoder_hidden[:self.decoder.n_layers]
        # Initialize decoder input with SOS_token
        decoder_input = torch.ones(1, 1, device=self.device, dtype=torch.long) * SOS_token
        # Initialize tensors to append decoded words to
        all_tokens = torch.zeros([0], device=self.device, dtype=torch.long)
        all_scores = torch.zeros([0], device=self.device)
        # Iteratively decode one word token at a time
        for _ in range(max_length):
            # Forward pass through decoder
            decoder_output, decoder_hidden = self.decoder(decoder_input,
                                                          decoder_hidden, encoder_outputs)
            # Obtain most likely word token and its softmax score
            decoder_scores, decoder_input = torch.max(decoder_output, dim=1)
            # Record token and score
            all_tokens = torch.cat((all_tokens, decoder_input), dim=0)
            all_scores = torch.cat((all_scores, decoder_scores), dim=0)
            # Prepare current token to be next decoder input (add a dimension)
            decoder_input = torch.unsqueeze(decoder_input, 0)
        # Return collections of word tokens and scores
        return all_tokens, all_scores

class Chatbot:

    def __init__(self, model_name, txt_file, checkpoint_iter):
        # Hyperparameters
        self.formatted_movie_lines_file = txt_file
        self.max_length = 10
        self.trimming_threshold = 3
        self.model_name = model_name
        self.attn_model = 'dot' # dot, general or concat
        self.hidden_size = 500
        self.encoder_n_layers = 2
        self.decoder_n_layers = 2
        self.dropout = 0.1
        self.batch_size = 64
        self.do_training = False
        self.checkpoint_iter = checkpoint_iter
        self.clip = 50.0
        self.teacher_forcing_ratio = 1.0
        self.learning_rate = 0.0001
        self.decoder_learning_ratio = 5.0
        self.n_iteration = 4000
        self.print_every = 1
        self.save_every = 500

        USE_CUDA = torch.cuda.is_available()
        self.device = torch.device("cuda" if USE_CUDA else "cpu")


        # Define path to new file
        datafile = os.path.join("data", self.formatted_movie_lines_file)
        save_dir = os.path.join("data", "save")

        model_name = self.model_name
        attn_model = self.attn_model
        hidden_size = self.hidden_size
        encoder_n_layers = self.encoder_n_layers
        decoder_n_layers = self.decoder_n_layers
        dropout = self.dropout
        batch_size = self.batch_size

        # Set checkpoint to load from; set to None if starting from scratch
        loadFilename = None
        checkpoint = None
        checkpoint_iter = self.checkpoint_iter

        self.voc, pairs = self.loadPrepareData(self.model_name, datafile, save_dir)

        if not self.do_training:
            loadFilename = os.path.join(save_dir, model_name,
                                        '{}-{}_{}'.format(encoder_n_layers, decoder_n_layers, hidden_size),
                                        '{}_checkpoint.tar'.format(checkpoint_iter))
            # If loading on same machine the model was trained on
            checkpoint = torch.load(loadFilename)
            # If loading a model trained on GPU to CPU
            #checkpoint = torch.load(loadFilename, map_location=torch.device('cpu'))
            encoder_sd = checkpoint['en']
            decoder_sd = checkpoint['de']
            encoder_optimizer_sd = checkpoint['en_opt']
            decoder_optimizer_sd = checkpoint['de_opt']
            embedding_sd = checkpoint['embedding']
            self.voc.__dict__ = checkpoint['voc_dict']

        print('Building encoder and decoder ...')
        # Initialize word embeddings
        embedding = nn.Embedding(self.voc.num_words, hidden_size)

        if loadFilename:
            embedding.load_state_dict(embedding_sd)
        # Initialize encoder & decoder models
        encoder = EncoderRNN(hidden_size, embedding, encoder_n_layers, dropout)
        decoder = LuongAttnDecoderRNN(attn_model, embedding, hidden_size,
                                    self.voc.num_words, decoder_n_layers, dropout)

        if loadFilename:
            encoder.load_state_dict(encoder_sd)
            decoder.load_state_dict(decoder_sd)
        # Use appropriate device
        self.encoder = encoder.to(self.device)
        self.decoder = decoder.to(self.device)
        print('Models built and ready to go!')

        self.max_length = self.max_length

        if self.do_training:
            # Configure training/optimization
            clip = self.clip
            teacher_forcing_ratio = self.teacher_forcing_ratio
            learning_rate = self.learning_rate
            decoder_learning_ratio = self.decoder_learning_ratio
            n_iteration = self.n_iteration
            print_every = self.print_every
            save_every = self.save_every

            # Ensure dropout layers are in train mode
            encoder.train()
            decoder.train()

            # Initialize optimizers
            print('Building optimizers ...')
            encoder_optimizer = optim.Adam(encoder.parameters(), lr=learning_rate)
            decoder_optimizer = optim.Adam(decoder.parameters(), lr=learning_rate * decoder_learning_ratio)
            if loadFilename:
                encoder_optimizer.load_state_dict(encoder_optimizer_sd)
                decoder_optimizer.load_state_dict(decoder_optimizer_sd)

            # Run training iterations
            print("Starting Training!")
            self.trainIters(model_name, self.voc, pairs, encoder, decoder, encoder_optimizer, decoder_optimizer,
                    embedding, encoder_n_layers, decoder_n_layers, save_dir, n_iteration,
                    batch_size, print_every, save_every, clip, loadFilename,
                    teacher_forcing_ratio, hidden_size, self.max_length, checkpoint)

        # Set dropout layers to eval mode
        self.encoder.eval()
        self.decoder.eval()

        # Initialize search module
        self.searcher = GreedySearchDecoder(encoder, decoder)

        # Begin chatting (uncomment and run the following line to begin)
        # self.evaluateInput(encoder, decoder, searcher, voc, max_length)

        print("\n*** CHATBOT MODULE READY [2/3] ***\n")

    def printLines(self, file, n=10):
        with open(file, 'rb') as datafile:
            lines = datafile.readlines()
        for line in lines[:n]:
            print(line)

    # Splits each line of the file into a dictionary of fields
    def loadLines(self, fileName, fields):
        lines = {}
        with open(fileName, 'r', encoding='iso-8859-1') as f:
            for line in f:
                values = line.split(" +++$+++ ")
                # Extract fields
                lineObj = {}
                for i, field in enumerate(fields):
                    lineObj[field] = values[i]
                lines[lineObj['lineID']] = lineObj
        return lines

    # Groups fields of lines from `loadLines` into conversations based on *movie_conversations.txt*
    def loadConversations(self, fileName, lines, fields):
        conversations = []
        with open(fileName, 'r', encoding='iso-8859-1') as f:
            for line in f:
                values = line.split(" +++$+++ ")
                # Extract fields
                convObj = {}
                for i, field in enumerate(fields):
                    convObj[field] = values[i]
                # Convert string to list (convObj["utteranceIDs"] == "['L598485', 'L598486', ...]")
                lineIds = eval(convObj["utteranceIDs"])
                # Reassemble lines
                convObj["lines"] = []
                for lineId in lineIds:
                    convObj["lines"].append(lines[lineId])
                conversations.append(convObj)
        return conversations


    # Extracts pairs of sentences from conversations
    def extractSentencePairs(self, conversations):
        qa_pairs = []
        for conversation in conversations:
            # Iterate over all the lines of the conversation
            for i in range(len(conversation["lines"]) - 1):  # We ignore the last line (no answer for it)
                inputLine = conversation["lines"][i]["text"].strip()
                targetLine = conversation["lines"][i+1]["text"].strip()
                # Filter wrong samples (if one of the lists is empty)
                if inputLine and targetLine:
                    qa_pairs.append([inputLine, targetLine])
        return qa_pairs

    # Turn a Unicode string to plain ASCII, thanks to
    # https://stackoverflow.com/a/518232/2809427
    def unicodeToAscii(self, s):
        return ''.join(
            c for c in unicodedata.normalize('NFD', s)
            if unicodedata.category(c) != 'Mn'
        )

    # Lowercase, trim, and remove non-letter characters
    def normalizeString(self, s):
        s = self.unicodeToAscii(s.lower().strip())
        s = re.sub(r"([.!?])", r" \1", s)
        s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
        s = re.sub(r"\s+", r" ", s).strip()
        return s

    # Read query/response pairs and return a voc object
    def readVocs(self, datafile, model_name):
        pairs = []
        with open(datafile, 'r', encoding='utf-8') as f:
            while True:
                line = f.readline()
                if line:
                    pair = [self.normalizeString(s) for s in line.split('\t')]
                    if self.filterPair(pair):
                        pairs.append(pair)
                else:
                    break
        '''
        # Read the file and split into lines
        lines = open(datafile, encoding='utf-8').\
            read().strip().split('\n')
        # Split every line into pairs and normalize
        pairs = [[self.normalizeString(s) for s in l.split('\t')] for l in lines]
        '''
        voc = Voc(model_name)
        return voc, pairs

    # Returns True iff both sentences in a pair 'p' are under the MAX_LENGTH threshold
    def filterPair(self, p):
        # Input sequences need to preserve the last word for EOS token
        try:
            return len(p[0].split(' ')) < self.max_length and len(p[1].split(' ')) < self.max_length
        except IndexError:
            return False

    # Filter pairs using filterPair condition
    def filterPairs(self, pairs):
        return [pair for pair in pairs if self.filterPair(pair)]

    # Using the functions defined above, return a populated voc object and pairs list
    def loadPrepareData(self, model_name, datafile, save_dir):
        print("Start preparing training data ...")
        voc, pairs = self.readVocs(datafile, model_name)
        print("Read {!s} sentence pairs".format(len(pairs)))
        #pairs = self.filterPairs(pairs)
        print("Trimmed to {!s} sentence pairs".format(len(pairs)))
        print("Counting words...")
        for pair in pairs:
            voc.addSentence(pair[0])
            voc.addSentence(pair[1])
        print("Counted words:", voc.num_words)
        return voc, pairs

    def trimRareWords(self, voc, pairs, threshold_minimum):
        # Trim words used under the MIN_COUNT from the voc
        voc.trim(threshold_minimum)
        # Filter out pairs with trimmed words
        keep_pairs = []
        for pair in pairs:
            input_sentence = pair[0]
            output_sentence = pair[1]
            keep_input = True
            keep_output = True
            # Check input sentence
            for word in input_sentence.split(' '):
                if word not in voc.word2index:
                    keep_input = False
                    break
            # Check output sentence
            for word in output_sentence.split(' '):
                if word not in voc.word2index:
                    keep_output = False
                    break

            # Only keep pairs that do not contain trimmed word(s) in their input or output sentence
            if keep_input and keep_output:
                keep_pairs.append(pair)

        print("Trimmed from {} pairs to {}, {:.4f} of total".format(len(pairs), len(keep_pairs), len(keep_pairs) / len(pairs)))
        return keep_pairs

    def indexesFromSentence(self, voc, sentence):
        return [voc.word2index[word] for word in sentence.split(' ')
                if word in voc.word2index] + [EOS_token]


    def zeroPadding(self, l, fillvalue=PAD_token):
        return list(itertools.zip_longest(*l, fillvalue=fillvalue))

    def binaryMatrix(self, l, value=PAD_token):
        m = []
        for i, seq in enumerate(l):
            m.append([])
            for token in seq:
                if token == PAD_token:
                    m[i].append(0)
                else:
                    m[i].append(1)
        return m

    # Returns padded input sequence tensor and lengths
    def inputVar(self, l, voc):
        indexes_batch = [self.indexesFromSentence(voc, sentence) for sentence in l]
        lengths = torch.tensor([len(indexes) for indexes in indexes_batch])
        padList = self.zeroPadding(indexes_batch)
        padVar = torch.LongTensor(padList)
        return padVar, lengths

    # Returns padded target sequence tensor, padding mask, and max target length
    def outputVar(self, l, voc):
        indexes_batch = [self.indexesFromSentence(voc, sentence) for sentence in l]
        max_target_len = max([len(indexes) for indexes in indexes_batch])
        padList = self.zeroPadding(indexes_batch)
        mask = self.binaryMatrix(padList)
        mask = torch.ByteTensor(mask)
        padVar = torch.LongTensor(padList)
        return padVar, mask, max_target_len

    # Returns all items for a given batch of pairs
    def batch2TrainData(self, voc, pair_batch):
        pair_batch.sort(key=lambda x: len(x[0].split(" ")), reverse=True)
        input_batch, output_batch = [], []
        for pair in pair_batch:
            input_batch.append(pair[0])
            output_batch.append(pair[1])
        inp, lengths = self.inputVar(input_batch, voc)
        output, mask, max_target_len = self.outputVar(output_batch, voc)
        return inp, lengths, output, mask, max_target_len

    def maskNLLLoss(self, inp, target, mask):
        nTotal = mask.sum()
        crossEntropy = -torch.log(torch.gather(inp, 1, target.view(-1, 1)).squeeze(1))
        loss = crossEntropy.masked_select(mask).mean()
        loss = loss.to(self.device)
        return loss, nTotal.item()

    def train(self, input_variable, lengths, target_variable, mask, max_target_len,
            encoder, decoder, embedding, encoder_optimizer, decoder_optimizer,
            batch_size, clip, teacher_forcing_ratio, max_length):

        # Zero gradients
        encoder_optimizer.zero_grad()
        decoder_optimizer.zero_grad()

        # Set device options
        input_variable = input_variable.to(self.device)
        lengths = lengths.to(self.device)
        target_variable = target_variable.to(self.device)
        mask = mask.to(self.device)

        # Initialize variables
        loss = 0
        print_losses = []
        n_totals = 0

        # Forward pass through encoder
        encoder_outputs, encoder_hidden = encoder(input_variable, lengths)

        # Create initial decoder input (start with SOS tokens for each sentence)
        decoder_input = torch.LongTensor([[SOS_token for _ in range(batch_size)]])
        decoder_input = decoder_input.to(self.device)

        # Set initial decoder hidden state to the encoder's final hidden state
        decoder_hidden = encoder_hidden[:decoder.n_layers]

        # Determine if we are using teacher forcing this iteration
        use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False

        # Forward batch of sequences through decoder one time step at a time
        if use_teacher_forcing:
            for t in range(max_target_len):
                decoder_output, decoder_hidden = decoder(
                    decoder_input, decoder_hidden, encoder_outputs
                )
                # Teacher forcing: next input is current target
                decoder_input = target_variable[t].view(1, -1)
                # Calculate and accumulate loss
                mask_loss, nTotal = self.maskNLLLoss(decoder_output, target_variable[t], mask[t])
                loss += mask_loss
                print_losses.append(mask_loss.item() * nTotal)
                n_totals += nTotal
        else:
            for t in range(max_target_len):
                decoder_output, decoder_hidden = decoder(
                    decoder_input, decoder_hidden, encoder_outputs
                )
                # No teacher forcing: next input is decoder's own current output
                _, topi = decoder_output.topk(1)
                decoder_input = torch.LongTensor([[topi[i][0] for i in range(batch_size)]])
                decoder_input = decoder_input.to(self.device)
                # Calculate and accumulate loss
                mask_loss, nTotal = self.maskNLLLoss(decoder_output, target_variable[t], mask[t])
                loss += mask_loss
                print_losses.append(mask_loss.item() * nTotal)
                n_totals += nTotal

        # Perform backpropatation
        loss.backward()

        # Clip gradients: gradients are modified in place
        _ = nn.utils.clip_grad_norm_(encoder.parameters(), clip)
        _ = nn.utils.clip_grad_norm_(decoder.parameters(), clip)

        # Adjust model weights
        encoder_optimizer.step()
        decoder_optimizer.step()

        return sum(print_losses) / n_totals

    def trainIters(self, model_name, voc, pairs, encoder, decoder, encoder_optimizer, decoder_optimizer,
                embedding, encoder_n_layers, decoder_n_layers, save_dir, n_iteration,
                batch_size, print_every, save_every, clip, loadFilename,
                teacher_forcing_ratio, hidden_size, max_length, checkpoint):

        # Load batches for each iteration
        training_batches = [self.batch2TrainData(voc, [random.choice(pairs) for _ in range(batch_size)])
                            for _ in range(n_iteration)]

        # Initializations
        print('Initializing ...')
        start_iteration = 1
        print_loss = 0
        if loadFilename:
            start_iteration = checkpoint['iteration'] + 1

        # Training loop
        print("Training...")
        for iteration in range(start_iteration, n_iteration + 1):
            training_batch = training_batches[iteration - 1]
            # Extract fields from batch
            input_variable, lengths, target_variable, mask, max_target_len = training_batch

            # Run a training iteration with batch
            loss = self.train(input_variable, lengths, target_variable, mask, max_target_len,
                        encoder, decoder, embedding, encoder_optimizer, decoder_optimizer,
                        batch_size, clip, teacher_forcing_ratio, max_length)
            print_loss += loss

            # Print progress
            if iteration % print_every == 0:
                print_loss_avg = print_loss / print_every
                print("Iteration: {}; Percent complete: {:.1f}%; Average loss: {:.4f}"
                    .format(iteration, iteration / n_iteration * 100, print_loss_avg))
                print_loss = 0

            # Save checkpoint
            if (iteration % save_every == 0):
                directory = os.path.join(save_dir, model_name,
                                        '{}-{}_{}'.format(encoder_n_layers, decoder_n_layers, hidden_size))
                if not os.path.exists(directory):
                    os.makedirs(directory)
                torch.save({
                    'iteration': iteration,
                    'en': encoder.state_dict(),
                    'de': decoder.state_dict(),
                    'en_opt': encoder_optimizer.state_dict(),
                    'de_opt': decoder_optimizer.state_dict(),
                    'loss': loss,
                    'voc_dict': voc.__dict__,
                    'embedding': embedding.state_dict()
                }, os.path.join(directory, '{}_{}.tar'.format(iteration, 'checkpoint')))

    def evaluate(self, encoder, decoder, searcher, voc, sentence, max_length):
        ### Format input sentence as a batch
        # words -> indexes
        indexes_batch = [self.indexesFromSentence(voc, sentence)]
        # Create lengths tensor
        lengths = torch.tensor([len(indexes) for indexes in indexes_batch])
        # Transpose dimensions of batch to match models' expectations
        input_batch = torch.LongTensor(indexes_batch).transpose(0, 1)
        # Use appropriate device
        input_batch = input_batch.to(self.device)
        lengths = lengths.to(self.device)
        # Decode sentence with searcher
        tokens, scores = searcher(input_batch, lengths, max_length)
        # indexes -> words
        decoded_words = [voc.index2word[token.item()] for token in tokens]
        return decoded_words


    def evaluateInput(self, encoder, decoder, searcher, voc, max_length):
        input_sentence = ''
        while True:
            try:
                # Get input sentence
                input_sentence = input('> ')
                # Check if it is quit case
                if input_sentence == 'q' or input_sentence == 'quit': break
                # Normalize sentence
                input_sentence = self.normalizeString(input_sentence)
                # Evaluate sentence
                output_words = self.evaluate(encoder, decoder, searcher, voc, input_sentence, max_length)
                # Format and print response sentence
                output_words[:] = [x for x in output_words if not (x == 'EOS' or x == 'PAD')]
                print('Bot:', ' '.join(output_words))

            except KeyError:
                print("Error: Encountered unknown word.")

    def get_answer(self, sentence):
        # Normalize sentence
        normalize_sentence = self.normalizeString(sentence)
        # Evaluate sentence
        output_words = self.evaluate(self.encoder, self.decoder, self.searcher,
                                self.voc, normalize_sentence, self.max_length)
        # Format and print response sentence
        output_words[:] = [x for x in output_words if not (x == 'EOS' or x == 'PAD')]

        # Delete multiple punctuation mark endings
        output_words.reverse()
        head = output_words[0]
        output = ''

        for i, word in enumerate(output_words):
            if word not in head:
                output_words = output_words[i-1:]
                output_words.reverse()
                break;

        '''
        # Fix verbs contractions
        fixed_output = output_words
        prev = ''
        for i, word in enumerate(output_words):
            if prev in 'i' and word in 'm':
                fixed_output[i] = 'am'
            elif prev in 'you' and word in 're':
                fixed_output[i] = 'are'
            elif prev in 'i' and word in 'll':
                fixed_output[i] = 'will'
            elif prev in 'don' and word in 't':
                fixed_output[i] = '\'t'
            elif prev in 'it' and word in 's':
                fixed_output[i] = 'is'

        return ' '.join(fixed_output)
        '''

        return ' '.join(output_words)
