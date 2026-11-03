import os
import openai
import torch
from functools import lru_cache
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings , ChatOpenAI
from src.const.config import ENV_PATH, EMBED_DIMENSION, RERANK_CACHE_PATH
from sentence_transformers import CrossEncoder

load_dotenv(dotenv_path=ENV_PATH, override=False)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL") or os.getenv("LLM_MODEL")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL")
RERANK_MODEL = os.getenv("RERANK_MODEL")

GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL")

LAW_API = os.getenv("LAW_API_OC")

# 임베딩 모델 정의
@lru_cache(maxsize=1)
def create_embedding():
    return OpenAIEmbeddings(
        model=OPENAI_EMBED_MODEL,
        dimensions=EMBED_DIMENSION,
        api_key=OPENAI_API_KEY,
    )

# 임베딩 모델 다른 곳에서 사용하는 법
# from src.const.model import create_embedding
# emb = create_embedding()

# 필요한 다른 모델도 정의하고 위에 예시처럼 사용

# 챗 모델 정의
@lru_cache(maxsize=1)
def create_llm(temperature : float = 0):
    return ChatOpenAI(
        model=OPENAI_LLM_MODEL,
        temperature=temperature,
        api_key=OPENAI_API_KEY
    )

# .env의 리랭크 모델을 CPU에서 생성합니다.
@lru_cache(maxsize=1)
def create_rerank_model():
    if not RERANK_MODEL:
        raise ValueError(".env에 RERANK_MODEL을 지정해야 합니다.")
    # 선택 의존성이라 모델을 사용하는 시점에만 로딩합니다.
    # 5600X급 6코어 환경에서 배치 추론이 병렬 처리되도록 물리 코어 수준을 사용합니다.
    torch.set_num_threads(min(6, os.cpu_count() or 4))
    return CrossEncoder(
        RERANK_MODEL, device="cpu", max_length=512,
        cache_folder=str(RERANK_CACHE_PATH), trust_remote_code=False,
    )

# ============================================================================================================

# 테스트 용 GENIMI VER

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

def create_embedding_gmn():
    return GoogleGenerativeAIEmbeddings(
        model=GEMINI_EMBED_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )

def create_llm_gmn(temperature: float = 0):
    return ChatGoogleGenerativeAI(
        model=GEMINI_LLM_MODEL,
        temperature=temperature,
        google_api_key=GOOGLE_API_KEY,
    )

# 기존 LLM을 재사용하고, 생성 시 출력 길이와 JSON 형식을 지정합니다.
@lru_cache(maxsize=1)
def create_generator_llm():
    return create_llm().bind(
        max_tokens=int(os.environ["GENERATOR_MAX_OUTPUT_TOKENS"]),
        response_format={"type": "json_object"},
    )