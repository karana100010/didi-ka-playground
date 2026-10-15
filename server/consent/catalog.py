"""Words the consent service uses: purposes, register and field labels, default templates, the answer vocabulary.

Labels here pre-fill the setup of any source whose tables and columns have these names (the mock
registers do). Operators change them on the connect screen; what is saved in the source's setup wins.
"""

PURPOSES = {
    'income_question': ('आपकी आमदनी के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your income'),
    'savings_question': ('आपकी बचत के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your savings'),
    'loan_question': ('आपके कर्ज़ के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your loans'),
    'enterprise_question': ('आपके धंधे के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your enterprise'),
    'wages_question': ('आपकी मज़दूरी के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your wages'),
    'farm_question': ('आपकी खेती के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your farm'),
    'group_question': ('आपके समूह के बारे में आपके सवाल का जवाब देने के लिए', 'To answer your question about your SHG'),
    'lakhpati_status_check': ('यह जाँचने के लिए कि आप लखपति दीदी हैं या नहीं', 'To check whether you are a Lakhpati Didi'),
    'bank_linkage_readiness': ('यह जाँचने के लिए कि आपका समूह बैंक लोन के लिए तैयार है या नहीं',
                               'To check whether your SHG is ready for a bank loan'),
    'business_plan': ('आपके धंधे का बिज़नेस प्लान बनाने के लिए', 'To make a business plan for your enterprise'),
    'other_question': ('आपके सवाल का जवाब देने के लिए', 'To answer your question'),
}

# table -> (Hindi name, English name)
REGISTERS = {
    'users': ('सदस्य सूची', 'Member list'),
    'shg': ('समूह विवरण', 'SHG profile'),
    'meetings': ('समूह बैठक रजिस्टर', 'SHG meeting register'),
    'savings': ('बचत रजिस्टर', 'Savings register'),
    'loans': ('कर्ज़ रजिस्टर', 'Loan register'),
    'dar': ('डिजिटल आजीविका रजिस्टर', 'Digital Aajeevika Register'),
    'enterprise': ('उद्यम रजिस्टर', 'Enterprise register'),
    'wages_vbg': ('वीबी-जी राम जी मज़दूरी रिकॉर्ड', 'VB–G RAM G wage record'),
    'agristack': ('एग्रीस्टैक किसान रिकॉर्ड', 'AgriStack farmer record'),
}

