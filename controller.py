# https://pytorch.org/tutorials/beginner/nlp/deep_learning_tutorial.html

import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

SQUAD_QUESTIONS_FILE = os.getenv('SquadQuestionsFile')
SUBTITLES_FILE = os.getenv('SubtitlesFile')
CONTROLLER_MODEL = os.getenv('ControllerModel')

class BoWClassifier(nn.Module):  # inheriting from nn.Module!

    def __init__(self, num_labels, vocab_size):
        # calls the init function of nn.Module.  Dont get confused by syntax,
        # just always do it in an nn.Module
        super(BoWClassifier, self).__init__()

        # Define the parameters that you will need.  In this case, we need A and b,
        # the parameters of the affine mapping.
        # Torch defines nn.Linear(), which provides the affine map.
        # Make sure you understand why the input dimension is vocab_size
        # and the output is num_labels!
        self.linear = nn.Linear(vocab_size, num_labels)

        # NOTE! The non-linearity log softmax does not have parameters! So we don't need
        # to worry about that here

    def forward(self, bow_vec):
        # Pass the input through the linear layer,
        # then pass that through log_softmax.
        # Many non-linearities and other functions are in torch.nn.functional
        return F.log_softmax(self.linear(bow_vec), dim=1)

class Controller():

    def __init__(self):
        torch.manual_seed(1)

        self.data = []
        self.test_data = []

        train_i = 8311 # ~= 11873 * 0.7

        with open(SQUAD_QUESTIONS_FILE, encoding='UTF-8') as squad:
            qa_counter = 0
            for line in squad:
                if qa_counter < train_i:
                    self.data.append((line.split(), 'QA'))
                else:
                    self.test_data.append((line.split(), 'QA'))
                qa_counter += 1

        with open(SUBTITLES_FILE, encoding='UTF-8') as subtitles:
            sub_counter = 0
            for line in subtitles:
                if sub_counter < train_i:
                    self.data.append((line.split(), 'CHAT'))
                else:
                    self.test_data.append((line.split(), 'CHAT'))
                sub_counter += 1

        # word_to_ix maps each word in the vocab to a unique integer, which will be its
        # index into the Bag of words vector
        self.word_to_ix = {}
        for sent, _ in self.data + self.test_data:
            for word in sent:
                if word not in self.word_to_ix:
                    self.word_to_ix[word] = len(self.word_to_ix)

        VOCAB_SIZE = len(self.word_to_ix)
        NUM_LABELS = 2
        self.label_to_ix = {"QA": 0, "CHAT": 1}

        self.model = BoWClassifier(NUM_LABELS, VOCAB_SIZE)
        self.model.load_state_dict(torch.load(CONTROLLER_MODEL))
        self.model.eval()

        print('\n*** CONTROLLER READY [3/3] ***\n')


    def define_class(self, sentence):
        words = sentence.split()
        p_qa = 0
        p_chat = 0
        for word in words:
            if word not in self.word_to_ix:
                break
            p = next(self.model.parameters())[:, self.word_to_ix[word]]
            p_qa += p[0]
            p_chat += p[1]
        if len(words) != 0:
            p_qa = p_qa / len(words)
            p_chat = p_chat / len(words)

        if p_qa > p_chat:
            return self.label_to_ix['QA']
        else:
            return self.label_to_ix['CHAT']

    def run_test_data(self):
        total_correct = 0
        total_wrong = 0

        for t in self.test_data:
            label_found = self.define_class(' '.join(t[0]))
            gold_label = self.label_to_ix[t[1]]

            if label_found == gold_label:
                total_correct += 1
                print('correct\n')
            else:
                total_wrong += 1
                print('wrong\n')

        print('Total correct: ' + str(total_correct))
        print('Total wrong: ' + str(total_wrong))
