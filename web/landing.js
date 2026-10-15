import { api, busy, errorNotice, topBar } from '/api.js';

topBar(null);

const message = document.getElementById('message');

document.getElementById('sample').addEventListener('click', event => busy(event.currentTarget, 'Loading samples…', async () => {
    message.replaceChildren();
    try {
        await api('/v1/sources/sample', { method: 'POST' });
        location.href = '/login';
    } catch (error) {
        message.replaceChildren(errorNotice(error));
    }
}));

try {
    const { sources } = await api('/v1/sources');
    const primary = sources.find(s => s.setup.role === 'primary' && s.setup.confirmed);
    if (primary) {
        document.getElementById('resume-text').textContent =
            `${sources.length} source${sources.length === 1 ? '' : 's'} connected. The didis come from ${primary.name}.`;
        document.getElementById('resume').hidden = false;
    }
} catch { /* the hero still works; errors show when the user acts */ }