# column -> (Hindi label, English label, sensitivity)
FIELDS = {
    'name': ('नाम', 'name', 'low'), 'name_local': ('नाम', 'name', 'low'), 'age': ('उम्र', 'age', 'medium'),
    'role': ('समूह में पद', 'role in the SHG', 'low'), 'village': ('गाँव', 'village', 'low'),
    'block': ('ब्लॉक', 'block', 'low'), 'district': ('ज़िला', 'district', 'low'), 'state': ('राज्य', 'state', 'low'),
    'language': ('भाषा', 'language', 'low'), 'asr_language': ('बोली', 'spoken language', 'low'),
    'phone_last4': ('फ़ोन के आख़िरी 4 अंक', 'last 4 digits of phone', 'high'),
    'shg_name': ('समूह का नाम', 'SHG name', 'low'), 'vo_name': ('ग्राम संगठन', 'Village Organisation', 'low'),
    'clf_name': ('संकुल संघ', 'Cluster Level Federation', 'low'), 'formed_on': ('समूह बनने की तारीख', 'date formed', 'low'),
    'members': ('सदस्य संख्या', 'number of members', 'low'), 'bank_branch': ('बैंक शाखा', 'bank branch', 'low'),
    'meeting_day': ('बैठक का दिन', 'meeting day', 'low'), 'grade': ('समूह ग्रेड', 'SHG grade', 'medium'),
    'date': ('बैठक की तारीख', 'meeting date', 'low'), 'held': ('बैठक हुई या नहीं', 'whether the meeting was held', 'low'),
    'attendance': ('हाज़िरी', 'attendance', 'low'), 'savings_collected': ('जमा बचत', 'savings collected', 'medium'),
    'loans_given': ('समूह से दिए गए कर्ज़', 'internal loans given', 'medium'),
    'repayments_due': ('देय किस्तें', 'repayments due', 'medium'), 'repayments_made': ('चुकाई गई किस्तें', 'repayments made', 'medium'),
    'books_updated': ('खाता-बही', 'books of account', 'low'),
    'month': ('महीना', 'month', 'low'), 'amount': ('राशि', 'amount', 'high'), 'cumulative': ('कुल बचत', 'total savings', 'high'),
    'loan_id': ('कर्ज़ नंबर', 'loan number', 'low'), 'source': ('कर्ज़ का स्रोत', 'loan source', 'medium'),
    'purpose': ('कर्ज़ का उद्देश्य', 'loan purpose', 'medium'), 'disbursed_on': ('कर्ज़ मिलने की तारीख', 'date received', 'medium'),
    'rate_pct': ('ब्याज दर', 'interest rate', 'medium'), 'tenure_months': ('अवधि', 'tenure', 'medium'),
    'emi': ('मासिक किस्त', 'monthly instalment', 'high'), 'outstanding': ('बकाया राशि', 'amount outstanding', 'high'),
    'overdue_days': ('देरी के दिन', 'days overdue', 'high'),
    'cycle_no': ('कारोबार-चक्र', 'business cycle', 'low'), 'activity': ('काम का प्रकार', 'activity', 'medium'),
    'income': ('आमदनी', 'income', 'high'), 'expense': ('खर्च', 'expenses', 'high'),
    'units_sold': ('बिक्री की मात्रा', 'quantity sold', 'medium'), 'unit': ('इकाई', 'unit', 'low'),
    'sale_rate': ('बिक्री दर', 'sale price', 'medium'),
    'enterprise_id': ('उद्यम नंबर', 'enterprise number', 'low'), 'type': ('धंधे का प्रकार', 'type of enterprise', 'low'),
    'started_on': ('शुरू होने की तारीख', 'start date', 'low'), 'units': ('इकाइयों की संख्या', 'number of units', 'medium'),
    'unit_label': ('इकाई', 'unit', 'low'), 'monthly_turnover': ('मासिक कारोबार', 'monthly turnover', 'high'),
    'monthly_cost': ('मासिक लागत', 'monthly cost', 'high'), 'assets_value': ('संपत्ति का मूल्य', 'value of assets', 'high'),
    'job_card_no': ('जॉब कार्ड नंबर', 'job card number', 'medium'), 'days_worked': ('काम के दिन', 'days worked', 'medium'),
    'wages_paid': ('मिली मज़दूरी', 'wages paid', 'high'), 'wages_pending': ('बाकी मज़दूरी', 'wages pending', 'high'),
    'farmer_id': ('किसान आईडी', 'farmer id', 'medium'), 'land_acres': ('ज़मीन (एकड़)', 'land (acres)', 'medium'),
    'irrigated': ('सिंचाई', 'irrigation', 'low'), 'crop_kharif': ('ख़रीफ़ फ़सल', 'kharif crop', 'low'),
    'crop_rabi': ('रबी फ़सल', 'rabi crop', 'low'),
}

KEY_COLUMNS = ('didi_id', 'household_id', 'shg_id')
OFFICE_BEARERS = ('president', 'secretary', 'treasurer')

DEFAULT_TEMPLATE = {'hi': '{source_name} से: {fields}।', 'en': 'From your {source_name}: {fields}.'}
INTRO = {'hi': '{purpose}, हमें आपकी यह जानकारी देखनी होगी:', 'en': '{purpose}, we need to look at this information:'}
RETENTION = {'hi': 'यह जानकारी सिर्फ़ इसी सवाल के लिए इस्तेमाल होगी, फिर हटा दी जाएगी।',
             'en': 'It will be used only for this question, then deleted.'}
ASK = {'hi': 'क्या आप अनुमति देती हैं?', 'en': 'Do you give permission?'}

