import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_MODEL = (os.environ.get("OPENAI_MODEL") or "gpt-4o").strip() or "gpt-4o"
_MAX_RETRIES = int((os.environ.get("OPENAI_MAX_RETRIES") or "8").strip() or "8")

# Retries help with transient 429 TPM/RPM (OpenAI honors Retry-After).
llm = ChatOpenAI(
    model=_MODEL,
    temperature=0,
    max_retries=max(0, _MAX_RETRIES),
)
