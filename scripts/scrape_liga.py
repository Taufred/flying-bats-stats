"""Scraped die BSV-NRW-Liga-Daten und schreibt sie direkt zwischen die
LEAGUES-AUTO-START / LEAGUES-AUTO-END Marker in index.html.

Aufruf: python scripts/scrape_liga.py
"""
import re
import sys
import json
import datetime
from urllib.request import urlopen, Request
from html.parser import HTMLParser

LEAGUES = {
    'bb': {
        'name': 'Bezirksliga Herren',
        'icon': '⚾',
        'season': 2026,
        'our_team': 'Duisburg Flying Bats e.V.',
        'url': 'https://www.bsvnrw.de/ligadetails/?bsm_league=6370&bsm_season=2026',
    },
    'sb': {
        'name': 'Mixed Softball Liga',
        'icon': '🥎',
        'season': 2026,
        'our_team': 'Duisburg Flying Bats e.V. 2',
        'url': 'https://www.bsvnrw.de/ligadetails/?bsm_league=6258&bsm_season=2026',
    },
}

UA = 'Mozilla/5.0 (compatible; flying-bats-scraper/1.0)'


def fetch(url):
    req = Request(url, headers={'User-Agent': UA})
    with urlopen(req, timeout=30) as r:
        raw = r.read()
    # BSM serves utf-8
    return raw.decode('utf-8', errors='replace')