# Default answer vocabulary. Operators add their own words per language (custom words win).
VOCABULARY = {
    'hi': {
        'yes': ['हाँ', 'हां', 'हा', 'जी हाँ', 'जी हां', 'हाँ जी', 'हां जी', 'ठीक है', 'ठीक', 'देख लो', 'देख लीजिए', 'देखिए', 'देख लीजिये',
                'चलेगा', 'बिल्कुल', 'ज़रूर', 'जरूर', 'हाँ देख लो', 'haan', 'han', 'ha', 'theek hai', 'thik hai', 'ok', 'okay', 'yes'],
        'no': ['नहीं', 'नही', 'ना', 'मत देखो', 'मत देखिए', 'रहने दो', 'रहने दीजिए', 'अभी नहीं', 'नहीं चाहिए', 'nahi', 'nahin', 'na', 'no'],
        'why': ['क्यों', 'किसलिए', 'किस लिए', 'पहले बताओ', 'पहले बताइए', 'क्यों चाहिए', 'kyon', 'kyu', 'why'],
    },
    'ta': {'yes': ['ஆமா', 'ஆம்', 'சரி', 'பாருங்கள்', 'yes', 'ok'], 'no': ['வேண்டாம்', 'இல்லை', 'no'], 'why': ['ஏன்', 'why']},
    'en': {'yes': ['yes', 'ok', 'okay', 'sure', 'go ahead'], 'no': ['no', 'not now', "don't"], 'why': ['why']},
}
# Words that make a reply conditional ("yes, but…", "only…"): such replies go to the LLM interpreter.
QUALIFIERS = {'hi': ['पर', 'लेकिन', 'मगर', 'सिर्फ़', 'सिर्फ', 'केवल', 'बस', 'छोड़कर', 'मत देखना', 'मत देखिए', 'sirf', 'lekin', 'par'],
              'ta': ['ஆனால்', 'மட்டும்'], 'en': ['but', 'only', 'except']}
# Pre-filled custom words when the sample registers are loaded (design doc §4.4).
SAMPLE_CUSTOM = {'hi': [{'phrase': 'हओ', 'meaning': 'yes', 'note': 'Bundeli, added for Sagar'},
                        {'phrase': 'हव', 'meaning': 'yes', 'note': 'Chhattisgarhi, added for Bilaspur'},
                        {'phrase': 'बाद में बताएँगे', 'meaning': 'no', 'note': 'offer to ask later'}]}

SCHEME_INFO = {
    'lakhpati_didi': 'Lakhpati Didi (DAY-NRLM): an SHG member whose annual household income is ₹1 lakh or more, sustained for '
                     'at least four agricultural seasons or business cycles, with average monthly income above ₹10,000. '
                     'The national target has been raised to 6 crore.',
    'panchsutra': 'Panchsutra: the five habits NRLM expects of an SHG: regular meetings, regular savings, regular internal '
                  'lending, timely repayment, and up-to-date books of account. Banks look at them before SHG bank linkage.',
    'cif': 'Community Investment Fund (CIF): money from NRLM that Village Organisations and Cluster Level Federations lend '
           'to SHGs for members\' livelihoods.',
    'bank_linkage': 'SHG–bank linkage: banks lend to SHGs, usually after grading them on the Panchsutra; repeat loans grow '
                    'with good repayment.',
    'vbg_ramg': 'The VB–G RAM G Act, 2025 (in force since 1 July 2026) replaced MGNREGA and guarantees wage employment to '
                'rural households. In this simulation, wages_vbg holds days worked and wages paid per household.',
    'dar': 'The Digital Aajeevika Register records a member\'s monthly livelihood income and expenses. In this simulation a '
           'business cycle is six months: cycle 1 is Oct 2024–Mar 2025 and cycle 4 is Apr–Sep 2026.',
}


def default_fields(tables):
    """Pre-filled labels for a source's columns (setup['fields'])."""
    out = {}
    for table, columns in tables.items():
        out[table] = {}
        for column in columns:
            if column in KEY_COLUMNS or column == 'didi_id':
                continue
            hi, en, sensitivity = FIELDS.get(column, (column.replace('_', ' '), column.replace('_', ' '), 'medium'))
            out[table][column] = {'hi': hi, 'en': en, 'sensitivity': sensitivity}
    return out


def default_registers(tables):
    return {t: {'hi': REGISTERS.get(t, (t, t))[0], 'en': REGISTERS.get(t, (t, t))[1]} for t in tables}
