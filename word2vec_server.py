#!/usr/bin/env python
# coding: utf-8

import socket
import datetime
import threading
import sys
import gensim
import logging
import json
import configparser
import csv


class WebVectorsThread(threading.Thread):
    def __init__(self, connect, address):
        threading.Thread.__init__(self)
        self.connect = connect
        self.address = address

    def run(self):
        clientthread(self.connect, self.address)


def clientthread(connect, addres):
    # Sending message to connected client
    connect.send(bytes(b'word2vec model server'))

    # infinite loop so that function do not terminate and thread do not end.
    while True:
        # Receiving from client
        data = connect.recv(1024)
        if not data:
            break
        query = json.loads(data.decode('utf-8'))
        output = operations[query['operation']](query)
        now = datetime.datetime.now()
        print(now.strftime("%Y-%m-%d %H:%M"), '\t', addres[0] + ':' + str(addres[1]), '\t',
              data.decode('utf-8'), file=sys.stderr)
        reply = json.dumps(output, ensure_ascii=False)
        connect.sendall(reply.encode('utf-8'))
        break

    # came out of loop
    connect.close()


config = configparser.RawConfigParser()
config.read('/var/www/webvectors/webvectors.cfg')

root = config.get('Files and directories', 'root')
HOST = config.get('Sockets', 'host')  # Symbolic name meaning all available interfaces
PORT = config.getint('Sockets', 'port')  # Arbitrary non-privileged port
tags = config.getboolean('Tags', 'use_tags')

logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)

# Loading models

our_models = {}
with open(root + config.get('Files and directories', 'models'), 'r') as csvfile:
    reader = csv.DictReader(csvfile, delimiter='\t')
    for row in reader:
        our_models[row['identifier']] = {}
        our_models[row['identifier']]['path'] = row['path']
        our_models[row['identifier']]['default'] = row['default']
        our_models[row['identifier']]['tags'] = row['tags']
        our_models[row['identifier']]['algo'] = row['algo']
        our_models[row['identifier']]['corpus_size'] = int(row['size'])

models_dic = {}

for m in our_models:
    modelfile = our_models[m]['path']
    our_models[m]['vocabulary'] = True
    if our_models[m]['algo'] == 'fasttext':
        models_dic[m] = gensim.models.KeyedVectors.load(modelfile)
    else:
        if modelfile.endswith('.bin.gz') or modelfile.endswith('.bin'):
            models_dic[m] = gensim.models.KeyedVectors.load_word2vec_format(modelfile, binary=True)
            our_models[m]['vocabulary'] = False
        elif modelfile.endswith('.vec.gz') or modelfile.endswith('.txt.gz') or \
                modelfile.endswith('.vec') or modelfile.endswith('.txt'):
            models_dic[m] = gensim.models.KeyedVectors.load_word2vec_format(modelfile, binary=False)
            our_models[m]['vocabulary'] = False
        else:
            models_dic[m] = gensim.models.KeyedVectors.load(modelfile)
    models_dic[m].init_sims(replace=True)
    print("Model", m, "from file", modelfile, "loaded successfully.", file=sys.stderr)


# Get pairs of words to create graph

def get_edges(word, model, mostsim):
    edges = [{"source": word, "target": word, "value": 1}]
    neighbors_list = []

    for item in mostsim:
        edges.append({"source": word, "target": item[0], "value": item[1]})
        neighbors_list.append(item[0])

    pairs = [
        (neighbors_list[ab], neighbors_list[ba])
        for ab in range(len(neighbors_list))
        for ba in range(ab + 1, len(neighbors_list))
    ]
    for pair in pairs:
        edges.append(
            {"source": pair[0], "target": pair[1], "value": float(model.similarity(*pair))}
        )
    return edges


def find_variants(word, usermodel):
    # Find variants of query word in the model
    model = models_dic[usermodel]
    results = None
    candidates_set = set()
    candidates_set.add(word.upper())
    if tags and our_models[usermodel]['tags'] == 'True':
        candidates_set.add(word.split('_')[0] + '_X')
        candidates_set.add(word.split('_')[0].lower() + '_' + word.split('_')[1])
        candidates_set.add(word.split('_')[0].capitalize() + '_' + word.split('_')[1])
    else:
        candidates_set.add(word.lower())
        candidates_set.add(word.capitalize())
    for candidate in candidates_set:
        if candidate in model.wv.vocab:
            results = candidate
            break
    return results


def frequency(word, model):
    # Find word frequency tier
    corpus_size = our_models[model]['corpus_size']
    if word not in models_dic[model].wv.vocab:
        word = find_variants(word, model)
        if not word:
            return 0, 'low'
    if not our_models[model]['vocabulary']:
        return 0, 'mid'
    wordfreq = models_dic[model].wv.vocab[word].count
    relative = wordfreq / corpus_size
    tier = 'mid'
    if relative > 0.00001:
        tier = 'high'
    elif relative < 0.0000005:
        tier = 'low'
    return wordfreq, tier


# Vector functions

