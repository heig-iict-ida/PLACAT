import argparse
import sys
import requests
import json


OPTS = None


def parse_args():
    parser = argparse.ArgumentParser(
        description='QA system: elasticsearch + BERT')

    parser.add_argument('-q', '--question', dest='question', metavar='W', nargs='+',
                        help='Question to be answered.')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose mode.')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    return parser.parse_args()


def main():
    question = ' '.join(OPTS.question)

    url = 'http://127.0.0.1:5000/'
    headers = { 'Content-Type': 'application/json' }
    payload = { 'queryResult': { 'queryText': question },
                'session': '123456' }

    r = requests.post(url, data=json.dumps(payload), headers=headers)

    answer = r.json()['fulfillmentText']
    if not answer:
        answer = 'No answer'

    print(answer)


if __name__ == '__main__':
    OPTS = parse_args()
    main()
