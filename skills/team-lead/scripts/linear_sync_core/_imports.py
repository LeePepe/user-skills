"""Shared standard-library and skill_runtime imports for linear_sync_core."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from skill_runtime.cli_contract import SelfDisclosingArgumentParser
