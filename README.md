# PLACAT

PLACAT is a voice-based conversational agent built using the Google Home platform, with the goal of combining the advantages of chatbots (user-friendly but not goal- oriented) with the capacities of question answering (QA) systems (which lack interactivity). Thanks to a controller that directs user input either to the chatbot or to the QA system by recognizing dialogue acts, we obtain a spoken QA chatbot over Wikipedia, implemented as a Google Home Action.

## Installation

To run this application, you first need to have an Elasticsearch's index containing Wikipedia's pages. Then you'll have to download the required models. This application has been tested using python=3.7.3 (it appears to break with python 3.8).

### Elasticsearch

1. Download and install [Elasticsearch](https://www.elastic.co/downloads/elasticsearch) (installation's step are at the bottom of the page). We did use the version `6.3.1`.
2. Download a CirrusSearch's dump of Wikipedia (it is a dump of Wikipedia's pages in a format enabling easy indexing on Elasticsearch) at [this page](https://dumps.wikimedia.org/other/cirrussearch/current/). Find one named something similar to `enwiki-20190114-cirrussearch-content.json.gz` for a dump of the classic english Wikipedia. You can try first the `simplewiki-20190114-cirrussearch-content.json.gz` to get a smaller dump and test before commiting to the big one.
3. Run Elasticserach: `systemctl start elasticsearch`
4. Create a new index: `curl -X PUT "localhost:9200/enwiki"`
5. Cut the dump in multiple files (The [Bulk API](https://www.elastic.co/guide/en/elasticsearch/reference/6.3/docs-bulk.html) accepts mass uploads in `ndjson` format but does not handle big files):
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
7. [Optionnal] You can download and install [Kibana](https://www.elastic.co/downloads/kibana) to easily visualize the data.
8. [Optionnal] If you want to keep only some attributes in the index:
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
9. [Optionnal] If you want to delete some pages (that add just noize and thus are not relevant for the application):
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

### [Optional] Google Action & Dialogflow (to use PLACAT on Google Home)

0. Check that your Google account has the following permissions enabled at the [Activity Controls](https://myaccount.google.com/activitycontrols): `Web & App Activity`, `Device Information` and `Voice & Audio Activity`
1. Create a new Google Action project at the [Actions Console](https://console.actions.google.com/). Then create a new Action with a `Custom intent`.
2. Once redirected on Dialogflow, create a new agent.
3. In this agent, create a new intent called `question`.
4. Under `Action and parameters`, add a new parameter named `question` with required checked, `@sys.any` as entity, `$question` as value, is list unchecked and anything you want as prompt.
5. Under `Training phrases`, add any noun (for instance "banana") and double click on it to bind it to the `@sys.any:question` entity.
6. Finally delete all text responses under `Responses` and check `Enable webhook call for this intent` under `Fulfillment`.

In the tab `Fulfillment` for the agent, you can specify the URL for your webhook (that you have to enable). For testing purposes, you can use [ngrok](https://ngrok.com/).

### Models

1. Download and unzip the folder [data](https://drive.google.com/file/d/1Jk-OXLyS1RAlUquwGLEvUpACIbws5amf/view?usp=sharing) into the root of the repository.
2. Download the file [pytorch_model.bin](https://drive.google.com/file/d/1g2wl_A7qhZXZAscNgU47ism9SahrUt47/view?usp=sharing) into the `bert-model/` folder.
3. Install the dependencies (into an virtual environment if you want) with `conda install --file requirements.txt` (you might need to add `conda-forge`'s channel: `conda config --add channels conda-forge` and then `conda config --set channel_priority strict`. You might as well need to install some packages "by hand").
4. Run `python -m spacy download en_core_web_lg` to download the model used by the neuralcoref module to enable pronouns resolution.
5. Execute `./run_backend.sh` to run PLACAT

## Test the application

1. Use the web interface at `http://127.0.0.1:5000/chat` once the server is up (you might need to change the address and/or port)
2. (or) use the `qa.py` script to test one question: `python qa.py -q What is penicillin ?`
3. (or) use the simulator on Dialogflow (if you have set it up in the optional step)
