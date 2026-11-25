"""Shared standard-library imports for the M2 router domain modules."""
from __future__ import annotations

import argparse
import copy
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
