from kiteconnect import KiteConnect

from core.config import (
    KITE_API_KEY,
    KITE_API_SECRET,
)

from core.utils import load_session


class ZerodhaAuth:

    def __init__(self):
        self.kite = KiteConnect(api_key=KITE_API_KEY)

    def get_login_url(self):
        return self.kite.login_url()

    def generate_session(self, request_token):
        return self.kite.generate_session(
            request_token=request_token,
            api_secret=KITE_API_SECRET,
        )

    def get_authenticated_client(self):

        session = load_session()

        if session is None:
            raise Exception(
                "No active session found. Please login first."
            )

        self.kite.set_access_token(
            session["access_token"]
        )

        return self.kite