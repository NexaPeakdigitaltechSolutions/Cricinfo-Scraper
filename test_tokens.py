from akamai.edgeauth import EdgeAuth
from constants import EDGE_AUTH_ENCRYPTION_KEY, TOKEN_LIFESPAN
import urllib.parse

def test_tokens():
    et = EdgeAuth(
        key=EDGE_AUTH_ENCRYPTION_KEY,
        window_seconds=TOKEN_LIFESPAN,
        escape_early=True,
    )
    # Token using query path
    print("URL Token:")
    print(et.generate_url_token("/v1/pages/series/schedule?seriesId=1510719"))
    
    # Token using acl
    print("ACL Token (/*):")
    try:
        print(et.generate_acl_token("/*"))
    except Exception as e:
        print("generate_acl_token failed:", e)

if __name__ == "__main__":
    test_tokens()
