# conda install spacy
# python -m spacy download en_core_web_lg

import spacy
import os
import json
import string
import re
import neuralcoref
import itertools
import requests
import datetime
import nltk

from os.path import join, dirname
from dotenv import load_dotenv
from bert import Bert
from chatbot import Chatbot
from controller import Controller
from flask import Flask, request, abort, jsonify, render_template
from elasticsearch import Elasticsearch
from elasticsearch_dsl import MultiSearch, Search
from spacy.lang.en.stop_words import STOP_WORDS
from datetime import datetime
from spacy.tokenizer import Tokenizer
from spacy.util import compile_infix_regex

app = Flask(__name__)

# Load all environment variables
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

bert = Bert()
chatbot = Chatbot(os.getenv('ChatbotModelName'),
                  os.getenv('ChatbotDataFile'),
                  int(os.getenv('ChatbotNbIterations')))
controller = Controller()

nlp = spacy.load('en_core_web_lg')
neuralcoref.add_to_pipe(nlp)
nltk.download('punkt')

ES_HOST = os.getenv('Host')
ES_PORT = os.getenv('Port')
ES_INDEX = os.getenv('Index')

sessions = []


@app.route('/', methods=['POST'])
def answer():
    req_data = request.get_json()

    if not contains_query_text(req_data):
        abort(400) # Bad Request

    sessionID = req_data['session']
    base_query = req_data['queryResult']['queryText']
    query = base_query[0].upper() + base_query[1:]

    answer, query_coref_resolved, label, title, article, answer_qa, answer_chatbot, title_qa, article = get_answer(query, sessionID)

    if not answer:
        answer = 'I don\'t know'

    for session in sessions:
        if session['id'] == sessionID:
            session['chat'].append({
                'query': query,
                'query_coref_resolved': query_coref_resolved,
                'answer': answer,
                'label': label,
                'titleAnswerPage': title
            })
            break
    else:
        sessions.append({
            'id': sessionID,
            'chat': [{
                'query': query,
                'query_coref_resolved': query_coref_resolved,
                'answer': answer,
                'label': label,
                'titleAnswerPage': title
            }]
        })

    '''
    if base_query == 'debug':
        print('\n***DEBUG***')
        for session in sessions:
            print('id: ' + session['id'])
            for chat in session['chat']:
                print('  query: ' + chat['query'])
                print('  query_coref_resolved: ' + chat['query_coref_resolved'])
                print('  answer: ' + chat['answer'])
                print('  label: ' + chat['label'])
                print('  titleAnswerPage: ' + chat['titleAnswerPage'])
                print('  ---')
    '''
    
    today = datetime.now()
    with open('dump/%s.json' % today.strftime("%A-%d-%b-%Y"), 'a') as f:
        f.write('%s\n' % json.dumps({
                'query': query,
                'answer': answer,
                'label_controller': label,
                'query_coref_resolved': query_coref_resolved,
                'answer_chatbot': answer_chatbot,
                'answer_qa': answer_qa,
                'title_qa': title_qa,
                'article_qa': article,
                'datetime': today.strftime("%A %d/%m/%Y, %H:%M:%S"),
                'timestamp': datetime.timestamp(today)
            }))
    
    with open('dump/%s.txt' % today.strftime("%A-%d-%b-%Y"), 'a') as f:
        f.write('Query: %s\nAnswer: %s\n\n' % (query, answer))

    return jsonify({ 'fulfillmentText': answer })


@app.route('/chat')
def index():
    return render_template("index.html")


@app.route("/get")
def get_bot_response():
    question = request.args.get('msg')
    
    #question = ' '.join(userText)

    url = 'http://127.0.0.1:5000/'
    headers = { 'Content-Type': 'application/json' }
    payload = { 'queryResult': { 'queryText': question },
                'session': '123456' }
    r = requests.post(url, data=json.dumps(payload), headers=headers)
    answer = r.json()['fulfillmentText']
    if not answer:
        answer = 'No answer'

    return answer


def contains_query_text(json):
    return ('queryResult' in json and
            'queryText' in json['queryResult'])


def contains_pronoun(query):
    return re.search(r"\b(he|him|his|himself|" \
                + r"she|her|hers|herself| " \
                + r"it|its|itself|" \
                + r"they|them|their|theirs|themselves)\b", query, re.IGNORECASE)


def fix_contractions(sentence):
    sentence_fixed = re.sub(r"\b(i|you|we|he|she|they|it|" \
            + r"somebody|someone|something|" \
            + r"|who|what|when|where|why|how|which|" \
            + r"this|these|that|those|there|here|" \
            + r"ain|isn|aren|wasn|weren|won|" \
            + r"can|couldn|shouldn|wouldn|mighn|musn|" \
            + r"don|doesn|didn|haven|hasn|hadn|" \
            + r"let)\b" \
            + r"\s*'?" \
            + r"\b(ll|d|ve|m|s|re|t)\b", r"\g<1>'\g<2>", sentence, re.IGNORECASE)
    return sentence_fixed


