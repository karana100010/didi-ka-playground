"""Claude models through the logged-in Claude Code CLI (`claude -p`), for judging on this computer only.

Model ids look like "claude-cli/claude-opus-5-5"; nvidia.chat hands them here. The CLI runs with tools off, no
saved session and no MCP servers, in an empty folder so no project instructions or memory reach the prompt. It
uses the CLI's own login, so it never runs on Vercel. Standard library only, so the eval environment can import it.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

PREFIX = 'claude-cli/'
_folder = None


class ClaudeCliError(Exception):
    pass


def available():
    return bool(shutil.which(os.environ.get('CLAUDE_CLI', 'claude'))) and not os.environ.get('VERCEL')


def ask(model, system, prompt, timeout=600):
    """One reply as text. Retries rate limits and overload with back-off."""
    global _folder
    if not available():
        raise ClaudeCliError('The Claude CLI is not available here (it runs only on a computer where you are logged in).')
    _folder = _folder or tempfile.mkdtemp(prefix='claude-cli-judge-')
    command = [os.environ.get('CLAUDE_CLI', 'claude'), '-p', '--model', model.removeprefix(PREFIX), '--tools', '',
               '--output-format', 'json', '--no-session-persistence', '--strict-mcp-config']
    if system:
        command += ['--system-prompt', system]
    # The CLI must use its own login, not the settings of a Claude Code session this process may run inside.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_CODE_', 'CLAUDECODE', 'ANTHROPIC_BASE_URL'))}
    wait, problem = 10, ''
    for _ in range(6):
        try:
            done = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=_folder, env=env)
            reply = json.loads(done.stdout)
        except subprocess.TimeoutExpired:
            reply = {'is_error': True, 'result': f'timed out after {timeout} s'}
        except ValueError:
            reply = {'is_error': True, 'result': (done.stderr or done.stdout)[:300]}
        if not reply.get('is_error'):
            return reply.get('result') or ''
        problem = str(reply.get('result'))
        if 'not logged in' in problem.lower() or '/login' in problem:
            raise ClaudeCliError(f'Claude CLI: {problem}. Run `claude auth login` in a terminal, then retry.')
        if not any(s in problem.lower() for s in ('rate limit', 'overloaded', '429', '529', 'timed out')):
            raise ClaudeCliError(f'Claude CLI: {problem}')
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise ClaudeCliError(f'Claude CLI: no answer after 6 tries ({problem})')


def chat(model, messages, tools=None, temperature=0.2, max_tokens=900, deadline=None, on_wait=None, stop=None):
    """Same shape as nvidia.chat. Text only: no tool calls."""
    from .nvidia import LlmError
    if tools:
        raise LlmError('rejected', f'{model} is used for judging only, without tools.', 422)
    system = '\n\n'.join(m.get('content') or '' for m in messages if m['role'] == 'system')
    turns = [m for m in messages if m['role'] != 'system']
    prompt = turns[0].get('content') or '' if len(turns) == 1 else '\n\n'.join(
        f'{m["role"].upper()}:\n{m.get("content") or ""}' for m in turns)
    started = time.monotonic()
    try:
        text = ask(model, system, prompt)
    except ClaudeCliError as error:
        raise LlmError('unavailable', str(error), 503) from error
    return {'message': {'role': 'assistant', 'content': text}, 'usage': {}, 'model': model,
            'ms': int((time.monotonic() - started) * 1000)}
