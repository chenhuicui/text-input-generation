# src/llm_integration/llm_chatter.py
import logging
from typing import Dict

import requests
import urllib3

# 禁用特定警告


logger = logging.getLogger(__name__)


class LLMChatter:
    def __init__(self, config: dict):
        self.api_key = config['api_key']
        self.base_url = config['base_url']
        self.model_type = config['model_type']
        self.max_retries = config['max_retries']
        self.verify_ssl = config.get('verify_ssl', True)

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def chat_completion(self, message: str, session_id: str = "") -> Dict:
        payload = {
            "modelType": self.model_type,
            "sessionId": session_id,
            "message": message
        }

        try:
            response = self.session.post(
                self.base_url,
                json=payload,
                timeout=30,
                verify=self.verify_ssl
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            return {}
