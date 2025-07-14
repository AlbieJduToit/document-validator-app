import os
import logging
from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger(__name__)


def create_keyfile_dict():
    variables_keys = {
        "type": "service_account",
        "project_id": os.getenv("GOOGLE_PROJECT_ID"),
        "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
        "private_key": os.getenv("GOOGLE_SERVICE_API_KEY").replace("\\n", "\n"),
        "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "auth_uri": os.getenv("GOOGLE_AUTH_URI"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": os.getenv("GOOGLE_AUTH_PROVIDER_CERT"),
        "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_CERT"),
        "universe_domain": "googleapis.com"
    }
    return variables_keys

