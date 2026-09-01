"""
Configuration for the EPL Outright Pricing Tool.

Copy this file to `config.py` and fill in the real internal hostnames.
`config.py` is gitignored so internal infrastructure names never get committed.

    cp config.example.py config.py
    # then edit config.py with the real hostnames

Ask a colleague or check the internal runbook for the correct hostnames.
"""

# SLOT — OBP Query Server (OQS pricing)
SLOT_BASE_URL = "https://slot-<env>.<internal-domain>"

# PES — Probabilistic Event Simulation (calculateProbability)
PES_BASE_URL = "http://<pes-host>:8080/ProbabilityEngine/v1.1/calculateProbability"

# SpringBox — downstream market publisher
SPRINGBOX_BASE_URL = "https://spbox-<env>.<internal-domain>"

# SOCOQS — Soccer Outcome Query Server (round-trip / fallback engine)
SOCOQS_BASE_URL = "https://socoqs-<env>.<internal-domain>"

# Default game / fixture ID (EPL Outrights)
DEFAULT_GAME_ID = "40680329"
