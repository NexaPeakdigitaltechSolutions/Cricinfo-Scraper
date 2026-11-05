import binascii
import hmac
import hashlib

# In Akamai EdgeAuth, what data is hashed?
# Usually: "exp={exp}~data={data}~url={url}" or similar, or just the url path with exp!
# Let's inspect akamai.edgeauth source code or test how EdgeAuth computes the hmac!
from akamai.edgeauth import EdgeAuth
from constants import EDGE_AUTH_ENCRYPTION_KEY

path = "/v1/edition/details?lang=en&edition=in&navigation=true&trendingMatches=true&keySeriesItems=true&sponsoredItems=true&promotedItems=true&askQuestionContent=true"

# Let's see how EdgeAuth builds the hash string
et = EdgeAuth(key=EDGE_AUTH_ENCRYPTION_KEY, escape_early=True)
# EdgeAuth has an internal method or we can inspect it:
import inspect
print(inspect.getsource(et.generate_url_token))

