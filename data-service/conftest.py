import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("INTERNAL_TOKEN", "token-de-tests")
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    "$pbkdf2-sha256$29000$W2ut1TrH2HsvhRBiTCkF4A$Jfok.x3ndPveJJZySg7ULTI/8Fe3WQ5nNyXLW2DQgP8",
)
