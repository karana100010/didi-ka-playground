"""Business-plan templates: which records a plan reads, its assumptions, and its formulas.

Each formula is written once, in a syntax both Excel and the playground's calculator understand (names,
+ - * /, max, min, round), so the workbook keeps live formulas and the spoken summary uses exactly the same
arithmetic. Every assumption says where its value came from: her register, a mock JEEViKA unit cost, or
her own words through the agent.
"""
import csv
from pathlib import Path

UNIT_COSTS_FILE = Path(__file__).resolve().parents[2] / 'data' / 'reference' / 'jeevika_unit_costs.csv'
RECENT = ('2026-04', '2026-09')  # "recent" register months used for averages: Apr–Sep 2026


def unit_costs():
    with UNIT_COSTS_FILE.open(encoding='utf-8') as f:
        return {(r['enterprise'], r['item']): {**r, 'cost_inr': _number(r['cost_inr'])} for r in csv.DictReader(f)}


def _number(text):
    value = float(text)
    return int(value) if value.is_integer() else value


def cost(enterprise, item):
    row = unit_costs()[(enterprise, item)]
    return row['cost_inr'], f'Mock JEEViKA unit cost: {item}, {row["unit"]} ({row["source"]})'


COMMON_NEEDS = [{'table': 'dar', 'fields': ['month', 'activity', 'income', 'expense', 'units_sold', 'sale_rate']},
                {'table': 'enterprise', 'fields': ['type', 'units', 'unit_label', 'monthly_turnover', 'monthly_cost']},
                {'table': 'savings', 'fields': ['month', 'cumulative']}]

# Assumptions every plan has, after the template's own.
FINANCE = [
    ('own_contribution', 'अपनी बचत से', 'from her own savings', '₹', 0, 1000000),
    ('interest_rate', 'ब्याज दर (सालाना)', 'interest rate (per year)', '%', 0, 36),
    ('tenure_months', 'कर्ज़ की अवधि', 'loan tenure', 'months', 3, 84),
    ('household_income_now', 'अभी की सालाना पारिवारिक आमदनी', 'household net income now (last 12 months)', '₹', 0, 10000000),
]

