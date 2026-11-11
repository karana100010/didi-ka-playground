// The GRAMStack flow model for the plan pages, and the D3 views drawn from it: who sends what to whom in four
// scenarios. A view mounts on any element with data-flow="map | player | sequence | matrix | checks | phases".
// Everything above "views" is plain data and arithmetic, so tests/plans_flow.test.mjs runs it under node.
(() => {
    'use strict';

    // --- model -------------------------------------------------------------------------------------------------------

    // dash is the second encoding beside colour (flow.css says why one is needed)
    const KINDS = [
        { id: 'conv', label: 'Conversation', dash: null },
        { id: 'consent', label: 'Notice, decision, receipt', dash: '9 4' },
        { id: 'secret', label: 'Keyed secrets (VID, OTP)', dash: '0.1 5.5' },
        { id: 'record', label: 'Her record fields', dash: '10 3 2 3' },
        { id: 'token', label: 'Credentials, tokens, grants', dash: '5 3' },
        { id: 'meta', label: 'Requests and status', dash: '2 4' },
    ];
    const STAGES = ['Ask', 'Request', 'Sign in', 'Verify record', 'Consent', 'Grant and fetch', 'Answer', 'After the call'];
    const TRANSPORTS = ['keypad', 'SMS', 'HTTPS', 'mTLS', 'call audio', 'in process'];
    // The main path of a request's state in GRAMStack's lifecycle. WITHDRAWN is a terminal exit beside it.
    const STATES = ['AUTHENTICATION_PENDING', 'BINDING_PENDING', 'CONSENT_PENDING', 'CONSENTED', 'AUTHORIZED', 'REDEEMING',
        'EVIDENCE_COMMITTED', 'RELEASE_ATTEMPTED', 'COMPLETED'];

    const ZONES = [
        { id: 'her', label: 'Her devices', own: 'ext', x: 12, y: 16, w: 184, h: 372 },
        { id: 'pg', label: 'Didi ka Playground · a consumer', own: 'pg', x: 250, y: 16, w: 608, h: 196 },
        { id: 'other', label: 'Other consumers', own: 'ext', dashed: true, x: 870, y: 16, w: 184, h: 196 },
        { id: 'gs', label: 'GRAMStack · trust boundary', own: 'gs', x: 250, y: 272, w: 804, h: 240 },
        { id: 'out', label: 'Outside GRAMStack', own: 'ext', x: 250, y: 568, w: 804, h: 92 },
        { id: 'proc', label: 'Processors', own: 'ext', x: 1074, y: 184, w: 188, h: 196 },
    ];

    // x, y: centre of the card on the map. tag: 'new' or 'changed' by this plan, from the phase named.
    const NODES = [
        { id: 'app', zone: 'her', x: 104, y: 82, label: 'Didi in the app', sub: 'screen, mic, speaker', tag: 'changed', phase: '2',
            role: 'Her view of the playground: a web page with a microphone and a speaker.',
            keeps: 'Nothing once the tab closes.',
            change: 'Sign-in moves to eSignet. The didi picker stays only in mock mode.' },
        { id: 'phone', zone: 'her', x: 104, y: 344, label: 'Didi on a call', sub: 'any phone, keypad', tag: 'new', phase: '3',
            role: 'Any phone. She calls one published number and uses the keypad for anything secret.',
            keeps: 'SMS from eSignet and GRAMStack in her inbox. Nothing else.',
            change: 'A new channel. Consent by voice comes in phase 3 and sign-in on a call in phase 4.' },
        { id: 'conv', zone: 'pg', x: 350, y: 82, label: 'Conversation', sub: 'speech in, speech out', tag: 'changed', phase: '3',
            role: 'Turns her speech into text and Didi’s answer into speech.',
            keeps: 'The conversation for the session. No audio from a protected segment, because none reaches it.',
            change: 'It stops answering calls. GRAMStack’s voice channel holds the call and streams audio to it.' },
        { id: 'agents', zone: 'pg', x: 554, y: 82, label: 'Agents', sub: 'planner, Hindi writer',
            role: 'Plan which records a question needs and write the Hindi answer. The model is Gemma 4, hosted by Bhashini.',
            keeps: 'Nothing between questions.' },
        { id: 'copy', zone: 'pg', x: 758, y: 82, label: 'Received copy', sub: 'encrypted, ≤ 30 min', tag: 'new', phase: '2',
            role: 'The fields from one fetch, kept so a follow-up question does not need a new consent.',
            keeps: 'Approved fields, encrypted per session, for 30 minutes at most and less if the call ends.',
            change: 'Replaces the rows the playground kept after reading a register directly.' },
        { id: 'gw', zone: 'pg', x: 554, y: 172, label: 'gateway.fetch', sub: 'consent cache', tag: 'changed', phase: '2',
            role: 'The one door every read goes through. It looks for a live consent before any fetch.',
            keeps: 'A cache of GRAMStack request ids, statuses and receipt references.',
            change: 'Its ledger becomes a cache of GRAMStack’s consent, checked again before each use.' },
        { id: 'conn', zone: 'pg', x: 758, y: 172, label: 'Connector', sub: 'consumer client',  tag: 'new', phase: '2',
            role: 'The consumer client: token, access request, status, grant and one retrieval. It never retries a data call.',
            keeps: 'The request id, the grant key and whether the one retrieval was tried.',
            change: 'New. A reference version for one membership field is already merged.' },
        { id: 'third', zone: 'other', x: 962, y: 118, label: 'Another consumer', sub: 'helpline, bank, NGO app', tag: 'new', phase: '5b',
            role: 'Any other registered consumer. It gets its own grant and nothing from the playground.',
            keeps: 'Its own copy, under its own recipient terms.',
            change: 'A fictional second consumer proves that the channels are not the playground’s alone.' },
        { id: 'voice', zone: 'gs', x: 350, y: 344, label: 'Voice channel', sub: 'holds every call', tag: 'new', phase: '3',
            role: 'Answers the published number, holds the call, and cuts the consumer’s audio leg during sign-in and consent.',
            keeps: 'The few seconds of her spoken decision, encrypted, for 90 days. No VID or OTP once the step ends.',
            change: 'New, and the largest ask of GRAMStack. Its own design leaves voice to the product.' },
        { id: 'cm', zone: 'gs', x: 554, y: 344, label: 'Consent manager', sub: 'source of truth',  tag: 'changed', phase: '3',
            role: 'Renders the exact notice, records her decision, signs the artefact and holds the consent status.',
            keeps: 'Exact notices, decisions, signed artefacts, status and withdrawals.',
            change: 'Accepts presentation records for voice, and for text with voice.' },
        { id: 'gateway', zone: 'gs', x: 758, y: 344, label: 'Gateway', sub: 'access requests', tag: 'changed', phase: '3',
            role: 'Takes access requests, tracks each request’s state, asks the adapter to verify the subject, and carries the one retrieval.',
            keeps: 'The request and its state. Never the registry’s response.',
            change: 'Sets the consent mode from the channel that serves her, and takes callback requests from consumers.' },
        { id: 'authz', zone: 'gs', x: 962, y: 344, label: 'AuthZ', sub: 'grants, redemption',
            role: 'Decides whether to issue a grant, and redeems it once in the same transaction that checks the consent.',
            keeps: 'Signed policies, grants and the one-use reservation.' },
        { id: 'page', zone: 'gs', x: 350, y: 464, label: 'Consent page', sub: 'text and voice', tag: 'changed', phase: '3',
            role: 'GRAMStack’s own consent page.',
            keeps: 'The presentation record: what was rendered, when, in which language.',
            change: 'Today it is English and text only. The plan adds Hindi and a spoken notice.' },
        { id: 'authn', zone: 'gs', x: 554, y: 464, label: 'AuthN', sub: 'Hydra, sign-in handlers', tag: 'changed', phase: '4',
            role: 'Hydra and GRAMStack’s sign-in handlers together. Issues consumer tokens and signs her in through eSignet.',
            keeps: 'Short-lived sessions and transactions. No citizen accounts.',
            change: 'Adds a decoupled sign-in for calls, where there is no browser to redirect.' },
        { id: 'evidence', zone: 'gs', x: 962, y: 464, label: 'Evidence', sub: 'signed before release',
            role: 'Commits signed consent artefacts and access receipts before anything is released.',
            keeps: 'Signed evidence and its commitments.' },
        { id: 'esignet', zone: 'out', x: 554, y: 622, label: 'eSignet', sub: 'VID and OTP, SMS', tag: 'changed', phase: '4',
            role: 'The identity provider. It checks her VID and sends the OTP to her registered mobile.',
            keeps: 'Identity data, outside this plan.',
            change: 'Its operator has to agree to the decoupled flow.' },
        { id: 'adapter', zone: 'out', x: 758, y: 622, label: 'Registry adapter', sub: 'LokOS, tabular', tag: 'changed', phase: '5',
            role: 'Verifies that she owns the record, checks the grant itself, redeems it once and releases only the approved fields.',
            keeps: 'The source mapping and the retrieval attempt. Source data stays in the registry.',
            change: 'One configurable tabular adapter publishes the playground’s other registers.' },
        { id: 'dedi', zone: 'out', x: 962, y: 622, label: 'DeDi', sub: 'anchors commitments',
            role: 'Public anchor for evidence commitments.',
            keeps: 'Commitments only. No personal data.' },
        { id: 'bhashini', zone: 'proc', x: 1168, y: 244, label: 'Bhashini', sub: 'speech and Gemma 4', tag: 'changed', phase: '2',
            role: 'Speech recognition, speech synthesis and the hosted Gemma 4 model.',
            keeps: 'Whatever its processor agreements allow. This plan sends it no VID and no OTP.',
            change: 'Named as a processor twice: of the playground, and of GRAMStack’s voice channel and page.' },
        { id: 'obs', zone: 'proc', x: 1168, y: 338, label: 'Tracing, errors', sub: 'Langfuse, GlitchTip', tag: 'changed', phase: '2',
            role: 'Traces of the playground’s runs and its error reports.',
            keeps: 'Timings, step names and error types.',
            change: 'Record values, VID, OTP, grants and receipt tokens are stripped first, and a test enforces it.' },
    ];
    const PG = new Set(['conv', 'agents', 'gw', 'conn', 'copy']);

    // A step is one message. prot: inside a protected segment. state: the request state it reaches.
    // tag: a landmark the checks look for. derived: conversation that carries figures from her record.
    const S = (from, to, kind, tr, pay, what, more) => ({ from, to, kind, tr, pay, what, ...more });
    const P = (from, to, kind, tr, pay, what, more) => S(from, to, kind, tr, pay, what, { ...more, prot: true });

    const ask = () => [
        S('conv', 'bhashini', 'conv', 'HTTPS', 'audio for recognition', 'Speech recognition turns a spoken question into text.'),
        S('conv', 'agents', 'conv', 'in process', 'question text', 'The question text goes to the agents.'),
        S('agents', 'bhashini', 'conv', 'HTTPS', 'planner prompt', 'The planner prompt, with her question, goes to Gemma 4 hosted by Bhashini.',
            { why: 'Bhashini hosts the model, so it gets her words as audio and again as text.' }),
        S('agents', 'gw', 'meta', 'in process', 'fields and purpose', 'The plan needs her savings fields, to answer her question about savings.'),
        S('gw', 'conn', 'meta', 'in process', 'start a request', 'No live consent is cached, so a GRAMStack request starts.'),
    ];
    const request = (c, who, accepted) => [
        S(c, 'authn', 'token', 'HTTPS', 'client credentials', `${who} sends its client credentials to Hydra, inside AuthN.`,
            { why: c === 'conn' ? 'When hosted, the secret sits behind a KMS-backed signer, not in an environment variable.'
                : 'It is registered and approved separately from the playground.' }),
        S('authn', c, 'token', 'HTTPS', 'consumer token', 'A consumer token comes back. It names the application, not her.'),
        S(c, 'gateway', 'meta', 'HTTPS', 'POST /v1/access-requests',
            'The access request names the registry, the resource, the exact fields, the purpose and the recipient terms, with an idempotency key.'),
        S('gateway', c, 'meta', 'HTTPS', '202, request id', accepted,
            { state: 'AUTHENTICATION_PENDING', why: 'A 202 says the workflow was accepted. Nothing has been disclosed.' }),
    ];
    // Sign-in by keypad: the same eight messages wherever GRAMStack's voice channel signs her in.
    const keyed = () => [
        P('phone', 'voice', 'secret', 'keypad', 'VID', 'She keys her VID.', { why: 'Keypad tones never pass through speech recognition.' }),
        P('voice', 'authn', 'secret', 'mTLS', 'VID', 'The VID goes to AuthN, which runs the decoupled sign-in.'),
        P('authn', 'esignet', 'secret', 'HTTPS', 'VID, send OTP', 'AuthN asks eSignet to check the VID and send an OTP.',
            { why: 'Attempts are capped per call, per VID and per caller number.' }),
        P('esignet', 'phone', 'secret', 'SMS', 'OTP', 'eSignet sends the OTP by SMS. The message names the one number where it may be keyed.'),
        P('phone', 'voice', 'secret', 'keypad', 'OTP', 'She keys the six digits.'),
        P('voice', 'authn', 'secret', 'mTLS', 'OTP', 'The OTP goes to AuthN.'),
        P('authn', 'esignet', 'secret', 'HTTPS', 'verify OTP', 'AuthN asks eSignet to verify it.'),
        P('esignet', 'authn', 'token', 'HTTPS', 'ID token', 'eSignet returns an ID token: an authenticated principal.'),
    ];
    const bound = M => M('authn', 'gateway', 'token', 'in process', 'verified principal',
        'AuthN hands the verified principal to the Gateway, bound to this request.',
        { state: 'BINDING_PENDING', why: 'Signing in proves who she is. It does not prove the record is hers.' });
    const verify = M => [
        M('gateway', 'adapter', 'token', 'mTLS', 'subject verification', 'The Gateway asks the registry adapter whether this principal owns the requested record.',
            { why: 'The Gateway makes this call, not the sign-in step. The member reference in the request was only a hint.' }),
        M('adapter', 'gateway', 'meta', 'mTLS', 'verified context', 'The adapter returns a verified subject context: an opaque handle, no record data.',
            { state: 'CONSENT_PENDING' }),
    ];
    const signed = M => [
        M('cm', 'evidence', 'consent', 'mTLS', 'signed artefact', 'The consent manager signs the artefact and commits it as evidence. Only then is the consent active.',
            { tag: 'artefact' }),
        M('evidence', 'dedi', 'consent', 'HTTPS', 'commitment', 'The commitment is anchored on DeDi.'),
    ];
    const active = M => M('cm', 'gateway', 'meta', 'mTLS', 'consent.active', 'The consent.active event reaches the Gateway.',
        { state: 'CONSENTED', tag: 'active', why: 'The event is a prompt to read the status again. It cannot undo a withdrawal.' });
    // Consent spoken on a call. The notice and the decision line differ by consumer.
    const spoken = (notice, decision) => [
        P('gateway', 'cm', 'meta', 'mTLS', 'consent request', 'The Gateway creates the consent request from that context.'),
        P('cm', 'voice', 'consent', 'mTLS', 'exact notice', notice),
        P('voice', 'bhashini', 'consent', 'HTTPS', 'notice text for speech', 'The notice text goes to Bhashini to be spoken.'),
        P('voice', 'phone', 'consent', 'call audio', 'spoken notice', 'She hears the notice, then “हाँ के लिए 1, ना के लिए 2 दबाएँ, या बोलिए”.'),
        P('phone', 'voice', 'consent', 'call audio', '“हओ”, then 1', 'She says “हओ”, hears the scope read back, and presses 1.',
            { why: 'The key press is the stronger evidence. Anything unclear is recorded as no.' }),
        P('voice', 'bhashini', 'consent', 'HTTPS', 'spoken answer', 'Her spoken answer goes to Bhashini for recognition.',
            { why: 'Bhashini hears the decision. It never hears the VID or the OTP.' }),
        P('voice', 'cm', 'consent', 'mTLS', 'decision, presentation', decision),
        ...signed(P),
        P('cm', 'phone', 'consent', 'SMS', 'receipt reference', 'She gets a short receipt reference by SMS.'),
        active(P),
    ];
    // Grant, one retrieval, evidence before release. c is the consumer: the playground's connector or another one.
    const grantAndFetch = (c, who, delivered, why) => [
        S(c, 'gateway', 'meta', 'HTTPS', 'GET status', `${who} polls the request. Status CONSENTED, next action: request a grant.`),
        S(c, 'gateway', 'token', 'HTTPS', 'POST …/grant', 'It asks for a grant, with its consumer token and an idempotency key.'),
        S('gateway', 'authz', 'meta', 'in process', 'evaluate',
            'AuthZ checks the consumer, the verified subject, the active consent, the signed registry policy and the deadlines.'),
        S('authz', 'gateway', 'token', 'in process', 'signed grant',
            'AuthZ signs a grant: ES256, bound to the consumer’s certificate, single use, at most 5 minutes.', { state: 'AUTHORIZED', tag: 'issue' }),
        S('gateway', c, 'token', 'HTTPS', 'grant', 'The grant comes back. A retry returns the same grant, never a second one.', { tag: 'grant' }),
        S(c, 'gateway', 'token', 'mTLS', 'GET …/data, grant', 'It presents the grant on the data endpoint, over its client certificate.',
            { why: 'This call spends the consent. It is never retried.' }),
        S('gateway', 'adapter', 'token', 'mTLS', 'retrieval, grant', 'The Gateway passes the grant and the verified presenter to the adapter.'),
        S('adapter', 'authz', 'token', 'mTLS', 'redeem',
            'The adapter checks the grant itself, then redeems it once. The grant is consumed and the consent’s one use is reserved.',
            { state: 'REDEEMING', tag: 'redeem', why: 'Withdrawal and redemption lock the same record. Whichever commits first wins.' }),
        S('adapter', 'evidence', 'consent', 'mTLS', 'access receipt', 'The adapter commits the access receipt before any field leaves.',
            { state: 'EVIDENCE_COMMITTED', tag: 'receipt' }),
        S('adapter', 'gateway', 'record', 'mTLS', 'approved fields', 'Only the approved fields leave the adapter.',
            { state: 'RELEASE_ATTEMPTED', tag: 'release' }),
        S('gateway', c, 'record', 'mTLS', 'fields, receipt reference', delivered, { state: 'COMPLETED', tag: 'deliver', why }),
    ];
    const keep = () => S('conn', 'copy', 'record', 'in process', 'encrypted copy', 'The playground keeps an encrypted copy for this conversation, 30 minutes at most.');
    const answer = () => [
        S('copy', 'agents', 'record', 'in process', 'fields', 'The fields go to the writer.'),
        S('agents', 'bhashini', 'record', 'HTTPS', 'writer prompt', 'The writer prompt, with her record fields, goes to Gemma 4 hosted by Bhashini.',
            { why: 'This is the one place her record fields leave the playground. Bhashini is named as its processor.' }),
        S('agents', 'conv', 'conv', 'in process', 'answer text', 'The Hindi answer comes back as text.', { derived: true }),
        S('conv', 'bhashini', 'conv', 'HTTPS', 'answer text for speech', 'The answer text goes to Bhashini to be spoken. It holds figures from her record.',
            { derived: true }),
    ];

    const RAW = [
        { id: 'call', label: 'Phone call that needs records', stages: [
            ['Ask', [
                S('phone', 'voice', 'conv', 'call audio', 'her question', 'She calls the published number and asks “मेरी बचत कितनी है?”',
                    { why: 'GRAMStack answers every call, so it decides who hears what.' }),
                S('voice', 'conv', 'conv', 'call audio', 'call audio', 'The channel passes the conversation audio to the playground.',
                    { why: 'Only outside protected segments.' }),
                ...ask(),
            ]],
            ['Request', [
                ...request('conn', 'The playground', 'Accepted. The reply carries the request id and where to follow it.'),
                S('conn', 'voice', 'token', 'HTTPS', 'segment token', 'The playground asks the channel for a protected segment, with a signed single-use token.',
                    { why: 'The token is bound to this request and expires with the interaction.' }),
            ]],
            ['Sign in', [
                P('voice', 'phone', 'conv', 'call audio', '“VID डायल कीजिए”', 'The playground’s audio leg is cut. The channel says “पहले पहचान: अपना VID डायल कीजिए”.',
                    { why: 'A different voice and a tone mark the handover. They tell her who is speaking. They are not a security control.' }),
                ...keyed(),
                bound(P),
            ]],
            ['Verify record', verify(P)],
            ['Consent', [
                ...spoken('The consent manager gives the channel the exact notice: purpose, register, fields, retention.',
                    'The decision goes to the consent manager with the presentation record: channel VOICE, language, notice digest and how she confirmed.'),
                S('voice', 'conv', 'meta', 'call audio', 'handback', 'The channel says “आपकी अनुमति दर्ज हो गई” and restores the playground’s leg.'),
            ]],
            ['Grant and fetch', [...grantAndFetch('conn', 'The playground', 'The fields and the receipt reference reach the playground.'), keep()]],
            ['Answer', [
                ...answer(),
                S('conv', 'voice', 'conv', 'call audio', 'answer audio', 'The answer audio goes to the channel.', { derived: true }),
                S('voice', 'phone', 'conv', 'call audio', '“आपकी कुल बचत…”', 'She hears “आपकी कुल बचत ₹12,000 है…”.', { derived: true }),
            ]],
            ['After the call', [
                S('conv', 'obs', 'meta', 'HTTPS', 'redacted trace',
                    'Traces and error reports leave with record values, VID, OTP, grants and receipt tokens removed.',
                    { why: 'A test fails if any of them reach a trace.' }),
                S('gw', 'copy', 'meta', 'in process', 'delete', 'The call has ended, so the copy is deleted.'),
                S('conn', 'gateway', 'meta', 'mTLS', 'handling report: deleted', 'The playground files a handling report for this disclosure: deleted.',
                    { tag: 'handling', why: 'GRAMStack records it as reported. It is the playground’s own statement, not proof.' }),
            ]],
        ] },
        { id: 'app', label: 'In the app, text and voice', stages: [
            ['Ask', [
                S('app', 'conv', 'conv', 'HTTPS', 'her question', 'She asks by voice, or types, in the playground app.'),
                ...ask(),
            ]],
            ['Request', [
                ...request('conn', 'The playground', 'Accepted, with an interaction URL for her browser.'),
                S('conv', 'app', 'meta', 'HTTPS', 'interaction URL', 'The app is told to open GRAMStack’s page.'),
                S('app', 'page', 'meta', 'HTTPS', 'open the page', 'Her browser goes to GRAMStack. The session cookie and the CSRF token are GRAMStack’s own.'),
            ]],
            ['Sign in', [
                S('page', 'authn', 'token', 'in process', 'start sign-in',
                    'The page starts a sign-in bound to this request: authorization code with PKCE, state and nonce.'),
                S('authn', 'esignet', 'token', 'HTTPS', 'redirect', 'Her browser is sent on to eSignet.'),
                S('esignet', 'app', 'secret', 'SMS', 'OTP', 'eSignet sends the OTP by SMS. She types her VID and the OTP on eSignet’s own page.',
                    { why: 'Neither is typed into the playground or into GRAMStack.' }),
                S('esignet', 'authn', 'token', 'HTTPS', 'ID token', 'eSignet calls back with an ID token. AuthN checks the issuer, the audience and the nonce.'),
                bound(S),
            ]],
            ['Verify record', verify(S)],
            ['Consent', [
                S('gateway', 'cm', 'meta', 'mTLS', 'consent request', 'The Gateway creates the consent request from that context.'),
                S('cm', 'page', 'consent', 'in process', 'exact notice', 'The consent manager gives the page the exact notice.'),
                S('page', 'bhashini', 'consent', 'HTTPS', 'notice text for speech', 'The notice text goes to Bhashini to be spoken.'),
                S('page', 'app', 'consent', 'HTTPS', 'notice, shown and spoken', 'She reads the notice and hears it at the same time. She can replay it.'),
                S('app', 'page', 'consent', 'HTTPS', 'tap, or “हाँ”', 'She taps Allow, or says हाँ and sees it shown back to confirm.'),
                S('page', 'cm', 'consent', 'in process', 'decision, presentation',
                    'The decision is recorded with mode TEXT_VOICE, the digests of both renderings and which one decided.',
                    { why: 'The mode comes from this page, not from the playground’s request.' }),
                ...signed(S),
                active(S),
                S('page', 'app', 'meta', 'HTTPS', 'return', 'She goes back to the playground.'),
            ]],
            ['Grant and fetch', [...grantAndFetch('conn', 'The playground', 'The fields and the receipt reference reach the playground.'), keep()]],
            ['Answer', [
                ...answer(),
                S('conv', 'app', 'conv', 'HTTPS', 'answer, shown and spoken', 'She reads and hears the answer.', { derived: true }),
            ]],
        ] },
        { id: 'third', label: 'Another consumer asks', stages: [
            ['Request', [
                ...request('third', 'Another consumer', 'Accepted.'),
                S('third', 'voice', 'meta', 'HTTPS', 'callback request', 'It asks GRAMStack to call her. It never transfers a live call into sign-in.',
                    { why: 'A transferred call could be a scam line imitating the handover.' }),
            ]],
            ['Sign in', [
                P('voice', 'phone', 'conv', 'call audio', 'call from GRAMStack', 'GRAMStack calls her from its published number and asks for her VID.'),
                ...keyed(),
                bound(P),
            ]],
            ['Verify record', verify(P)],
            ['Consent', spoken('The notice names the other consumer as the recipient.', 'The decision is recorded with that consumer’s id.')],
            ['Grant and fetch', grantAndFetch('third', 'The other consumer', 'The fields and the receipt reference reach the other consumer.',
                'The playground takes no part and receives nothing.')],
        ] },
        // The approval she withdraws here was given on an earlier call that dropped before the fetch.
        { id: 'history', label: 'History and withdrawal by call', start: 'CONSENTED', startNote: 'the approval she never used', stages: [
            ['Ask', [
                S('phone', 'voice', 'conv', 'call audio', 'call, then 2', 'She calls the published number and presses 2: “पिछली अनुमतियाँ सुनने के लिए 2 दबाएँ”.'),
            ]],
            ['Sign in', [
                P('voice', 'phone', 'conv', 'call audio', '“VID डायल कीजिए”', 'History needs a fresh sign-in, even straight after another call.',
                    { why: 'A history session is separate from any consent interaction. A receipt number or an SMS link is not enough.' }),
                ...keyed(),
                P('authn', 'voice', 'token', 'mTLS', 'history session', 'AuthN opens a history session: one registry, 5 minutes at most.'),
            ]],
            ['Consent', [
                P('voice', 'cm', 'meta', 'mTLS', 'list her records', 'The channel asks for her records in this registry.'),
                P('cm', 'voice', 'consent', 'mTLS', 'her records', 'Her approvals and refusals come back, each with its organisation and register.',
                    { why: 'A single-use consent is spent by its one fetch. The savings approval from her last call shows as spent.' }),
                P('voice', 'bhashini', 'consent', 'HTTPS', 'list text for speech', 'The list goes to Bhashini to be spoken.'),
                P('voice', 'phone', 'consent', 'call audio', 'spoken list', 'She hears two entries: an approval from a call that dropped before the fetch, and the spent savings approval.'),
                P('phone', 'voice', 'consent', 'keypad', '3: stop it', 'She presses 3 to stop the approval that was never used.'),
                P('voice', 'cm', 'consent', 'mTLS', 'withdrawal', 'The withdrawal goes to the consent manager under her fresh sign-in.'),
                P('cm', 'evidence', 'consent', 'mTLS', 'withdrawal record', 'The withdrawal is committed as evidence. The original approval stays on record.'),
                P('cm', 'gateway', 'meta', 'mTLS', 'consent.withdrawn', 'The Gateway moves that request to WITHDRAWN. No grant can be issued for it.',
                    { state: 'WITHDRAWN', tag: 'withdrawn' }),
                P('phone', 'voice', 'consent', 'keypad', '4: erase', 'She presses 4 to ask the playground to erase what it kept from the spent approval.',
                    { why: 'There is nothing left in a spent consent to withdraw. What remains is the copy.' }),
                P('voice', 'gateway', 'meta', 'mTLS', 'rights case: erasure', 'The channel files a rights case of type erasure, bound to that consent and its receipt. She hears the case reference.',
                    { why: 'Withdrawal, cancellation and erasure are separate operations. Erasure of a recipient’s copy is routed to that recipient.' }),
                P('cm', 'phone', 'consent', 'SMS', 'receipt reference', 'She gets a receipt reference for the withdrawal by SMS.'),
            ]],
            ['After the call', [
                S('conn', 'gateway', 'meta', 'HTTPS', 'GET status', 'The playground’s next status check on that request says WITHDRAWN. It drops the cached consent and asks for nothing.'),
                S('conn', 'gateway', 'meta', 'mTLS', 'poll rights queue', 'The playground polls its private rights-case queue, over its recipient certificate.'),
                S('gateway', 'conn', 'meta', 'mTLS', 'case metadata', 'The erasure case comes back as metadata only: the case, its type, and the consent and receipt it refers to.'),
                S('conn', 'gateway', 'meta', 'mTLS', 'acknowledge', 'The playground acknowledges the case.', { why: 'Accepting a case is not completing it.' }),
                S('gw', 'copy', 'meta', 'in process', 'delete', 'It deletes anything still held from that disclosure.',
                    { why: 'After 30 minutes the copy is normally gone already. The case still gets an answer.' }),
                S('conn', 'gateway', 'meta', 'mTLS', 'handling report: deleted', 'It files a handling report for the disclosure: deleted.', { tag: 'handling' }),
                S('conn', 'gateway', 'meta', 'mTLS', 'resolve', 'It resolves the case. She sees the new status the next time she checks her history.',
                    { why: 'The resolution is the playground’s authenticated report. GRAMStack does not verify the deletion itself.' }),
            ]],
        ] },
    ];

    const byId = Object.fromEntries(NODES.map(n => [n.id, n]));
    const kindOf = Object.fromEntries(KINDS.map(k => [k.id, k]));
    const key = s => `${s.from}>${s.to}`;
    NODES.forEach(n => { n.own = ZONES.find(z => z.id === n.zone).own; });
    const SCENARIOS = RAW.map(({ stages, ...sc }) => {
        const steps = stages.flatMap(([stage, list]) => list.map(s => ({ ...s, stage })));
        let reached = sc.start || null;
        steps.forEach((s, i) => { reached = s.state || reached; Object.assign(s, { i, n: i + 1, sc: sc.id, reached }); });
        return { ...sc, steps };
    });
    const ALL = SCENARIOS.flatMap(sc => sc.steps);
    const PAIRS = new Map();  // every directed link that carries something in any scenario
    ALL.forEach(s => {
        const pair = PAIRS.get(key(s)) || { key: key(s), from: s.from, to: s.to };
        PAIRS.set(pair.key, pair);
    });

    // --- checks: the plan's own rules, tested against the steps above -----------------------------------------------
    // every step tagged `later` has one tagged `earlier` before it in its scenario (and to the same node, if `same`)
    const follows = (later, earlier, same) => {
        const list = ALL.filter(s => s.tag === later);
        return list.length > 0 && list.every(s => ALL.some(e => e.sc === s.sc && e.tag === earlier && e.i < s.i && (!same || e.to === s.to)));
    };
    const leaving = test => ALL.filter(s => test(s) && PG.has(s.from) && !PG.has(s.to));
    const CHECKS = [
        { t: 'No VID or OTP reaches the playground or Bhashini',
            ok: !ALL.some(s => s.kind === 'secret' && (PG.has(s.to) || s.to === 'bhashini')),
            n: 'No flow of keyed secrets ends at a playground component or at Bhashini.' },
        { t: 'Secrets move only in protected segments or on eSignet’s own page',
            ok: ALL.filter(s => s.kind === 'secret').every(s => s.prot || s.from === 'esignet' || s.to === 'esignet'),
            n: 'On a call every secret step is protected. In the app the OTP stays between eSignet and her.' },
        { t: 'The playground takes no part when another consumer asks',
            ok: !ALL.some(s => s.sc === 'third' && (PG.has(s.from) || PG.has(s.to))),
            n: 'Each consumer gets only its own grant.' },
        { t: 'Record fields reach a consumer only after a grant issued to it', ok: follows('deliver', 'grant', true),
            n: 'Each delivery of fields follows a grant returned to the same consumer.' },
        { t: 'No grant is issued before consent.active', ok: follows('issue', 'active'),
            n: 'AuthZ signs a grant only after the consent.active event has moved the request to CONSENTED.' },
        { t: 'Evidence is committed before data leaves the adapter', ok: follows('release', 'receipt'),
            n: 'The access receipt comes before the first field in every scenario that releases fields.' },
        { t: 'Every approval is signed before release', ok: follows('release', 'artefact'),
            n: 'The signed consent artefact exists before any field is released.' },
        { t: 'Record fields leave the playground only to Bhashini, its named processor',
            ok: leaving(s => s.kind === 'record').length > 0 && leaving(s => s.kind === 'record').every(s => s.to === 'bhashini')
                && leaving(s => s.derived).every(s => ['bhashini', 'voice', 'app'].includes(s.to)),
            n: 'The writer prompt is the only such flow. The answer, which is derived from her record, goes to Bhashini for speech and back to her.' },
        { t: 'Traces carry metadata only', ok: ALL.filter(s => s.to === 'obs').every(s => s.kind === 'meta' && !s.derived),
            n: 'Nothing secret, nothing from her record and no consent content goes to Langfuse or GlitchTip.' },
    ];

    // --- geometry of the map ----------------------------------------------------------------------------------------
    const VIEW = [1272, 676], W = 168, H = 48;
    // Long links run through the corridors between zones as right-angle routes with rounded corners. Each link
    // that can be lit at the same time as its neighbour has a lane of its own, so one never hides another:
    // y 228 to 261 between the consumers and GRAMStack, y 404 between GRAMStack's two rows, x 650 / 662 between
    // the second and third columns, x 210 / 224 / 238 beside her devices. Points sit on card edges.
    // A link with no route of its own uses the reverse link's route (authn>conn, authn>third, page>app).
    const ROUTES = {
        'agents>bhashini': [[470, 96], [452, 96], [452, 228], [1084, 228]],
        'conv>bhashini': [[424, 106], [424, 236], [1084, 236]],
        'conv>obs': [[412, 106], [412, 244], [1064, 244], [1064, 338], [1084, 338]],
        'conn>voice': [[700, 196], [700, 253], [380, 253], [380, 320]],
        'third>voice': [[878, 120], [650, 120], [650, 253], [380, 253], [380, 320]],
        'voice>bhashini': [[400, 320], [400, 261], [1084, 261]],
        'page>bhashini': [[434, 456], [452, 456], [452, 261], [1084, 261]],
        'conn>authn': [[674, 184], [662, 184], [662, 464], [638, 464]],
        'third>authn': [[878, 134], [662, 134], [662, 464], [638, 464]],
        'authn>gateway': [[638, 472], [700, 472], [700, 368]],
        'voice>gateway': [[416, 320], [416, 304], [722, 304], [722, 320]],
        'cm>evidence': [[590, 368], [590, 404], [930, 404], [930, 440]],
        'cm>phone': [[554, 368], [554, 404], [210, 404], [210, 358], [188, 358]],
        'adapter>authz': [[842, 612], [852, 612], [852, 392], [896, 392], [896, 368]],
        'adapter>evidence': [[842, 628], [868, 628], [868, 472], [878, 472]],
        'esignet>phone': [[470, 634], [140, 634], [140, 368]],
        'esignet>app': [[470, 622], [224, 622], [224, 130], [148, 130], [148, 106]],
        'app>page': [[172, 106], [172, 118], [238, 118], [238, 464], [266, 464]],
    };
    const rounded = (points, radius = 9) => {
        // stop 3px short of the cards at both ends, so an arrowhead touches the border
        const pts = points.map(p => p.slice());
        [[0, 1], [pts.length - 1, pts.length - 2]].forEach(([end, next]) => {
            const d = Math.hypot(pts[next][0] - pts[end][0], pts[next][1] - pts[end][1]);
            pts[end] = [pts[end][0] + (pts[next][0] - pts[end][0]) / d * 3, pts[end][1] + (pts[next][1] - pts[end][1]) / d * 3];
        });
        const out = [`M${pts[0]}`];
        for (let i = 1; i < pts.length - 1; i++) {
            const [p, q, n] = [pts[i - 1], pts[i], pts[i + 1]];
            const a = Math.hypot(q[0] - p[0], q[1] - p[1]), b = Math.hypot(n[0] - q[0], n[1] - q[1]), r = Math.min(radius, a / 2, b / 2);
            out.push(`L${[q[0] + (p[0] - q[0]) / a * r, q[1] + (p[1] - q[1]) / a * r]}`,
                `Q${q} ${[q[0] + (n[0] - q[0]) / b * r, q[1] + (n[1] - q[1]) / b * r]}`);
        }
        return `${out.join(' ')} L${pts[pts.length - 1]}`;
    };
    // where the line from a card's centre towards a point leaves the card, 3px out
    const clip = (n, [x, y]) => {
        const dx = x - n.x, dy = y - n.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
        const t = Math.min((W / 2 + 3) / (Math.abs(ux) || 1e-9), (H / 2 + 3) / (Math.abs(uy) || 1e-9));
        return [n.x + ux * t, n.y + uy * t];
    };
    // A short link is a curve. With traffic both ways the two directions bend apart; one-way links run straight.
    // If the first choice would cross another card, bends of the other side and of larger size are tried in turn.
    const curve = (a, b, bothWays) => {
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, bend = 12 + len * 0.06;
        const others = NODES.filter(n => n !== a && n !== b);
        let first = null;
        for (const m of bothWays ? [1, -1, 2.2, -2.2, 3.4, -3.4] : [0, 1, -1, 2.2, -2.2, 3.4, -3.4]) {
            const c = [(a.x + b.x) / 2 - dy / len * bend * m, (a.y + b.y) / 2 + dx / len * bend * m];
            const s = clip(a, c), e = clip(b, c);
            const d = `M${s.map(v => v.toFixed(1))} Q${c.map(v => v.toFixed(1))} ${e.map(v => v.toFixed(1))}`;
            first = first || d;
            let clear = true;
            for (let t = 0.05; t < 1 && clear; t += 0.05) {
                const px = (1 - t) ** 2 * s[0] + 2 * (1 - t) * t * c[0] + t * t * e[0];
                const py = (1 - t) ** 2 * s[1] + 2 * (1 - t) * t * c[1] + t * t * e[1];
                clear = !others.some(n => Math.abs(px - n.x) < W / 2 + 6 && Math.abs(py - n.y) < H / 2 + 6);
            }
            if (clear) return d;
        }
        return first;
    };
    const drawn = {};
    const pathOf = k => {
        if (!drawn[k]) {
            const [a, b] = k.split('>'), back = ROUTES[`${b}>${a}`];
            drawn[k] = ROUTES[k] ? rounded(ROUTES[k]) : back ? rounded(back.slice().reverse()) : curve(byId[a], byId[b], PAIRS.has(`${b}>${a}`));
        }
        return drawn[k];
    };

    const problems = [];
    ALL.forEach(s => {
        const where = `${s.sc} ${s.n}`;
        if (!byId[s.from] || !byId[s.to] || s.from === s.to) problems.push(`${where}: bad link ${key(s)}`);
        if (!kindOf[s.kind]) problems.push(`${where}: unknown kind ${s.kind}`);
        if (!TRANSPORTS.includes(s.tr)) problems.push(`${where}: unknown transport ${s.tr}`);
        if (!STAGES.includes(s.stage)) problems.push(`${where}: unknown stage ${s.stage}`);
        if (s.state && s.state !== 'WITHDRAWN' && !STATES.includes(s.state)) problems.push(`${where}: unknown state ${s.state}`);
    });
    Object.keys(ROUTES).forEach(k => { if (!PAIRS.has(k)) problems.push(`route ${k} carries nothing`); });

    globalThis.GramstackFlow = { KINDS, STAGES, STATES, ZONES, NODES, SCENARIOS, CHECKS, ROUTES, PAIRS, pathOf, problems };
    if (typeof document === 'undefined') return;

    // --- views -------------------------------------------------------------------------------------------------------
    const hosts = document.querySelectorAll('[data-flow]');
    if (typeof d3 === 'undefined') {
        hosts.forEach(host => { host.textContent = 'This figure needs the D3 library, which did not load.'; });
        return;
    }
    if (problems.length) console.error('GRAMStack flow model:', problems);

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const players = [], maps = [];
    let serial = 0;

    /** Stroke a path as one data kind: its colour, its dash, and round caps for the dotted one. */
    function stroke(path, kind, width) {
        return path.style('stroke', `var(--k-${kind})`).attr('fill', 'none').attr('stroke-width', width)
            .attr('stroke-dasharray', kindOf[kind].dash).attr('stroke-linecap', kind === 'secret' ? 'round' : null);
    }

    /** A short line in a kind's colour and dash, then its label. The line carries identity; the text stays ink. */
    function kindKey(box, kind) {
        stroke(box.append('svg').attr('viewBox', '0 0 28 8').attr('aria-hidden', 'true').append('path').attr('d', 'M1,4 H27'), kind.id, 2.4);
        box.append('span').text(kind.label);
        return box;
    }

    function legend(root, withSegment) {
        const box = root.append('div').attr('class', 'fl-legend');
        KINDS.forEach(k => kindKey(box.append('span').attr('class', 'fl-key'), k));
        if (withSegment) box.append('span').attr('class', 'fl-key').call(s => s.append('i')).append('span').text('Protected segment');
    }

    function markers(svg, uid) {
        const defs = svg.append('defs');
        [...KINDS.map(k => k.id), 'idle', 'ink'].forEach(k => defs.append('marker').attr('id', `${uid}-${k}`).attr('viewBox', '0 0 10 10')
            .attr('refX', 9).attr('refY', 5).attr('markerUnits', 'userSpaceOnUse').attr('markerWidth', 9).attr('markerHeight', 9)
            .attr('orient', 'auto-start-reverse').append('path').attr('d', 'M0,0 L10,5 L0,10 z')
            .style('fill', k === 'idle' ? 'var(--faint)' : k === 'ink' ? 'var(--ink)' : `var(--k-${k})`));
    }

    let tipBox;
    /** One floating tooltip for the page, above (x, y) in viewport pixels. tip(null) hides it. */
    function tip(text, x, y) {
        tipBox = tipBox || d3.select(document.body).append('div').attr('class', 'fl-tip').attr('role', 'tooltip');
        tipBox.attr('hidden', text == null ? '' : null);
        if (text == null) return;
        const box = tipBox.text(text).node().getBoundingClientRect();
        tipBox.style('left', `${Math.max(8, Math.min(x - box.width / 2, window.innerWidth - box.width - 8))}px`)
            .style('top', `${y - box.height - 12 < 8 ? y + 18 : y - box.height - 12}px`);
    }

    // 16px line icons, one per component
    const ICONS = {
        app: 'M4.5 1.5h7a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zM7 12h2',
        phone: 'M5 1.5h6a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zM6.5 4h3v2.5h-3zM6.5 9h.01M9.5 9h.01M6.5 11.5h.01M9.5 11.5h.01',
        conv: 'M2.5 3h11v7.5h-6l-3 2.5v-2.5h-2z',
        agents: 'M8 2l1.6 4.4L14 8l-4.4 1.6L8 14l-1.6-4.4L2 8l4.4-1.6z',
        copy: 'M4.5 7.5h7v6h-7zM6 7.5V5a2 2 0 0 1 4 0v2.5',
        gw: 'M2.5 3h11l-4 5v5l-3-1.5V8z',
        conn: 'M6 2v3M10 2v3M4.5 5h7v3a3.5 3.5 0 0 1-7 0zM8 11.5V14',
        third: 'M3.5 13.5v-10h6v10M9.5 6.5h3v7M2 13.5h12M5.5 6h2M5.5 8.5h2M5.5 11h2',
        voice: 'M3 6v4M5.5 4v8M8 2.5v11M10.5 4v8M13 6v4',
        cm: 'M4 1.5h5.5l3 3v10H4zM6 9.5l1.5 1.5L10 8',
        gateway: 'M8 2v4M8 10v4M2 8h4M10 8h4M6 6h4v4H6z',
        authz: 'M8 1.5l5 2v4c0 3.2-2.1 5.6-5 7-2.9-1.4-5-3.8-5-7v-4z',
        page: 'M2 3h12v10H2zM2 6h12M4 4.5h.01M6 4.5h.01',
        authn: 'M7.5 5.5a3 3 0 1 0 6 0a3 3 0 1 0-6 0M8.3 7.7L3 13M5.2 10.8l1.6 1.6',
        evidence: 'M4 6a4 4 0 1 0 8 0a4 4 0 1 0-8 0M5.6 9.3L4.5 14 8 12.5l3.5 1.5-1.1-4.7',
        esignet: 'M1.5 3.5h13v9h-13zM4 6.5h3v3H4zM9 7h3.5M9 9.5h3.5',
        adapter: 'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2zM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
        dedi: 'M8 5v9M8 5a1.5 1.5 0 1 0 0-3a1.5 1.5 0 1 0 0 3M3 9.5c0 2.5 2.2 4.5 5 4.5s5-2 5-4.5M5.5 7.5h5',
        bhashini: 'M8 1.5a2 2 0 0 0-2 2v4a2 2 0 0 0 4 0v-4a2 2 0 0 0-2-2zM4 7.5a4 4 0 0 0 8 0M8 11.5V14',
        obs: 'M1.5 8h3l2-5 3 10 2-5h3',
    };

    /** The corner dot: filled for new in this plan, half for changed by it. */
    function tagDot(g, tag) {
        if (tag === 'new') return g.append('circle').attr('class', 'fl-dot').attr('r', 4);
        g.append('circle').attr('class', 'fl-dot-ring').attr('r', 3.5);
        return g.append('path').attr('class', 'fl-dot').attr('d', 'M0,-3.5 A3.5,3.5 0 0,1 0,3.5 Z');
    }

    /** A small pill centred on (x, y). */
    function badge(svg, x, y, text, cls) {
        const g = svg.append('g').attr('class', `fl-badge ${cls}`).attr('transform', `translate(${x},${y})`).style('pointer-events', 'none'), w = text.length * 6.8 + 16;
        g.append('rect').attr('x', -w / 2).attr('y', -11).attr('width', w).attr('height', 22).attr('rx', 11);
        g.append('text').attr('text-anchor', 'middle').attr('y', 3.8).text(text);
        return g;
    }

    /** The component map: zones, cards and every link. The caller picks a mode: light() for the overview, step() for the player. */
    function mapView(box, label) {
        const uid = `fl${++serial}`, zoneOf = id => ZONES.find(z => z.id === id);
        const svg = box.append('svg').attr('class', 'fl-map').attr('viewBox', `0 0 ${VIEW}`).attr('role', 'group').attr('aria-label', label);
        markers(svg, uid);

        const zone = svg.append('g').selectAll('g').data(ZONES).join('g');
        const zoneRect = zone.append('rect').attr('class', d => `fl-zone ${d.own}${d.dashed ? ' dashed' : ''}`)
            .attr('x', d => d.x).attr('y', d => d.y).attr('width', d => d.w).attr('height', d => d.h).attr('rx', 10);
        zone.append('text').attr('class', d => `fl-zlabel ${d.own}`).attr('x', d => d.x + 12).attr('y', d => d.y + 18).text(d => d.label);

        const legendG = svg.append('g').attr('transform', 'translate(1074,16)');
        legendG.append('text').attr('class', 'fl-zlabel').attr('x', 12).attr('y', 18).text('Key');
        [['new', 'New in this plan'], ['changed', 'Changed by this plan']].forEach(([tag, text], k) => {
            tagDot(legendG.append('g').attr('transform', `translate(18,${40 + k * 20})`), tag);
            legendG.append('text').attr('class', 'fl-keytext').attr('x', 32).attr('y', 44 + k * 20).text(text);
        });
        [['pg', 'Playground'], ['gs', 'GRAMStack'], ['', 'Outside both']].forEach(([own, text], k) => {
            legendG.append('rect').attr('class', `fl-zone ${own}`).attr('x', 12).attr('y', 74 + k * 20).attr('width', 12).attr('height', 12).attr('rx', 3);
            legendG.append('text').attr('class', 'fl-keytext').attr('x', 32).attr('y', 84 + k * 20).text(text);
        });

        const edge = svg.append('g').selectAll('path').data([...PAIRS.values()], d => d.key).join('path').attr('class', 'fl-edge').attr('d', d => pathOf(d.key));

        const node = svg.append('g').selectAll('g').data(NODES).join('g').attr('class', d => `fl-node ${d.own}`)
            .attr('transform', d => `translate(${d.x - W / 2},${d.y - H / 2})`).attr('tabindex', 0).attr('role', 'button')
            .attr('aria-label', d => `${d.label}: ${d.sub}`);
        node.append('rect').attr('class', 'card').attr('width', W).attr('height', H).attr('rx', 8);
        node.append('rect').attr('class', 'tile').attr('x', 10).attr('y', 10).attr('width', 28).attr('height', 28).attr('rx', 6);
        node.append('path').attr('class', 'fl-icon').attr('transform', 'translate(16,16)').attr('d', d => ICONS[d.id]);
        const text = node.selectAll('text').data(d => [{ t: d.label, c: 'l', y: 21, max: d.tag ? 98 : 114 }, { t: d.sub, c: 's', y: 37, max: 114 }])
            .join('text').attr('class', d => d.c).attr('x', 46).attr('y', d => d.y).text(d => d.t);
        node.filter(d => d.tag).each(function (d) { tagDot(d3.select(this).append('g').attr('transform', `translate(${W - 11},11)`), d.tag); });
        // labels wider than the card are squeezed to fit; measured again once the web fonts are in
        const fit = () => text.attr('textLength', null).each(function (d) {
            if (this.getComputedTextLength() > d.max) d3.select(this).attr('textLength', d.max).attr('lengthAdjust', 'spacingAndGlyphs');
        });
        fit();
        if (document.fonts) document.fonts.ready.then(fit);

        const pg = zoneOf('pg'), gs = zoneOf('gs');
        const shade = svg.append('rect').attr('class', 'fl-shade').attr('x', pg.x).attr('y', pg.y).attr('width', pg.w).attr('height', pg.h).attr('rx', 10).attr('opacity', 0);
        const cut = badge(svg, 350, 172, 'audio leg cut', '').attr('opacity', 0);
        const segment = badge(svg, gs.x + gs.w - 82, gs.y + 21, 'protected segment', 'gs').attr('opacity', 0);
        const packet = svg.append('circle').attr('class', 'fl-packet').attr('r', 5.5).attr('opacity', 0);

        /** Overview: every link faint, one component's links lit with arrowheads. */
        function light(id) {
            edge.attr('display', null).each(function (d) {
                const on = !!id && (d.from === id || d.to === id);
                const path = d3.select(this).style('stroke', on ? 'var(--ink)' : null).attr('stroke-width', on ? 1.8 : null)
                    .attr('opacity', id && !on ? 0.4 : 1).attr('marker-end', on ? `url(#${uid}-ink)` : null);
                if (on) path.raise();
            });
            node.classed('hot', d => d.id === id).classed('dim', d => !!id && d.id !== id && !PAIRS.has(`${id}>${d.id}`) && !PAIRS.has(`${d.id}>${id}`));
        }

        /** Player: the links walked so far, the current one on top, the rest hidden unless `every`. */
        function step(sc, i, { every, focus }) {
            const cur = sc.steps[i], used = new Set(sc.steps.map(key)), last = new Map();
            sc.steps.slice(0, i + 1).forEach(s => last.set(key(s), s));
            const touches = d => !focus || d.from === focus || d.to === focus;
            let current;
            edge.each(function (d) {
                const isCur = d.key === key(cur), back = `${d.to}>${d.from}`;
                // two directions on one route would hide each other: only the one used last is drawn
                const newer = (ROUTES[d.key] || ROUTES[back]) && last.get(back);
                const past = last.get(d.key) && !(newer && newer.i > last.get(d.key).i) ? last.get(d.key) : null;
                const kind = isCur ? cur.kind : past && past.kind;
                const path = d3.select(this).attr('display', isCur || past || (every && !newer && used.has(d.key)) ? null : 'none');
                if (kind) stroke(path, kind, isCur ? 3 : 1.7);
                else path.style('stroke', null).attr('stroke-width', null).attr('stroke-dasharray', '2 4').attr('stroke-linecap', null);
                path.attr('opacity', !touches(d) ? 0.08 : isCur || !past ? 1 : 0.55).attr('marker-end', `url(#${uid}-${kind || 'idle'})`);
                if (isCur) current = path.raise();
            });
            const inScenario = new Set(sc.steps.flatMap(s => [s.from, s.to]));
            const linked = id => sc.steps.some(s => (s.from === focus && s.to === id) || (s.to === focus && s.from === id));
            node.classed('hot', d => d.id === cur.from || d.id === cur.to)
                .classed('dim', d => (focus ? d.id !== focus && !linked(d.id) : !inScenario.has(d.id)));
            // the playground has an audio leg to cut only in a scenario where the call reaches it
            shade.attr('opacity', cur.prot ? 0.6 : 0);
            cut.attr('opacity', cur.prot && used.has('voice>conv') ? 1 : 0);
            segment.attr('opacity', cur.prot ? 1 : 0);
            zoneRect.classed('prot', d => d.id === 'gs' && !!cur.prot);

            const line = current.node(), length = line.getTotalLength();
            const at = t => { const p = line.getPointAtLength(t * length); packet.attr('cx', p.x).attr('cy', p.y); };
            packet.interrupt().style('fill', `var(--k-${cur.kind})`).attr('opacity', touches(cur) ? 1 : 0);
            if (reduce) at(0.5); else packet.transition().duration(900).ease(d3.easeCubicInOut).tween('move', () => at);
        }

        return { node, light, step, scale: () => svg.node().getBoundingClientRect().width / VIEW[0] };
    }

    /** What one component does, keeps, receives, sends and never receives. Counts come from all four scenarios. */
    function inspect(panel, id) {
        panel.html('');
        if (!id) {
            panel.append('h4').text('Select a component');
            panel.append('p').attr('class', 'fl-note').text('Hover over a component, or select it, to light its links and see what it does, '
                + 'what it keeps and what never reaches it. The counts come from the four scenarios in the dataflow section.');
            return;
        }
        const n = byId[id], title = panel.append('h4');
        title.append('span').text(n.label);
        title.append('span').attr('class', 'fl-pill').text(ZONES.find(z => z.id === n.zone).label);
        if (n.tag) title.append('span').attr('class', 'fl-pill').text(`${n.tag === 'new' ? 'New' : 'Changed'} in phase ${n.phase}`);
        const dl = panel.append('dl'), row = (term, fill) => dl.append('div').call(d => d.append('dt').text(term)).append('dd').call(fill);
        const tally = side => KINDS.map(k => [k, ALL.filter(s => s[side] === id && s.kind === k.id).length]);
        const chips = (dd, list) => {
            const box = dd.append('div').attr('class', 'fl-chips');
            list.filter(([, count]) => count).forEach(([k, count]) => kindKey(box.append('span').attr('class', 'fl-key'), k).append('b').text(`×${count}`));
            if (!list.some(([, count]) => count)) box.text('Nothing');
        };
        row('Role', dd => dd.text(n.role));
        row('Keeps', dd => dd.text(n.keeps));
        if (n.change) row('What this plan changes', dd => dd.text(n.change));
        row('Receives', dd => chips(dd, tally('to')));
        row('Sends', dd => chips(dd, tally('from')));
        row('Never receives', dd => dd.text(tally('to').filter(([, count]) => !count).map(([k]) => k.label).join(' · ') || 'Nothing is ruled out'));
    }

    /** data-flow="map": the architecture map. Hover previews a component, a click pins it. */
    function overview(host) {
        const root = d3.select(host).append('div').attr('class', 'fl');
        const map = mapView(root.append('div').attr('class', 'fl-scroll'), 'Components and trust boundaries after integration');
        const panel = root.append('div').attr('class', 'fl-inspect').attr('aria-live', 'polite');
        let pinned = null, hover = null;
        const show = () => { map.light(hover || pinned); inspect(panel, hover || pinned); };
        const pin = id => { pinned = pinned === id ? null : id; show(); };
        map.node.on('pointerenter focus', (e, d) => { hover = d.id; show(); }).on('pointerleave blur', () => { hover = null; show(); })
            .on('click', (e, d) => pin(d.id))
            .on('keydown', (e, d) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pin(d.id); } });
        show();
        maps.push({ focus(id) { pinned = id; show(); host.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); } });
    }

    // lifelines of a sequence diagram, left to right: her, the playground, other consumers, GRAMStack, outside, processors
    const SEQ = ['phone', 'app', 'conv', 'agents', 'gw', 'conn', 'copy', 'third', 'voice', 'page', 'cm', 'gateway', 'authn', 'authz', 'evidence',
        'esignet', 'adapter', 'dedi', 'bhashini', 'obs'];

    /** A sequence diagram of the given steps. With `pick`, a click on a row calls pick(step). Returns { mark(step) }. */
    function sequenceView(box, steps, pick) {
        const uid = `fl${++serial}`, host = box.node();
        const parts = SEQ.filter(id => steps.some(s => s.from === id || s.to === id)).map(id => byId[id]);
        const left = 34, right = 26, gap = Math.min(132, (VIEW[0] - left - right) / parts.length), width = left + gap * parts.length + right;
        const x = Object.fromEntries(parts.map((p, k) => [p.id, left + gap * (k + 0.5)]));
        const min = `${Math.round(width * 0.8)}px`;

        const head = box.append('div').attr('class', 'fl-seqhead');
        const who = head.append('svg').attr('viewBox', `0 0 ${width} 46`).style('min-width', min).attr('aria-hidden', 'true')
            .selectAll('g').data(parts).join('g').attr('transform', p => `translate(${x[p.id]},0)`);
        who.each(function (p) {
            const words = p.label.split(' '), half = Math.ceil(words.length / 2);
            const lines = p.label.length > 12 && words.length > 1 ? [words.slice(0, half).join(' '), words.slice(half).join(' ')] : [p.label];
            lines.forEach((line, k) => {
                const label = d3.select(this).append('text').attr('text-anchor', 'middle').attr('y', (lines.length === 1 ? 26 : 19) + k * 13).text(line);
                if (line.length * 6.4 > gap - 6) label.attr('textLength', gap - 6).attr('lengthAdjust', 'spacingAndGlyphs');
            });
        });
        who.append('line').attr('class', p => p.own).attr('x1', -24).attr('x2', 24).attr('y1', 42).attr('y2', 42);

        // rows top to bottom: a divider where the stage changes, then one row per step, taller if it reaches a state
        const rows = [], dividers = [], bands = [];
        let y = 4, stage = null;
        steps.forEach(s => {
            if (s.stage !== stage) { stage = s.stage; dividers.push({ y, stage }); y += 24; }
            const row = { s, y, h: s.state ? 50 : 30 }, band = bands[bands.length - 1];
            if (s.prot && band && band.open) band.y1 = y + row.h;
            else if (s.prot) bands.push({ y0: y, y1: y + row.h, open: true });
            else if (band) band.open = false;
            rows.push(row);
            y += row.h;
        });
        const height = y + 8;
        const svg = box.append('svg').attr('class', 'fl-seq').attr('viewBox', `0 0 ${width} ${height}`).style('min-width', min).attr('role', 'img')
            .attr('aria-label', `Sequence diagram of steps ${steps[0].n} to ${steps[steps.length - 1].n}: ${steps.length} messages between ${parts.length} components.`);
        markers(svg, uid);
        svg.append('g').selectAll('rect').data(bands).join('rect').attr('class', 'fl-band').attr('x', 26).attr('width', width - 32)
            .attr('y', b => b.y0).attr('height', b => b.y1 - b.y0).attr('rx', 6);
        svg.append('g').selectAll('text').data(bands.filter(b => b.y1 - b.y0 >= 150)).join('text').attr('class', 'fl-mono').attr('text-anchor', 'middle')
            .attr('transform', b => `translate(${width - 15},${(b.y0 + b.y1) / 2}) rotate(90)`).text('Protected segment');
        svg.append('g').selectAll('line').data(parts).join('line').attr('class', 'fl-life').attr('x1', p => x[p.id]).attr('x2', p => x[p.id]).attr('y1', 0).attr('y2', height);
        const divider = svg.append('g').selectAll('g').data(dividers).join('g');
        divider.append('line').attr('class', 'fl-div').attr('x1', 0).attr('x2', width).attr('y1', d => d.y + 4).attr('y2', d => d.y + 4);
        divider.append('text').attr('class', 'fl-mono halo').attr('x', 6).attr('y', d => d.y + 18).text(d => d.stage);

        const row = svg.append('g').selectAll('g').data(rows).join('g').attr('class', r => `fl-row${r.s.prot ? ' prot' : ''}${pick ? ' pick' : ''}`);
        row.append('rect').attr('class', 'hit').attr('x', 0).attr('y', r => r.y).attr('width', width).attr('height', r => r.h);
        row.append('text').attr('class', 'num').attr('x', 6).attr('y', r => r.y + 23.5).text(r => r.s.n);
        row.each(function (r) {
            const g = d3.select(this), s = r.s, x1 = x[s.from], x2 = x[s.to];
            stroke(g.append('path').attr('d', `M${x1},${r.y + 20} H${x2 - Math.sign(x2 - x1) * 2}`), s.kind, 2).attr('marker-end', `url(#${uid}-${s.kind})`);
            const label = s.pay + (s.derived ? ' · from her record' : ''), half = label.length * 3.1 + 4;
            g.append('text').attr('class', 'msg').attr('text-anchor', 'middle').attr('y', r.y + 13)
                .attr('x', Math.max(left + half, Math.min((x1 + x2) / 2, width - right - half))).text(label);
            if (!s.state) return;
            // the request's state lives in the Gateway, so the pill sits on its lifeline
            const w = s.state.length * 5.9 + 14, pill = g.append('g').attr('class', 'st')
                .attr('transform', `translate(${Math.max(w / 2 + 28, Math.min(x.gateway || x2, width - w / 2 - 28))},${r.y + 38})`);
            pill.append('rect').attr('x', -w / 2).attr('y', -8).attr('width', w).attr('height', 16).attr('rx', 8);
            pill.append('text').attr('text-anchor', 'middle').attr('y', 3.4).text(s.state);
        });
        if (pick) row.on('click', (e, r) => pick(r.s));

        return {
            mark(cur) {
                row.classed('now', r => r.s === cur);
                const at = rows.find(r => r.s === cur);
                if (!at || host.scrollHeight <= host.clientHeight) return;
                const top = head.node().clientHeight + at.y * svg.node().getBoundingClientRect().height / height;
                if (top < host.scrollTop + 70 || top > host.scrollTop + host.clientHeight - 70) {
                    host.scrollTo({ top: top - host.clientHeight / 2, behavior: reduce ? 'auto' : 'smooth' });
                }
            },
        };
    }

    /** data-flow="sequence": some stages of one scenario as a still figure. data-stages is a |-separated list. */
    function staticSequence(host) {
        const sc = SCENARIOS.find(s => s.id === host.dataset.scenario) || SCENARIOS[0];
        const wanted = (host.dataset.stages || '').split('|').filter(Boolean);
        const root = d3.select(host).append('div').attr('class', 'fl');
        legend(root, true);
        sequenceView(root.append('div').attr('class', 'fl-seqwrap'), sc.steps.filter(s => !wanted.length || wanted.includes(s.stage)));
    }

    /** data-flow="player": step through a scenario on the map or as a sequence, with a scrubber and a step table. */
    function player(host) {
        const uid = ++serial, root = d3.select(host).append('div').attr('class', 'fl');
        const st = { sc: Math.max(0, SCENARIOS.findIndex(s => s.id === host.dataset.scenario)), i: 0, view: 'map', every: false, focus: null, timer: null };
        const scenario = () => SCENARIOS[st.sc];
        let built = null, sequence = null, sequenceOf = null;

        const bar = root.append('div').attr('class', 'fl-bar');
        const tabs = bar.append('div').attr('class', 'fl-tabs').attr('role', 'group').attr('aria-label', 'Scenario').selectAll('button').data(SCENARIOS)
            .join('button').attr('class', 'fl-tab').attr('type', 'button').text(d => d.label)
            .on('click', (e, d) => { stop(); Object.assign(st, { sc: SCENARIOS.indexOf(d), i: 0, focus: null }); render(); });
        const controls = bar.append('div').attr('class', 'fl-ctl');
        const button = (text, label, run) => controls.append('button').attr('class', 'fl-btn').attr('type', 'button').attr('aria-label', label).text(text).on('click', run);
        button('‹', 'Previous step', () => { stop(); go(st.i - 1); });
        const playButton = button('Play', null, () => (st.timer ? stop() : play()));
        button('›', 'Next step', () => { stop(); go(st.i + 1); });
        const count = controls.append('span').attr('class', 'fl-count');
        const views = bar.append('div').attr('class', 'fl-seg').attr('role', 'group').attr('aria-label', 'View').selectAll('button')
            .data([['map', 'Map'], ['seq', 'Sequence']]).join('button').attr('class', 'fl-tab').attr('type', 'button').text(d => d[1])
            .on('click', (e, d) => { st.view = d[0]; render(); });
        const every = bar.append('label').attr('class', 'fl-check');
        const everyBox = every.append('input').attr('type', 'checkbox').attr('id', `fl-every-${uid}`).on('change', e => { st.every = e.target.checked; render(); });
        every.append('span').text('Every path');
        const unfocus = bar.append('button').attr('class', 'fl-tab').attr('type', 'button').on('click', () => { st.focus = null; render(); });

        legend(root, true);
        const timeline = root.append('div').attr('class', 'fl-tl');
        const stageRow = timeline.append('div').attr('class', 'fl-tl-stages').attr('aria-hidden', 'true');
        const ticks = timeline.append('div').attr('class', 'fl-tl-ticks').attr('role', 'slider').attr('tabindex', 0).attr('aria-label', 'Step').attr('aria-valuemin', 1);
        const caption = root.append('div').attr('class', 'fl-caption').attr('aria-live', 'polite');
        const mapBox = root.append('div').attr('class', 'fl-scroll');
        const map = mapView(mapBox, 'Dataflow map: the links walked so far in this scenario');
        const seqBox = root.append('div').attr('class', 'fl-seqwrap tall');
        const details = root.append('details').attr('class', 'fl-steps');
        const summary = details.append('summary');
        const table = details.append('div').attr('class', 'fl-tablewrap').append('table');
        table.append('thead').append('tr').selectAll('th').data(['#', 'Stage', 'From, to', 'What moves', 'Kind', 'Transport', 'Notes']).join('th').attr('scope', 'col').text(d => d);
        const tbody = table.append('tbody');

        function go(i) { st.i = Math.max(0, Math.min(i, scenario().steps.length - 1)); render(); }
        function stop() { clearInterval(st.timer); st.timer = null; playButton.text('Play'); }
        function play() {
            if (st.i >= scenario().steps.length - 1) st.i = 0;
            playButton.text('Pause');
            st.timer = setInterval(() => (st.i >= scenario().steps.length - 1 ? stop() : go(st.i + 1)), 2300);
            render();
        }
        const toggleFocus = id => { st.focus = st.focus === id ? null : id; render(); };
        map.node.on('click', (e, d) => toggleFocus(d.id))
            .on('keydown', (e, d) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleFocus(d.id); } });

        // the scrubber: click or drag to a step, arrow keys step, Page Up and Page Down jump by stage
        const under = e => {
            const box = ticks.node().getBoundingClientRect(), n = scenario().steps.length;
            return Math.max(0, Math.min(n - 1, Math.floor((e.clientX - box.left) / box.width * n)));
        };
        ticks.on('pointerdown', e => { stop(); ticks.node().setPointerCapture(e.pointerId); go(under(e)); })
            .on('pointermove', e => {
                const s = scenario().steps[under(e)];
                if (e.buttons) go(s.i);
                tip(`${s.n}. ${byId[s.from].label} → ${byId[s.to].label}\n${s.pay} · ${kindOf[s.kind].label.toLowerCase()}`, e.clientX, ticks.node().getBoundingClientRect().top);
            })
            .on('pointerleave', () => tip(null))
            .on('keydown', e => {
                const steps = scenario().steps, startOf = i => steps.findIndex(s => s.stage === steps[i].stage);
                const next = steps.find(s => s.i > st.i && s.stage !== steps[st.i].stage);
                const to = { ArrowRight: st.i + 1, ArrowUp: st.i + 1, ArrowLeft: st.i - 1, ArrowDown: st.i - 1, Home: 0, End: steps.length - 1,
                    PageDown: next ? next.i : steps.length - 1, PageUp: startOf(st.i) < st.i ? startOf(st.i) : startOf(Math.max(0, st.i - 1)) }[e.key];
                if (to === undefined) return;
                e.preventDefault();
                stop();
                go(to);
            });

        function build() {
            const steps = scenario().steps;
            stageRow.selectAll('span').data(STAGES.filter(g => steps.some(s => s.stage === g))).join('span')
                .style('flex', g => `${steps.filter(s => s.stage === g).length} 0 0px`).html('').append('b').text(g => g);
            ticks.attr('aria-valuemax', steps.length).selectAll('span').data(steps).join('span')
                .attr('class', s => `fl-tick${s.prot ? ' prot' : ''}`).style('--k', s => `var(--k-${s.kind})`).html('<i></i>');
            summary.text(`All ${steps.length} steps as a table`);
            tbody.selectAll('tr').data(steps).join('tr').on('click', (e, s) => { stop(); go(s.i); }).html('').each(function (s) {
                const tr = d3.select(this);
                tr.append('td').attr('class', 'n').append('button').attr('type', 'button').attr('aria-label', `Go to step ${s.n}`).text(s.n);
                tr.append('td').attr('class', 'nw').text(s.stage);
                tr.append('td').text(`${byId[s.from].label} → ${byId[s.to].label}`);
                tr.append('td').text(s.what);
                kindKey(tr.append('td').append('span').attr('class', 'fl-key'), kindOf[s.kind]);
                tr.append('td').attr('class', 'nw').text(s.tr);
                tr.append('td').text([s.prot && 'protected', s.derived && 'from her record', s.state].filter(Boolean).join(' · '));
            });
        }

        function describe(cur) {
            caption.html('');
            const meta = caption.append('div').attr('class', 'fl-meta');
            meta.append('span').attr('class', 'fl-pill').text(cur.stage);
            meta.append('span').text(`${byId[cur.from].label} → ${byId[cur.to].label}`);
            kindKey(meta.append('span').attr('class', 'fl-key'), kindOf[cur.kind]);
            meta.append('span').text(cur.tr);
            if (cur.derived) meta.append('span').attr('class', 'fl-pill').text('from her record');
            if (cur.prot) meta.append('span').attr('class', 'fl-pill prot').text('protected segment');
            caption.append('div').attr('class', 'fl-what').text(cur.what);
            if (cur.why) caption.append('div').attr('class', 'fl-why').text(cur.why);
            // request state: dots along the main path, filled up to the state reached
            const gone = cur.reached === 'WITHDRAWN', at = STATES.indexOf(gone ? 'CONSENTED' : cur.reached);
            const state = caption.append('div').attr('class', 'fl-state');
            state.append('span').text(scenario().startNote ? `Request state (${scenario().startNote})` : 'Request state');
            state.append('span').attr('class', 'fl-dots').attr('aria-hidden', 'true').selectAll('i').data(STATES).join('i')
                .attr('class', (s, k) => `${k <= at ? 'on' : ''}${k === at && !gone ? ' now' : ''}`);
            state.append('b').text(cur.reached || 'no request yet');
            if (gone) state.append('span').text('terminal: no grant can follow');
        }

        function render() {
            const sc = scenario(), cur = sc.steps[st.i];
            if (built !== sc.id) { build(); built = sc.id; }
            tabs.attr('aria-pressed', d => d === sc);
            views.attr('aria-pressed', d => d[0] === st.view);
            count.text(`${cur.n} / ${sc.steps.length}`);
            everyBox.property('checked', st.every);
            every.attr('hidden', st.view === 'map' ? null : '');
            unfocus.attr('hidden', st.focus && st.view === 'map' ? null : '').text(st.focus ? `Only ${byId[st.focus].label} · show all` : '');
            stageRow.selectAll('span').classed('now', g => g === cur.stage);
            ticks.attr('aria-valuenow', cur.n).attr('aria-valuetext', `Step ${cur.n} of ${sc.steps.length}, ${cur.stage}: ${cur.what}`)
                .selectAll('span').classed('now', s => s === cur).classed('past', s => s.i < st.i);
            describe(cur);
            tbody.selectAll('tr').attr('aria-current', s => (s === cur ? 'step' : null));
            mapBox.attr('hidden', st.view === 'map' ? null : '');
            seqBox.attr('hidden', st.view === 'seq' ? null : '');
            if (st.view === 'seq') {
                if (sequenceOf !== sc.id) { sequence = sequenceView(seqBox.html(''), sc.steps, s => { stop(); go(s.i); }); sequenceOf = sc.id; }
                return sequence.mark(cur);
            }
            map.step(sc, st.i, st);
            // on a narrow screen the map scrolls sideways: keep the current link in view
            const box = mapBox.node();
            if (box.scrollWidth > box.clientWidth + 4) {
                box.scrollTo({ left: (byId[cur.from].x + byId[cur.to].x) / 2 * map.scale() - box.clientWidth / 2, behavior: reduce ? 'auto' : 'smooth' });
            }
        }

        players.push({
            /** Show one component's links: in this scenario if it takes part, else in the first one where it does. */
            focus(id) {
                stop();
                const takesPart = sc => sc.steps.some(s => s.from === id || s.to === id);
                if (!takesPart(scenario())) Object.assign(st, { sc: SCENARIOS.findIndex(takesPart), i: 0 });
                Object.assign(st, { focus: id, view: 'map', every: true });
                render();
                mapBox.node().scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
            },
        });
        render();
    }

    /** data-flow="matrix": who receives what. Bubble area is the number of flows received across the four scenarios. */
    function matrix(host) {
        const root = d3.select(host).append('div').attr('class', 'fl');
        const table = root.append('div').attr('class', 'fl-scroll').append('table').attr('class', 'fl-matrix');
        const headRow = table.append('thead').append('tr');
        headRow.append('th').attr('scope', 'col').text('Component');
        KINDS.forEach(k => kindKey(headRow.append('th').attr('scope', 'col').append('span').attr('class', 'fl-key'), k));
        const body = table.append('tbody'), into = (n, k) => ALL.filter(s => s.to === n.id && s.kind === k.id);
        const most = Math.max(...NODES.flatMap(n => KINDS.map(k => into(n, k).length)));
        const show = id => {
            (players[0] || maps[0])?.focus(id);
            body.selectAll('tr').classed('hl', function () { return this.dataset.node === id; });
        };
        ZONES.forEach(z => {
            body.append('tr').attr('class', `zone ${z.own}`).append('th').attr('colspan', KINDS.length + 1).attr('scope', 'colgroup').text(z.label);
            NODES.filter(n => n.zone === z.id).forEach(n => {
                // the whole row is the target; the button in it is what the keyboard reaches
                const tr = body.append('tr').attr('data-node', n.id).on('click', () => show(n.id));
                tr.append('th').attr('scope', 'row').append('button').attr('type', 'button').attr('aria-label', `${n.label}: show its links on the map`).text(n.label);
                KINDS.forEach(k => {
                    const got = into(n, k);
                    if (!got.length) return tr.append('td').attr('class', 'z').text('·');
                    const td = tr.append('td').attr('class', 'c'), r = 14 * Math.sqrt(got.length / most);
                    const svg = td.append('svg').attr('viewBox', '0 0 34 30').attr('aria-hidden', 'true');
                    // a ring in the record colour: some of these flows carry an answer derived from her record
                    if (got.some(s => s.derived)) svg.append('circle').attr('cx', 17).attr('cy', 15).attr('r', Math.min(14, r + 3.2)).attr('fill', 'none').attr('stroke-width', 1.6).style('stroke', 'var(--k-record)');
                    svg.append('circle').attr('cx', 17).attr('cy', 15).attr('r', r).style('fill', `var(--k-${k.id})`);
                    td.append('span').text(got.length);
                    const lines = [...new Set(got.map(s => `${s.pay}, from ${byId[s.from].label}`))];
                    td.on('pointerenter pointermove', e => tip(`${n.label} receives ${got.length} × ${k.label.toLowerCase()}\n${lines.slice(0, 6).join('\n')}${lines.length > 6 ? '\n…' : ''}`, e.clientX, e.clientY))
                        .on('pointerleave', () => tip(null));
                    return td;
                });
            });
        });
        root.append('p').attr('class', 'fl-note').text('Bubble area is the number of flows a component receives across all four scenarios. '
            + 'A green ring marks conversation that carries an answer derived from her record. Select a component to see its links on the map.');
    }

    /** data-flow="checks": each rule with a PASS or FAIL icon and label. */
    function checks(host) {
        const item = d3.select(host).append('ul').attr('class', 'fl fl-checks').selectAll('li').data(CHECKS).join('li');
        const result = item.append('span').attr('class', c => `fl-res${c.ok ? ' ok' : ''}`);
        result.append('svg').attr('viewBox', '0 0 16 16').attr('aria-hidden', 'true').html(c => '<circle cx="8" cy="8" r="8" fill="currentColor"/>'
            + `<path d="${c.ok ? 'M4.4 8.3l2.4 2.4 4.8-5.2' : 'M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6'}" fill="none" style="stroke: var(--surface)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`);
        result.append('span').text(c => (c.ok ? 'PASS' : 'FAIL'));
        const body = item.append('span');
        body.append('span').text(c => c.t);
        body.append('small').text(c => c.n);
    }

    // start and length in weeks, as the plan estimates them. more: the extra week of a "5 to 6 weeks" estimate.
    const PHASES = [
        { id: '1', track: 'Shared start', name: 'Run both stacks together', start: 0, weeks: 1, state: 'started' },
        { id: '2', track: 'Shared start', name: 'Playground as a consumer, web first', start: 1, weeks: 2, state: 'started', after: '1' },
        { id: '3', track: 'Voice track', name: 'Voice consent profile and channel', start: 3, weeks: 4, state: 'prototype on a mock', after: '2' },
        { id: '4', track: 'Voice track', name: 'Decoupled sign-in on calls', start: 7, weeks: 5, more: 1, after: '3' },
        { id: '6', track: 'Voice track', name: 'Receipts, history and withdrawal by voice', start: 13, weeks: 1, after: '4' },
        { id: '5', track: 'Registers track', name: 'Tabular adapter for the other registers', start: 3, weeks: 2, after: '2' },
        { id: '5b', track: 'Registers track', name: 'A second consumer', start: 3, weeks: 1, after: '2' },
    ];

    /** data-flow="phases": the phases as a week-by-week chart with a voice track and a registers track. */
    function phases(host) {
        const width = 980, left = 316, right = 196, x = d3.scaleLinear([0, 14], [left, width - right]);
        let y = 4, track = null;
        PHASES.forEach(p => {
            if (p.track !== track) { track = p.track; p.head = y + 15; y += 24; } else p.head = null;
            p.y = y + 15;
            y += 30;
        });
        const bottom = y + 6;
        const svg = d3.select(host).append('div').attr('class', 'fl-scroll').append('svg').attr('class', 'fl-gantt').attr('viewBox', `0 0 ${width} ${bottom + 34}`)
            .attr('role', 'group').attr('aria-label', 'Phases by week: a shared start of three weeks, then a voice track to about week 14 and a registers track to about week 5.');
        const weeks = d3.range(0, 15, 2);
        svg.append('g').selectAll('line').data(weeks).join('line').attr('class', 'grid').attr('x1', x).attr('x2', x).attr('y1', 4).attr('y2', bottom);
        svg.append('g').selectAll('text').data(weeks).join('text').attr('class', 'tick').attr('text-anchor', 'middle').attr('x', x).attr('y', bottom + 15).text(d => d);
        svg.append('text').attr('class', 'tick').attr('x', left).attr('y', bottom + 30).text('weeks from the start, as estimated');
        svg.append('g').selectAll('text').data(PHASES.filter(p => p.head)).join('text').attr('class', 'track').attr('x', 12).attr('y', p => p.head + 4).text(p => p.track);
        const at = Object.fromEntries(PHASES.map(p => [p.id, p]));
        svg.append('g').selectAll('path').data(PHASES.filter(p => p.after)).join('path').attr('class', 'dep')
            .attr('d', p => `M${x(p.start)},${at[p.after].y + 8} V${p.y - 8}`);
        const row = svg.append('g').selectAll('g').data(PHASES).join('g');
        row.append('a').attr('href', p => `#phase-${p.id}`).append('text').attr('class', 'name').attr('x', 12).attr('y', p => p.y + 4.5).text(p => `${p.id} · ${p.name}`);
        row.append('rect').attr('class', p => `bar${p.state ? '' : ' todo'}`).attr('x', p => x(p.start)).attr('y', p => p.y - 8)
            .attr('width', p => x(p.start + p.weeks) - x(p.start)).attr('height', 16).attr('rx', 4);
        row.filter(p => p.more).append('rect').attr('class', 'more').attr('x', p => x(p.start + p.weeks) + 2).attr('y', p => p.y - 8)
            .attr('width', p => x(p.more) - x(0) - 2).attr('height', 16).attr('rx', 4);
        const length = p => (p.more ? `${p.weeks} to ${p.weeks + p.more} weeks` : `about ${p.weeks} week${p.weeks > 1 ? 's' : ''}`);
        row.append('text').attr('class', 'tip').attr('x', p => x(p.start + p.weeks + (p.more || 0)) + 8).attr('y', p => p.y + 4)
            .text(p => `${length(p)} · ${p.state || 'not started'}`);
        row.on('pointerenter pointermove', (e, p) => tip(`Phase ${p.id}: ${p.name}\n${length(p)}, ${p.state || 'not started'}`, e.clientX, e.clientY)).on('pointerleave', () => tip(null));
    }

    const VIEWS = { map: overview, player, sequence: staticSequence, matrix, checks, phases };
    hosts.forEach(host => (VIEWS[host.dataset.flow] ? VIEWS[host.dataset.flow](host) : console.error('Unknown data-flow view:', host.dataset.flow)));
})();