def clean_answer(answer):
    # Replace all whitespace characters by one space
    answer = re.sub(r"\s+", " ", answer)
    # Remove all characters other than . , - ' or letters (also with accents) or numbers or space
    # \p{L}\p{M}*+ matches a letter including any diacritics
    # for instance: 'à' encoded as U+0061 U+0300 as well as U+00E0
    #answer = re.sub(r"([^.,\-'0-9 \p{L}\p{M}*+]", "", answer)
    # Return answer without trailing space
    return answer.strip()


def resolve_pronouns(query, sessionID):
    if contains_pronoun(query):
        for session in sessions:
            if session['id'] == sessionID:
                reversed_chats = list(reversed(session['chat']))
                last_qa_chats = list(itertools.takewhile(lambda c: c['label'] == 'QA', reversed_chats))
                last_n_chats = last_qa_chats[:int(os.getenv('TemporalDistanceContext'))]
                unreversed_chats = list(reversed(last_n_chats))
                iter_chats = iter(unreversed_chats)

                conversation = ''
                try:
                    first_chat = next(iter_chats)

                    conversation += first_chat['query_coref_resolved'] + '. ' + first_chat['answer']

                    for chat in iter_chats:
                        conversation += '. ' + chat['query_coref_resolved'] + '. ' + chat['answer']
                except StopIteration:
                    pass
                finally:
                    del iter_chats

                if conversation:
                    conversation += '. ' + query + '.'
                else:
                    conversation += query + '.'

                query_result = query

                conv_nlp = nlp(conversation)
                conv_coref_resolved = conv_nlp._.coref_resolved
                conv_coref_resolved_nlp = nlp(conv_coref_resolved)
                conv_sentences = list(conv_coref_resolved_nlp.sents)
                query_coref_resolved = str(conv_sentences[-1])
                query_result = query_coref_resolved.rstrip('.')

                return (query_result, conversation)

    return (query, '')

def get_answer(query, sessionID):
    query_coref_resolved, conversation = resolve_pronouns(query, sessionID)

    answer_qa, title_qa, article_qa = get_answer_from_question(query_coref_resolved)
    answer_chatbot = chatbot.get_answer(query)

    answer = ''
    title = ''
    article = ''
    label = ''

    if controller.define_class(query) == 0:
        answer = answer_qa
        title = title_qa
        article = article_qa
        label = 'QA'

    elif controller.define_class(query) == 1:
        answer = answer_chatbot
        label = 'Chat'

    # Clean answer
    answer = clean_answer(answer)
    answer = fix_contractions(answer)
    answer = answer.capitalize()

    print()
    print('Query: ' + query)
    print('Query pronoun resolved: ' + query_coref_resolved)
    print('Answer chosen (and cleaned): ' + answer)
    print('Label: ' + label)
    if title: print('Wikipedia\'s title: ' + title)
    print('Conversation: ' + conversation)
    print('  ---')
    print('  Answer qa: ' + answer_qa)
    print('  Title qa: ' + title_qa)
    print('  ---')
    print('  Answer chatbot: ' + answer_chatbot)
    print()

    return (answer, query_coref_resolved, label, title, article, answer_qa, answer_chatbot, title_qa, article)

def query_tokenizer(nlp):
    inf = list(nlp.Defaults.infixes)               # Default infixes
    inf.remove(r"(?<=[0-9])[+\-\*^](?=[0-9-])")    # Remove the generic op between numbers or between a number and a -
    inf = tuple(inf)                               # Convert inf to tuple
    infixes = inf + tuple([r"(?<=[0-9])[+*^](?=[0-9-])", r"(?<=[0-9])-(?=-)"])  # Add the removed rule after subtracting (?<=[0-9])-(?=[0-9]) pattern
    infixes = [x for x in infixes if '-|–|—|--|---|——|~' not in x] # Remove - between letters rule
    infix_re = compile_infix_regex(infixes)

    return Tokenizer(nlp.vocab, prefix_search=nlp.tokenizer.prefix_search,
                                suffix_search=nlp.tokenizer.suffix_search,
                                infix_finditer=infix_re.finditer,
                                token_match=nlp.tokenizer.token_match,
                                rules=nlp.Defaults.tokenizer_exceptions)

