# PLACAT

PLACAT is a voice-based conversational agent built using the Google Home platform, with the goal of combining the advantages of chatbots (user-friendly but not goal- oriented) with the capacities of question answering (QA) systems (which lack interactivity). Thanks to a controller that directs user input either to the chatbot or to the QA system by recognizing dialogue acts, we obtain a spoken QA chatbot over Wikipedia, implemented as a Google Home Action.

The development of PLACAT is supported by a grant from the [HES-SO](https://www.hes-so.ch/) (AGP n. 82681).  The main developer is [Gabriel Luthier](https://github.com/gluthier) and the principal investigator is [Andrei Popescu-Belis](http://iict-space.heig-vd.ch/apu/), both at [HEIG-VD](https://heig-vd.ch/), Yverdon-les-Bains, Switzerland.  The outcomes of the PLACAT project are summarized in the following article: Luthier G. and Popescu-Belis A., [Chat or Learn: a Data-Driven Robust Question-Answering System](http://www.lrec-conf.org/proceedings/lrec2020/pdf/2020.lrec-1.672.pdf), *Proceedings of LREC 2020 (12th Language Resources and Evaluation Conference)*, Marseille, 11-16 May 2020, p. 5474-5480. 

## Installation

To run this application, you first need to have an Elasticsearch index containing Wikipedia pages. Then you'll have to download the required models. This application has been tested using python=3.7.3 (it appears to break with python 3.8).

### Elasticsearch

1. Download and install [Elasticsearch](https://www.elastic.co/downloads/elasticsearch) (installation steps are listed at the bottom of the page).  We used version `6.3.1`.
2. Download a [CirrusSearch dump](https://dumps.wikimedia.org/other/cirrussearch/current/) of Wikipedia (a dump of Wikipedia pages in a format enabling indexing on Elasticsearch). The first file named `enwiki-20190114-cirrussearch-content.json.gz` (or similar) is a dump of the English Wikipedia. For a smaller file, e.g. for testing, you can try first the Simple English dump named `simplewiki-20190114-cirrussearch-content.json.gz`.
3. Run Elasticserach: `systemctl start elasticsearch`.
4. Create a new index: `curl -X PUT "localhost:9200/enwiki"`.
5. Cut the dump in multiple files (the [Bulk API](https://www.elastic.co/guide/en/elasticsearch/reference/6.3/docs-bulk.html) accepts mass uploads in `ndjson` format but does not handle big files):
```sh
export dump=enwiki-20190114-cirrussearch-content.json.gz
export index=enwiki

mkdir chunks
cd chunks
zcat ../$dump | split -a 10 -l 500 - $index

for file in *; do
  echo -n "${file}:  "
  took=$(curl -s -H "Content-Type: application/x-ndjson" -XPOST localhost:9200/$index/_bulk --data-binary @$file |
    grep took | cut -d':' -f 2 | cut -d',' -f 1)
  printf '%7s\n' $took
  [ "x$took" = "x" ] || rm $file
done
```
6. You can now test the index by executing a simple search query:
```sh
curl -X GET "localhost:9200/$index/_search" -H 'Content-Type: application/json' -d'
{
  "query": {
    "simple_query_string" : {
        "query": "Switzerland",
        "fields": ["title"]
    }
  }
}
'
```
7. [Optional] Download and install [Kibana](https://www.elastic.co/downloads/kibana) to visualize the data.
8. [Optional] If you want to keep only some attributes in the index:
```sh
curl -X POST "localhost:9200/_reindex" -H 'Content-Type: application/json' -d'
{
  "source": {
    "index": "enwiki",
    "_source": ["title", "opening_text"]
  },
  "dest": {
    "index": "enwiki_clean"
  }
}
'
```
9. [Optional] If you want to delete individual pages (which may just add noise to the QA system):
```sh
# Find the page's id
curl -X GET "localhost:9200/enwiki/_search" -H 'Content-Type: application/json' -d'
{
  "query": {
    "term": { "title": "where is where" }
  }
}
'

# Test if the id is the right one
curl -X GET "localhost:9200/enwiki/_search" -H 'Content-Type: application/json' -d'
{
  "query": {
    "terms": { "_id": [ "36897462" ] }
  }
}
'

# Delete the page
curl -X DELETE "localhost:9200/enwiki/page/36897462"
```

### [Optional] Google Action & Dialogflow (to use PLACAT on the Google Home smart speaker)

0. Check that your Google account has the following permissions enabled at the [Activity Controls](https://myaccount.google.com/activitycontrols): `Web & App Activity`, `Device Information` and `Voice & Audio Activity`
1. Create a new Google Action project at the [Actions Console](https://console.actions.google.com/). Then create a new Action with a `Custom intent`.
2. Once redirected to Dialogflow, create a new agent.
3. In this agent, create a new intent called `question`.
4. Under `Action and parameters`, add a new parameter named `question` with `required` checked, `@sys.any` as entity, `$question` as value, and `is list` unchecked.  Write the prompt text that you wish to use.
5. Under `Training phrases`, add any noun (for instance "banana") and double click on it to bind it to the `@sys.any:question` entity.
6. Delete all text responses under `Responses` and check `Enable webhook call for this intent` under `Fulfillment`.
7. In the tab `Fulfillment` for the agent, specify the URL for your webhook, which you must enable. For testing purposes, you can use [ngrok](https://ngrok.com/).

### Models

1. Download and unzip the chatbot model [8000_checkpoint.tar](https://drive.google.com/file/d/1ha8DX6VvX8BCRY0vNn42i2GVmnJKcwTP/view?usp=sharing) (488MB) into the `data/save/bnc_cornell/2-2_500/` folder. The model has been trained using data from the [Cornell Movie-Dialogs Corpus](http://www.cs.cornell.edu/~cristian/Cornell_Movie-Dialogs_Corpus.html) and the [British National Corpus](http://www.natcorp.ox.ac.uk/) zipped up together.
2. Download and unzip the question-answering model [pytorch_model.bin](https://drive.google.com/file/d/10SykYKUNtP7cT-1FiQZKj5hODpp8bl-3/view?usp=sharing) (387MB) into the `bert-model/` folder.
3. Download the controller model [controller.pt](https://drive.google.com/file/d/1mnpTruT0kM42JS6TXeNxCCfg9PKXxVpX/view?usp=sharing) (132KB) into the `data/` folder.
4. Install the dependent packages, for instance into a virtual environment with `conda install --file requirements.txt`.  You might need to add `conda-forge`'s channel: `conda config --add channels conda-forge` and then `conda config --set channel_priority strict`. You might as well need to install some packages manually.
5. Run `python -m spacy download en_core_web_lg` to download the model used by the `neuralcoref` module to enable pronouns resolution.
6. Execute `./run_backend.sh` to run PLACAT

## Test the application

Use one of the following methods:
1. Web interface at `http://127.0.0.1:5000/chat` once the server is up (adjust address and/or port depending on your server).
2. `qa.py` script to test one question: `python qa.py -q What is penicillin ?`
3. Simulator on Dialogflow, if you have set it up in the optional step.
