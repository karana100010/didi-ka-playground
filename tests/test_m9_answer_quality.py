"""M9: answer quality seen live on nemotron: a sentence repeated three times, the model naming itself, source ids
and table names read out to her, a format retry taken as being about her, and the critic asking in words."""
from conftest import login
from test_m4 import lokos_id, run_turn
from test_m8_consent_in_text import FIELDS, QUESTION, act, last_observation

INTRO = 'मैं नेमोट्रॉन हूँ, GramStack की सहायक।'


def test_degenerate_catches_a_repeated_sentence_or_phrase():
    from agents.writer import degenerate
    assert degenerate(f'नमस्ते संतोष जी। {INTRO} {INTRO} {INTRO}')
    assert degenerate('आपकी कुल बचत पाँच हज़ार चार सौ रुपये है और ' * 3)          # a long phrase, not one sentence
    assert not degenerate('नमस्ते संतोष जी। आपकी कुल बचत ₹5,400 है। आप हर महीने ₹200 जमा करती हैं। '
                          'क्या आप अपनी लखपति स्थिति भी देखना चाहेंगी?')
    assert not degenerate('संतोष जी, आपकी बचत अच्छी है। संतोष जी, आपकी बचत अच्छी है।')   # twice is not a loop


def test_tidy_drops_the_model_name_and_source_ids():
    from agents.writer import tidy
    assert tidy(f'नमस्ते संतोष जी। {INTRO} आपकी कुल बचत ₹5,400 है।') == 'नमस्ते संतोष जी। आपकी कुल बचत ₹5,400 है।'
    assert tidy('आपकी कुल बचत ₹5,400 है (स्रोत src_3fa9c1, तालिका savings)।') == 'आपकी कुल बचत ₹5,400 है।'
    assert tidy('आपकी कुल बचत ₹5,400 है, स्रोत src_3fa9c1, तालिका savings।') == 'आपकी कुल बचत ₹5,400 है।'
    assert tidy('Your field work is going well.') == 'Your field work is going well.'


def test_prompts_name_the_assistant_and_forbid_ids():
    from agents import prompts
    rules = prompts.system({'name': 'Santosh'}, 'hi', prompts.tool_rules(''))
    assert 'Never call yourself by a model' in rules and 'Never\n  write source ids' in rules


def test_react_answer_is_cleaned_and_format_retry_is_about_the_format(client, fake_llm):
    session = login(client, 'D-005', agent='react', critic=False)
    lokos = lokos_id(client)
    fake_llm.script += [
        'Thought: मैं देखती हूँ।',                                                # no Action: a format retry
        act('request_consent', purpose='savings_question', items=[{'source': lokos, 'table': 'savings', 'fields': FIELDS}]),
        lambda m, t: act('fetch_records', consent_id=last_observation(m)['consent_id'], source=lokos, table='savings', fields=FIELDS),
        f'Final Answer: नमस्ते संतोष जी। {INTRO} आपकी कुल बचत ₹5,400 है (स्रोत {lokos}, तालिका savings)।',
    ]
    events, end = run_turn(client, session, QUESTION)
    retry = fake_llm.calls[1]['messages'][-1]['content']
    assert 'could not read' not in retry and 'format' in retry and 'not about her' in retry
    assert end['runs'][0]['result']['answer'] == 'नमस्ते संतोष जी। आपकी कुल बचत ₹5,400 है।'


def test_a_repeated_sentence_is_not_given_to_her(client, fake_llm):
    session = login(client, 'D-005', agent='direct', critic=False)
    loop = 'मैं दीदी का साथी हूँ, आपकी सहायक।'
    fake_llm.script += [f'नमस्ते संतोष जी। {loop} {loop} {loop}']
    events, end = run_turn(client, session, 'नमस्ते')
    assert loop not in end['runs'][0]['result']['answer']
    assert [e['data']['reason'] for e in events if e['type'] == 'writer_rejected'] == ['degenerate']


def test_no_records_does_not_ask_permission_in_words():
    from agents.critic import NO_RECORDS
    from agents.writer import asks_permission
    assert not any(asks_permission(text) or '?' in text for text in NO_RECORDS.values())
