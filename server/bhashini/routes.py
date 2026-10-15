"""bhashini_kit's HTTP API (/api/bhashini/* and /api/models), as a router. Request and response shapes
are unchanged, so the kit's web/bhashini.js works against this app as-is."""
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import client as bhashini
from . import config, models

router = APIRouter()

AUDIO = 25_700_000  # base64 of 10 min of 16 kHz 16-bit mono WAV
SHORT_AUDIO = 1_921_000  # 45 s
Lang = Field(default='hi', min_length=2, max_length=4)
Model = Field(default=None, max_length=120, description='service ID from GET /api/models; empty = default')


class AsrRequest(BaseModel):
    service_id: str | None = Model
    audio_content: str = Field(max_length=AUDIO)
    language: str = Field(default='hi', min_length=2, max_length=4, description="language code or 'auto'")


class AudioRequest(BaseModel):
    service_id: str | None = Model
    audio_content: str = Field(max_length=AUDIO)


class SpeechTranslateRequest(BaseModel):
    audio_content: str = Field(max_length=SHORT_AUDIO)
    source: str = Lang
    target: str = Field(default='en', min_length=2, max_length=4)
    speak: bool = False
    gender: str | None = Field(default=None, pattern='^(male|female)$')
    asr_service_id: str | None = Model
    nmt_service_id: str | None = Model
    tts_service_id: str | None = Model


class TranslateRequest(BaseModel):
    service_id: str | None = Model
    text: str = Field(max_length=4000)
    source: str = 'en'
    target: str = 'hi'


class TtsRequest(BaseModel):
    service_id: str | None = Model
    text: str = Field(max_length=4000)
    language: str = Lang
    gender: str | None = Field(default=None, pattern='^(male|female)$')


class TransliterateRequest(BaseModel):
    service_id: str | None = Model
    text: str = Field(max_length=1000)
    source: str = 'en'
    target: str = 'hi'
    sentence: bool = True
    suggestions: int = Field(default=5, ge=1, le=10)


class TextRequest(BaseModel):
    service_id: str | None = Model
    text: str = Field(max_length=4000)


class NerRequest(BaseModel):
    service_id: str | None = Model
    text: str = Field(max_length=4000)
    language: str = Lang


class OcrRequest(BaseModel):
    service_id: str | None = Model
    image_content: str | None = Field(default=None, max_length=14_000_000)
    image_url: str | None = Field(default=None, max_length=2000)
    language: str = Lang


async def _call(fn, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except bhashini.BhashiniError as error:
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status)


@router.post('/api/bhashini/asr')
async def asr(body: AsrRequest):
    return await _call(bhashini.transcribe, body.audio_content, body.language, body.service_id)


@router.post('/api/bhashini/detect-audio-language')
async def detect_audio_language(body: AudioRequest):
    return await _call(bhashini.detect_audio_language, body.audio_content, body.service_id)


@router.post('/api/bhashini/speech-translate')
async def speech_translate(body: SpeechTranslateRequest):
    return await _call(bhashini.speech_translate, body.audio_content, body.source, body.target,
                       body.speak, body.gender, body.asr_service_id, body.nmt_service_id, body.tts_service_id)


@router.post('/api/bhashini/denoise')
async def denoise(body: AudioRequest):
    return await _call(bhashini.denoise, body.audio_content, body.service_id)


@router.post('/api/bhashini/translate')
async def translate(body: TranslateRequest):
    return await _call(bhashini.translate, body.text, body.source, body.target, body.service_id)


@router.post('/api/bhashini/tts')
async def tts(body: TtsRequest):
    return await _call(bhashini.synthesize, body.text, body.language, body.gender, body.service_id)


@router.post('/api/bhashini/transliterate')
async def transliterate(body: TransliterateRequest):
    return await _call(bhashini.transliterate, body.text, body.source, body.target, body.sentence, body.suggestions,
                       body.service_id)


@router.post('/api/bhashini/detect-language')
async def detect_language(body: TextRequest):
    return await _call(bhashini.detect_language, body.text, body.service_id)


@router.post('/api/bhashini/ner')
async def ner(body: NerRequest):
    return await _call(bhashini.entities, body.text, body.language, body.service_id)


@router.post('/api/bhashini/ocr')
async def ocr(body: OcrRequest):
    return await _call(bhashini.ocr, body.language, body.image_content, body.image_url, body.service_id)


@router.get('/api/models')
async def list_models():
    """Selectable models per task, with languages and the configured default for each."""
    prefixes = {'asr': 'ASR', 'translation': 'NMT', 'transliteration': 'XLIT', 'tts': 'TTS', 'ocr': 'OCR', 'ner': 'NER',
                'audio-lang-detection': 'ALD', 'txt-lang-detection': 'TLD', 'denoiser': 'DENOISER'}
    return {'models': models.MODELS,
            'defaults': {task: config.get(f'BHASHINI_{prefix}_SERVICE_ID') for task, prefix in prefixes.items()},
            'source': 'https://dibd-bhashini.gitbook.io/bhashini-apis/available-models-for-usage'}


def key_set():
    return bool(config.get('BHASHINI_API_KEY'))