def get_query_from_question(question):
    if os.getenv('StripStopWordsForES'):
        question = strip_stop_words(question)

    if os.getenv('StripFiveWForES'):
        question = strip_five_w(question)

    if os.getenv('StripPunctuationForES'):
        question = strip_punctuation(question)

    # remove double space
    question = re.sub(r"\s+", " ", question)

    # nlp tokenizer ignore "-"
    nlp.tokenizer = query_tokenizer(nlp)
    question_nlp = nlp(question)
    query = ""
    maxQueryScore = 0

    #query build
    for word in question_nlp:
        if word.text != "":
            # if word is a proper noun, a named entity, superlative or comparative, add it to the query
            if word.pos_ == 'PROPN' or word.pos_ == 'ADJ' or word.pos_ == 'ADV' or word.ent_iob_ == 'B' or word.ent_iob_ == 'I':
                query += word.text + "^" + str(os.getenv('ESMajorWordMultiplication')) + " "
                maxQueryScore += int(os.getenv('ESMajorWordMultiplication'))
            # if word is a noun or firstname, add it to the query
            elif word.pos_ == 'NOUN' or word.pos_ == 'PRON':
                query += word.text + "^" + str(os.getenv('ESMediumWordMultiplication')) + " "
                maxQueryScore += int(os.getenv('ESMediumWordMultiplication'))
            # add to query other words
            else:
                query += word.text + "^" + str(os.getenv('ESLowWordMultiplication')) + " "  
                maxQueryScore += int(os.getenv('ESLowWordMultiplication'))

    return query, question, maxQueryScore

def get_documents_from_elasticsearch(question):
    question = question.lower()
    query, question, maxQueryScore = get_query_from_question(question)

    es = Elasticsearch([ES_HOST], port=ES_PORT)

    s = Search(using=es, index=ES_INDEX).query('query_string', query=query,
        fields=['title^'+str(os.getenv('ESBoostTitle')), 'opening_text^'+str(os.getenv('ESBoostOpeningText')), 'text^'+str(os.getenv('ESBoostText'))])[0:int(os.getenv('ESNbDocument'))]
        
    response = s.execute()
    passages = []

    for hit in response:
        scoreSentences = []
        
        sentences = nltk.sent_tokenize(hit.text)

        # get the score of all sentences of the document
        for sentence in sentences:
            scoreSentence = 0
            # temporary list of words of the sentence for didn't count two times the same word
            temp_question = question.split(' ') 

            for word in sentence.split(' '):    
                if word.lower() in temp_question: 
                    scoreSentence += int(query.split(word.lower()+'^')[1].split()[0])
                    #remove word from temp_question
                    temp_question.remove(word.lower())

            scoreSentences.append(scoreSentence)

        # score all passages of the document
        for i in range(len(scoreSentences)):
            if i + int(os.getenv('PassageLength')) < len(scoreSentences):
                score = 0
                for j in range(i, i + int(os.getenv('PassageLength'))):
                    score += scoreSentences[j]
                if score / (maxQueryScore * int(os.getenv('PassageLength'))) >= float(os.getenv('PassageScoreMin')):
                    passage = ""
                    for j in range(i, i + int(os.getenv('PassageLength'))):
                        passage += sentences[j] + " "
                    passages.append((passage,score,hit.title))
    
    #sort passages by score
    passages = sorted(passages, key=lambda x: x[1], reverse=True)
    #return only the first MaxESPassage passages
    passages = passages[:int(os.getenv('ESMaxPassage'))]
    return passages


def get_answer_from_question(question):
    '''
    Full query approach
    '''
    
    responses = []
    try:
        passages = get_documents_from_elasticsearch(question)
        for passage in passages:
            responses.append((bert.get_answer(question, passage[0]), passage))
    except:
        return ('','','')
    
    # remove response that are egual to ""
    responses = [r for r in responses if r[0] != ""]
    if len(responses) == 0:
        return ('','','')

    scores = []
    i = 0
    while i < len(responses):        
        response_i = " ".join([token.lemma_ for token in nlp(responses[i][0])])

        score = 0
        y = 0
        while y < len(responses):
            # compare responses with each other
            if i != y:                     
                response_y = " ".join([token.lemma_ for token in nlp(responses[y][0])])    
                if response_i == response_y: 
                    score += 1
                    responses.remove(responses[y])
                    y -= 1     
            y += 1
        scores.append(score)
        i += 1

    #return response with best score
    currentBest = 0
    for i in range(len(responses)):
        if scores[i] > scores[currentBest]:
            currentBest = i

    return (responses[currentBest][0],responses[currentBest][1][2],responses[currentBest][1][0])


def strip_stop_words(sentence):
    s = sentence.split()
    s_no_stop_words = ' '.join([w for w in s
                                if w.lower() not in STOP_WORDS])
    return s_no_stop_words


def strip_five_w(sentence):
    s = sentence.split()
    s_no_five_w = ' '.join([w for w in s
                            if w.lower() not in ['who', 'what', 'when', 'where', 'why']])
    return s_no_five_w


def strip_punctuation(sentence):
    translator = str.maketrans('', '', string.punctuation)
    s_no_punctuation = sentence.translate(translator)
    return s_no_punctuation
