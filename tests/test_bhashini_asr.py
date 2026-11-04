"""Bhashini ASR request shape. Dhruva answers HTTP 500 (DHRUVA-101 "Failed to send request") when a
bho/hne request has no serviceId, so the client must name a model for languages Dhruva doesn't route.

Offline by default; set BHASHINI_LIVE=1 to also call Bhashini with a synthetic clip.
"""
import array
import base64
import io
import math
import os
import wave

import pytest

from bhashini import client, config


def wav_base64(seconds=1.0):
    samples = array.array('h', (int(3000 * math.sin(2 * math.pi * 220 * i / client.RATE))
                                for i in range(int(seconds * client.RATE))))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(client.RATE)
        out.writeframes(samples.tobytes())
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture()
def no_service_ids(monkeypatch):
    real = config.get
    monkeypatch.setattr(config, 'get', lambda name, default=None:
                        '' if name.startswith('BHASHINI_ASR_SERVICE_ID') or name.startswith('BHASHINI_TTS_SERVICE_ID')
                        else 'test-key' if name == 'BHASHINI_API_KEY' else real(name, default))


@pytest.mark.parametrize('language', ['bho', 'hne'])
def test_unrouted_language_names_a_model(no_service_ids, monkeypatch, language):
    sent = []

    class Response:
        status_code, ok = 200, True

        def json(self):
            return {'pipelineResponse': [{'taskType': 'asr', 'output': [{'source': 'नमस्ते'}]}]}

    monkeypatch.setattr(client.requests, 'post', lambda url, json, **kw: sent.append(json) or Response())
    assert client.transcribe(wav_base64(), language)['text'] == 'नमस्ते'
    assert sent[0]['pipelineTasks'][0]['config']['serviceId'] == 'bhashini/bodhan/asr-transcribe-flex'


def test_routed_language_lets_dhruva_pick(no_service_ids):
    assert 'serviceId' not in client._asr_config('hi')
    assert 'serviceId' not in client._asr_config('ta')


def test_hne_tts_names_a_model(no_service_ids):
    assert client._tts_config('hne', 'female')['serviceId']


@pytest.mark.skipif(not os.environ.get('BHASHINI_LIVE'), reason='set BHASHINI_LIVE=1 to call Bhashini')
@pytest.mark.parametrize('language', ['hi', 'bho', 'hne', 'ta'])
def test_asr_live(language):
    try:
        client.transcribe(wav_base64(), language)
    except client.BhashiniError as error:
        assert error.code == 'speech_empty', error.message  # A tone has no words; anything else is a failure.