TEMPLATES = {
    'dairy': {
        'name': {'hi': 'डेयरी', 'en': 'Dairy'},
        'unit': {'hi': 'गाय', 'en': 'cows'},
        'assumptions': [
            ('cows_now', 'अभी गायें', 'cows now', 'cows', 0, 50),
            ('cows_to_add', 'नई गायें', 'cows to add', 'cows', 0, 20),
            ('milk_per_cow', 'हर गाय का रोज़ का दूध', 'milk per cow per day', 'litres', 1, 30),
            ('milk_price', 'दूध का भाव', 'milk price', '₹ per litre', 10, 120),
            ('feed_per_cow', 'हर गाय का रोज़ का चारा', 'feed per cow per day', '₹', 20, 600),
            ('vet_per_cow', 'हर गाय का इलाज और बीमा', 'vet care and insurance per cow', '₹ per month', 0, 3000),
            ('cow_price', 'एक गाय की कीमत', 'price of one cow', '₹', 10000, 200000),
        ],
        'formulas': {
            'revenue_now': 'cows_now*milk_per_cow*30*milk_price',
            'cost_now': 'cows_now*(feed_per_cow*30+vet_per_cow)',
            'revenue_after': '(cows_now+cows_to_add)*milk_per_cow*30*milk_price',
            'cost_after': '(cows_now+cows_to_add)*(feed_per_cow*30+vet_per_cow)',
            'investment': 'cow_price*cows_to_add',
        },
        'volume': 'milk_per_cow', 'price': 'milk_price',
    },
    'kirana': {
        'name': {'hi': 'किराना दुकान', 'en': 'Kirana shop'},
        'unit': {'hi': 'दुकान', 'en': 'shop'},
        'assumptions': [
            ('monthly_sales', 'हर महीने की बिक्री', 'monthly sales', '₹', 1000, 1000000),
            ('cost_share', 'बिक्री में माल और खर्च का हिस्सा', 'cost of goods and running costs, share of sales', '%', 10, 98),
            ('sales_increase', 'नए माल से बिक्री की बढ़त', 'sales increase from the new stock', '%', 0, 200),
            ('extra_running_cost', 'नया हर महीने का खर्च', 'extra rent and electricity', '₹ per month', 0, 50000),
            ('stock_investment', 'नए माल पर खर्च', 'investment in new stock', '₹', 1000, 1000000),
        ],
        'formulas': {
            'revenue_now': 'monthly_sales',
            'cost_now': 'monthly_sales*cost_share/100',
            'revenue_after': 'monthly_sales*(1+sales_increase/100)',
            'cost_after': 'monthly_sales*(1+sales_increase/100)*cost_share/100+extra_running_cost',
            'investment': 'stock_investment',
        },
        'volume': 'monthly_sales', 'price': None,
    },
    'tailoring': {
        'name': {'hi': 'सिलाई', 'en': 'Tailoring'},
        'unit': {'hi': 'मशीन', 'en': 'machines'},
        'assumptions': [
            ('machines_now', 'अभी मशीनें', 'machines now', 'machines', 0, 50),
            ('machines_to_add', 'नई मशीनें', 'machines to add', 'machines', 0, 20),
            ('garments_per_machine', 'हर मशीन से महीने के कपड़े', 'garments per machine per month', 'garments', 1, 300),
            ('price_per_garment', 'सिलाई का दाम', 'stitching charge', '₹ per garment', 20, 3000),
            ('material_per_garment', 'धागा और कपड़ा', 'fabric and thread', '₹ per garment', 0, 2000),
            ('electricity_per_machine', 'बिजली', 'electricity per machine', '₹ per month', 0, 5000),
            ('machine_price', 'एक मशीन की कीमत', 'price of one machine', '₹', 1000, 200000),
        ],
        'formulas': {
            'revenue_now': 'machines_now*garments_per_machine*price_per_garment',
            'cost_now': 'machines_now*(garments_per_machine*material_per_garment+electricity_per_machine)',
            'revenue_after': '(machines_now+machines_to_add)*garments_per_machine*price_per_garment',
            'cost_after': '(machines_now+machines_to_add)*(garments_per_machine*material_per_garment+electricity_per_machine)',
            'investment': 'machine_price*machines_to_add',
        },
        'volume': 'garments_per_machine', 'price': 'price_per_garment',
    },
}

# Derived results, in order. Each may use assumptions and earlier results.
RESULTS = [
    ('profit_now', 'revenue_now-cost_now'),
    ('profit_after', 'revenue_after-cost_after'),
    ('extra_profit', 'profit_after-profit_now'),
    ('loan_amount', 'max(0, investment-own_contribution)'),
    ('emi', None),  # PMT, computed specially (Excel: PMT(rate/12, n, -loan))
    ('extra_after_emi', 'extra_profit-emi'),
    ('payback_months', None),  # ROUNDUP(investment / extra_profit)
    ('annual_income_after', 'household_income_now+extra_after_emi*12'),
    ('monthly_income_after', 'annual_income_after/12'),
]


def needs(enterprise):
    if enterprise not in TEMPLATES:
        raise KeyError(enterprise)
    return COMMON_NEEDS


