## Requirements
- Python 3.10 (since web3.py (a library used by the Python scripts here) is not compatible with Python 3.11)
- Run `pip install -r requirements.txt` to install the required Python packages

## Tests Checklist
- [ ] with-srp, itp 15, threshold -1.0 (`./run.sh <ip_addr> <http_port> "with-srp" "15" "-0.30" <username>`)
- [ ] with-srp, itp 15, threshold -0.5 (`./run.sh <ip_addr> <http_port> "with-srp" "15" "-0.15" <username>`)
- [ ] with-srp, itp 15, threshold 0.0 (`./run.sh <ip_addr> <http_port> "with-srp" "15" "0.0" <username>`)
- [ ] without-srp, threshold -1.0 (`./run.sh <ip_addr> <http_port> "without-srp" "-0.30" <username>`)
- [ ] without-srp, threshold -0.5 (`./run.sh <ip_addr> <http_port> "without-srp" "-0.15" <username>`)
- [ ] without-srp, threshold 0.0 (`./run.sh <ip_addr> <http_port> "without-srp" "0.0" <username>`)

`<username>` is any string identifier e.g. `kenneth`. Make sure the directories where the logs and computed metrics will be stored are created before running `run.sh` (see the directories in `./simulation/simulations/logs/kenneth` to see how the directories are named). After running the tests, check the logs in `./simulation/simulations/logs/<username>` directory and the computed metrics in `./simulation/simulations/computed-metrics/<username>` directory.