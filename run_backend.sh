trap "exit" INT TERM ERR
trap "systemctl stop elasticsearch; kill 0" EXIT

export FLASK_ENV=development
export FLASK_APP=app.py

systemctl is-active elasticsearch || systemctl start elasticsearch
flask run

wait