class TableExtractor(HTMLParser):
    """Extrahiert alle <table>-Inhalte als Liste von Reihen mit Zellen-Texten."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []        # list of list[list[str]]
        self.current_table = None
        self.current_row = None
        self.current_cell = None
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.current_table = []
        elif tag == 'tr' and self.current_table is not None:
            self.current_row = []
        elif tag in ('td', 'th') and self.current_row is not None:
            self.current_cell = []
            self.in_cell = True

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_cell:
            text = ''.join(self.current_cell).strip()
            text = re.sub(r'\s+', ' ', text)
            self.current_row.append(text)
            self.in_cell = False
            self.current_cell = None
        elif tag == 'tr' and self.current_row is not None:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == 'table' and self.current_table is not None:
            self.tables.append(self.current_table)
            self.current_table = None


def parse_pct(value):
    value = value.strip().replace(',', '.')
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def parse_int(value, default=0):
    m = re.search(r'-?\d+', value or '')
    return int(m.group()) if m else default


def parse_date(text):
    # Beispiele: "19.04.26", "19.04.2026", "19. Apr 2026" etc. Wir wollen ISO YYYY-MM-DD
    text = text.strip()
    m = re.search(r'(\d{1,2})[.\s/-](\d{1,2})[.\s/-](\d{2,4})', text)
    if not m:
        return ''
    d, mo, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = '20' + y
    try:
        dt = datetime.date(int(y), int(mo), int(d))
        return dt.isoformat()
    except ValueError:
        return ''


def parse_time(text):
    text = (text or '').strip()
    m = re.search(r'(\d{1,2})[:.](\d{2})', text)
    if not m:
        return '—'
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_streak(text):
    return text.strip() or '—'


def find_standings_table(tables):
    """Suche eine Tabelle deren Header Pos / Team / W / L / PCT enthält."""
    for table in tables:
        if not table or len(table) < 2:
            continue
        headers = ' '.join(table[0]).lower()
        if 'team' in headers and ('w' in headers or 'wins' in headers) and ('pct' in headers or 'win' in headers):
            return table
    return None


def find_schedule_table(tables):
    """Suche eine Tabelle deren Header Datum + Heim + Gast enthält."""
    for table in tables:
        if not table or len(table) < 2:
            continue
        headers = ' '.join(table[0]).lower()
        if ('heim' in headers or 'home' in headers) and ('gast' in headers or 'away' in headers):
            return table
    return None


def header_index(headers, *needles):
    headers_lower = [h.lower() for h in headers]
    for n in needles:
        for i, h in enumerate(headers_lower):
            if n in h:
                return i
    return -1


def extract_standings(table):
    headers = table[0]
    pos_i = header_index(headers, '#', 'pos', 'platz', 'rang')
    team_i = header_index(headers, 'team', 'mannschaft')
    w_i = header_index(headers, 'w', 'wins', 'siege')
    l_i = header_index(headers, 'l', 'loss', 'niederl')
    pct_i = header_index(headers, 'pct', 'win%', 'quote')
    streak_i = header_index(headers, 'streak', 'serie')
    out = []
    for row in table[1:]:
        if len(row) < 4:
            continue
        team_name = row[team_i] if team_i >= 0 and team_i < len(row) else row[1]
        if not team_name:
            continue
        out.append({
            'pos': parse_int(row[pos_i] if pos_i >= 0 else str(len(out) + 1), len(out) + 1),
            'team': team_name,
            'w': parse_int(row[w_i] if w_i >= 0 else '0'),
            'l': parse_int(row[l_i] if l_i >= 0 else '0'),
            'pct': parse_pct(row[pct_i] if pct_i >= 0 else '0'),
            'streak': parse_streak(row[streak_i] if streak_i >= 0 else '—'),
        })
    return out


def extract_schedule(table, our_team):
    headers = table[0]
    date_i = header_index(headers, 'datum', 'date')
    time_i = header_index(headers, 'uhrzeit', 'time', 'beginn')
    home_i = header_index(headers, 'heim', 'home')
    away_i = header_index(headers, 'gast', 'away')
    result_i = header_index(headers, 'erg', 'result')
    today = datetime.date.today().isoformat()
    out = []
    for row in table[1:]:
        if len(row) < max(home_i, away_i) + 1:
            continue
        date = parse_date(row[date_i] if date_i >= 0 else '')
        if not date:
            continue
        home = row[home_i] if home_i >= 0 else ''
        away = row[away_i] if away_i >= 0 else ''
        if our_team not in home and our_team not in away:
            continue
        if date < today:
            continue
        result = row[result_i] if (result_i >= 0 and result_i < len(row)) else ''
        out.append({
            'date': date,
            'time': parse_time(row[time_i] if time_i >= 0 else ''),
            'home': home,
            'away': away,
            'result': result if result.strip() else None,
        })
    out.sort(key=lambda g: (g['date'], g['time']))
    return out[:10]


def scrape_league(meta):
    html = fetch(meta['url'])
    parser = TableExtractor()
    parser.feed(html)
    standings_t = find_standings_table(parser.tables)
    schedule_t = find_schedule_table(parser.tables)
    if not standings_t:
        raise RuntimeError(f"Keine Standings-Tabelle gefunden für {meta['url']}")
    if not schedule_t:
        print(f"!! Keine Schedule-Tabelle gefunden für {meta['url']} (lasse leer)")
    return {
        'name': meta['name'],
        'icon': meta['icon'],
        'season': meta['season'],
        'our_team': meta['our_team'],
        'source_url': meta['url'],
        'standings': extract_standings(standings_t),
        'schedule': extract_schedule(schedule_t, meta['our_team']) if schedule_t else [],
    }


def js_str(s):
    return json.dumps(s, ensure_ascii=False)


def render_leagues_block(bb, sb):
    def fmt_team(t):
        return f"      {{pos:{t['pos']}, team:{js_str(t['team'])}, w:{t['w']}, l:{t['l']}, pct:{t['pct']:.3f}, streak:{js_str(t['streak'])}}}"

    def fmt_game(g):
        result = js_str(g['result']) if g['result'] else 'null'
        return f"      {{date:{js_str(g['date'])}, time:{js_str(g['time'])}, home:{js_str(g['home'])}, away:{js_str(g['away'])}, result:{result}}}"

    def render_one(key, data, our_team_var):
        lines = []
        lines.append(f"  {key}: {{")
        lines.append(f"    name: {js_str(data['name'])}, icon:{js_str(data['icon'])}, season: {data['season']}, our_team: {our_team_var},")
        lines.append(f"    source_url: {js_str(data['source_url'])},")
        lines.append("    standings: [")
        lines.append(',\n'.join(fmt_team(t) for t in data['standings']))
        lines.append("    ],")
        lines.append("    schedule: [")
        lines.append(',\n'.join(fmt_game(g) for g in data['schedule']))
        lines.append("    ]")
        lines.append("  }")
        return '\n'.join(lines)

    body = render_one('bb', bb, 'OUR_TEAM') + ',\n' + render_one('sb', sb, 'OUR_TEAM_SB')
    return f"var LEAGUES = {{\n{body}\n}};"


def main():
    print('Scrape Bezirksliga (Baseball)...')
    bb = scrape_league(LEAGUES['bb'])
    print(f"  {len(bb['standings'])} Teams · {len(bb['schedule'])} kommende Spiele")
    print('Scrape Mixed Softball (Softball)...')
    sb = scrape_league(LEAGUES['sb'])
    print(f"  {len(sb['standings'])} Teams · {len(sb['schedule'])} kommende Spiele")

    new_block = render_leagues_block(bb, sb)

    # In index.html zwischen den Markern einsetzen
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.normpath(os.path.join(here, '..', 'index.html'))
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    pattern = re.compile(
        r'(// LEAGUES-AUTO-START[^\n]*\n).*?(\n// LEAGUES-AUTO-END)',
        re.DOTALL,
    )
    if not pattern.search(html):
        print('!! LEAGUES-AUTO-START/END Marker nicht gefunden — abgebrochen.')
        sys.exit(1)

    new_html = pattern.sub(lambda m: m.group(1) + new_block + m.group(2), html)

    if new_html == html:
        print('Keine Aenderungen.')
        return

    with open(html_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_html)
    print(f'index.html aktualisiert: {html_path}')


if __name__ == '__main__':
    main()