def _recent(rows, activity_words):
    return [r for r in rows if RECENT[0] <= (r.get('month') or '') <= RECENT[1]
            and any(w in (r.get('activity') or '').lower() for w in activity_words)]


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def derive(enterprise, rows):
    """Assumption values with their sources, from her rows {table: rows} and the mock unit costs."""
    dar, ent, savings = rows.get('dar') or [], (rows.get('enterprise') or [{}])[0], rows.get('savings') or []
    out = {}

    def put(key, value, source):
        out[key] = {'value': round(value, 2) if isinstance(value, float) else value, 'source': source}

    if enterprise == 'dairy':
        cows = int(ent.get('units') or 0) if ent.get('type') == 'dairy' else 0
        put('cows_now', cows, 'Enterprise register' if cows else 'No dairy in her enterprise register')
        put('cows_to_add', 1, 'Template default (the agent can change it from her question)')
        recent = _recent(dar, ('dairy',))
        litres = _avg([r.get('units_sold') for r in recent])
        if litres and cows:
            put('milk_per_cow', round(litres / cows / 30), 'Digital Aajeevika Register, Apr–Sep 2026 average')
        else:
            put('milk_per_cow', 8, 'Template default (no dairy sales in her register)')
        price = next((r.get('sale_rate') for r in reversed(recent) if r.get('sale_rate')), None)
        if price:
            put('milk_price', price, 'Digital Aajeevika Register, latest sale price')
        else:
            put('milk_price', *cost('dairy', 'milk price'))
        vet, vet_source = cost('dairy', 'vet care and insurance')
        expense = _avg([r.get('expense') for r in recent])
        if expense and cows:
            put('feed_per_cow', round((expense - vet * cows) / cows / 30), 'Digital Aajeevika Register, Apr–Sep 2026 average, '
                                                                          'less the mock vet cost')
        else:
            put('feed_per_cow', *cost('dairy', 'feed and fodder'))
        put('vet_per_cow', vet, vet_source)
        put('cow_price', *cost('dairy', 'cow (crossbred)'))
    elif enterprise == 'kirana':
        recent = _recent(dar, ('kirana', 'shop'))
        sales, expense = _avg([r.get('income') for r in recent]), _avg([r.get('expense') for r in recent])
        put('monthly_sales', round(sales) if sales else 20000,
            'Digital Aajeevika Register, Apr–Sep 2026 average' if sales else 'Template default (no kirana sales in her register)')
        put('cost_share', round(100 * expense / sales) if sales and expense else 75,
            'Digital Aajeevika Register, expenses as a share of sales' if sales and expense else 'Template default')
        put('sales_increase', 20, 'Template default (the agent can change it from her question)')
        put('extra_running_cost', *cost('kirana', 'extra rent and electricity'))
        put('stock_investment', *cost('kirana', 'additional stock'))
    elif enterprise == 'tailoring':
        machines = int(ent.get('units') or 0) if ent.get('type') == 'tailoring' else 0
        put('machines_now', machines, 'Enterprise register' if machines else 'No tailoring in her enterprise register')
        put('machines_to_add', 1, 'Template default (the agent can change it from her question)')
        recent = _recent(dar, ('tailor',))
        garments = _avg([r.get('units_sold') for r in recent])
        put('garments_per_machine', round(garments / machines) if garments and machines else 20,
            'Digital Aajeevika Register, Apr–Sep 2026 average' if garments and machines else 'Template default')
        price = next((r.get('sale_rate') for r in reversed(recent) if r.get('sale_rate')), None)
        if price:
            put('price_per_garment', price, 'Digital Aajeevika Register, latest stitching charge')
        else:
            put('price_per_garment', *cost('tailoring', 'stitching charge'))
        put('material_per_garment', *cost('tailoring', 'fabric and thread'))
        put('electricity_per_machine', *cost('tailoring', 'electricity'))
        put('machine_price', *cost('tailoring', 'sewing machine'))
    savings_now = max((r.get('cumulative') or 0 for r in savings), default=0)
    put('own_contribution', min(10000, savings_now), 'Up to ₹10,000 from her savings register (template rule)' if savings_now
        else 'No savings in her register')
    put('interest_rate', *cost('loan', 'interest rate'))
    put('tenure_months', *cost('loan', 'tenure'))
    months = {}
    for r in dar:
        months[r.get('month')] = months.get(r.get('month'), 0) + (r.get('income') or 0) - (r.get('expense') or 0)
    recent12 = sorted(m for m in months if m)[-12:]
    put('household_income_now', sum(months[m] for m in recent12), 'Digital Aajeevika Register, last 12 months net income')
    return out


def assumption_specs(enterprise):
    return TEMPLATES[enterprise]['assumptions'] + FINANCE
