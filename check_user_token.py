import datetime
import hmac
import hashlib

exp = 1793874608
dt = datetime.datetime.fromtimestamp(exp)
print("Token Expiry:", dt)
print("Current Local Time:", datetime.datetime.now())

# Check if our existing EDGE_AUTH_ENCRYPTION_KEY matches this token!
from constants import EDGE_AUTH_ENCRYPTION_KEY
from akamai.edgeauth import EdgeAuth

print("Constants key:", EDGE_AUTH_ENCRYPTION_KEY)

# The path in the user's curl was:
path = "/v1/edition/details?lang=en&edition=in&navigation=true&trendingMatches=true&keySeriesItems=true&sponsoredItems=true&promotedItems=true&askQuestionContent=true"

# Let's test EdgeAuth with the key from constants.py
et = EdgeAuth(
    key=EDGE_AUTH_ENCRYPTION_KEY,
    start_time=None,
    window_seconds=30*60,
    escape_early=True
)

# Test HMAC with different keys
def test_key(k, data):
    import binascii
    try:
        raw_key = binascii.a2b_hex(k)
        h = hmac.new(raw_key, data.encode('utf-8'), hashlib.sha256).hexdigest()
        return h
    except Exception as e:
        return str(e)

token_str = f"exp={exp}~hmac=8c902d98ce26ce768c378efda48c5e58f0aeb46eed0b1ff9cc6cd423b15a5634"
print("User token:", token_str)