def find_synonyms(query):
    q = query['query']
    pos = query['pos']
    usermodel = query['model']
    nr_neighbors = query['nr_neighbors']
    results = {'frequencies': {}, 'neighbours_dist': []}
    qf = q
    model = models_dic[usermodel]
    if qf not in model.wv.vocab:
        qf = find_variants(qf, usermodel)
        if not qf:
            if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(q):
                results['inferred'] = True
                qf = q
            else:
                results[q + " is unknown to the model"] = True
                results['frequencies'][q] = frequency(q, usermodel)
                return results
    results['frequencies'][q] = frequency(qf, usermodel)
    results['neighbors'] = []
    if pos == 'ALL':
        for i in model.wv.most_similar(positive=qf, topn=nr_neighbors):
            results['neighbors'].append(i)
    else:
        counter = 0
        for i in model.wv.most_similar(positive=qf, topn=30):
            if counter == nr_neighbors:
                break
            if i[0].split('_')[-1] == pos:
                results['neighbors'].append(i)
                counter += 1
    if len(results) == 0:
        results['No results'] = True
        return results
    for res in results['neighbors']:
        freq, tier = frequency(res[0], usermodel)
        results['frequencies'][res[0]] = (freq, tier)
    raw_vector = model[qf]
    results['vector'] = raw_vector.tolist()
    results['edges'] = get_edges(q, model, results['neighbors'])
    return results


def find_similarity(query):
    q = query['query']
    usermodel = query['model']
    model = models_dic[usermodel]
    results = {'similarities': [], 'frequencies': {}}
    for pair in q:
        (q1, q2) = pair
        qf1 = q1
        qf2 = q2
        if q1 not in model.wv.vocab:
            qf1 = find_variants(qf1, usermodel)
            if not qf1:
                if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(q1):
                    results['inferred'] = True
                    qf1 = q1
                else:
                    results["Unknown to the model"] = q1
                    return results
        if q2 not in model.wv.vocab:
            qf2 = find_variants(qf2, usermodel)
            if not qf2:
                if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(q2):
                    results['inferred'] = True
                    qf2 = q2
                else:
                    results["Unknown to the model"] = q2
                    return results
        results['frequencies'][qf1] = frequency(qf1, usermodel)
        results['frequencies'][qf2] = frequency(qf2, usermodel)
        pair2 = (qf1, qf2)
        result = float(model.similarity(qf1, qf2))
        results['similarities'].append((pair2, result))
    return results


def scalculator(query):
    q = query['query']
    pos = query['pos']
    usermodel = query['model']
    nr_neighbors = query['nr_neighbors']
    model = models_dic[usermodel]
    results = {'neighbors': [], 'frequencies': {}}
    positive_list = q[0]
    negative_list = q[1]
    plist = []
    nlist = []
    for word in positive_list:
        if len(word) < 2:
            continue
        if word in model.wv.vocab:
            plist.append(word)
            continue
        else:
            q = find_variants(word, usermodel)
            if not q:
                if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(word):
                    results['inferred'] = True
                    plist.append(word)
                else:
                    results["Unknown to the model"] = word
                    return results
            else:
                plist.append(q)
    for word in negative_list:
        if len(word) < 2:
            continue
        if word in model.wv.vocab:
            nlist.append(word)
            continue
        else:
            q = find_variants(word, usermodel)
            if not q:
                if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(word):
                    results['inferred'] = True
                    nlist.append(word)
                else:
                    results["Unknown to the model"] = word
                    return results
            else:
                nlist.append(q)
    if pos == "ALL":
        for w in model.wv.most_similar(positive=plist, negative=nlist, topn=nr_neighbors):
            results['neighbors'].append(w)
    else:
        for w in model.wv.most_similar(positive=plist, negative=nlist, topn=30):
            if w[0].split('_')[-1] == pos:
                results['neighbors'].append(w)
            if len(results['neighbors']) == nr_neighbors:
                break
    if len(results['neighbors']) == 0:
        results['No results'] = True
        return results
    for res in results['neighbors']:
        freq, tier = frequency(res[0], usermodel)
        results['frequencies'][res[0]] = (freq, tier)
    return results


def vector(query):
    q = query['query']
    usermodel = query['model']
    results = {}
    qf = q
    results['frequencies'] = {}
    results['frequencies'][q] = frequency(q, usermodel)
    model = models_dic[usermodel]
    if q not in model.wv.vocab:
        qf = find_variants(qf, usermodel)
        if not qf:
            if our_models[usermodel]['algo'] == 'fasttext' and model.wv.__contains__(q):
                results['inferred'] = True
                qf = q
            else:
                results[q + " is unknown to the model"] = True
                return results
    raw_vector = model[qf]
    raw_vector = raw_vector.tolist()
    results['vector'] = raw_vector
    return results


operations = {'1': find_synonyms, '2': find_similarity, '3': scalculator, '4': vector}

# Bind socket to local host and port

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print('Socket created', file=sys.stderr)

try:
    s.bind((HOST, PORT))
except socket.error as msg:
    print('Bind failed. Error Code and message: ' + str(msg), file=sys.stderr)
    sys.exit()

print('Socket bind complete', file=sys.stderr)

# Start listening on socket
s.listen(100)
print('Socket now listening on port', PORT, file=sys.stderr)

# now keep talking with the client
while 1:
    conn, addr = s.accept()
    # wait to accept a connection - blocking call

    # start new thread takes 1st argument as a function name to be run,
    # 2nd is the tuple of arguments to the function.
    thread = WebVectorsThread(conn, addr)
    thread.start()
