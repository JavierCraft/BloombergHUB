"""Evidence-only team comparison; historical win rate is not a calibrated forecast."""
from datetime import datetime, timezone
import concurrent.futures
import re


def compare(game, team_a, team_b):
    from src.sports.esports import Dota2
    result = {'game': game, 'team_a': team_a, 'team_b': team_b, 'teams': [],
              'favorite': None, 'forecast_probability': None,
              'note': 'Belum ada model peluang menang yang terkalibrasi untuk pertandingan ini.'}
    if game != 'dota2':
        result['note'] = 'Sumber riwayat tim untuk game ini belum tersambung. Favorit historis dan peluang menang tidak dapat dihitung; periksa odds Polymarket jika pasarnya tersedia.'
        return result
    source = Dota2()
    normalize = lambda s: re.sub(r'\s+', ' ', str(s).strip().casefold())
    teams = source.teams()
    found = []
    for name in (team_a, team_b):
        matches = [t for t in teams if normalize(t.get('name')) == normalize(name)]
        if len(matches) != 1:
            result['note'] = 'Identitas kedua tim belum cocok secara unik dengan OpenDota; tidak menebak riwayat tim atau favorit.'
            return result
        found.append(matches[0])
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - 90*86400
    def form(team):
        history = [m for m in source.team_matches(team['team_id'])
                   if cutoff <= (m.get('start_time') or 0) <= now
                   and isinstance(m.get('radiant_win'), bool) and isinstance(m.get('radiant'), bool)]
        history.sort(key=lambda m: m['start_time'], reverse=True)
        history = history[:20]
        wins = sum(m['radiant_win'] == m['radiant'] for m in history)
        return {'team': team['name'], 'team_id': team['team_id'], 'sample': len(history),
                'wins': wins, 'losses': len(history)-wins,
                'win_rate_pct': round(wins/len(history)*100, 1) if history else None,
                'rating': team.get('rating'),
                'matches': [{'match_id': m['match_id'],
                             'time_utc': datetime.fromtimestamp(m['start_time'], timezone.utc).isoformat(),
                             'opponent': m.get('opposing_team_name'),
                             'won': m['radiant_win'] == m['radiant'],
                             'url': f"https://www.opendota.com/matches/{m['match_id']}"} for m in history]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        result['teams'] = list(pool.map(form, found))
    a, b = result['teams']
    if min(a['sample'], b['sample']) >= 5 and a['win_rate_pct'] != b['win_rate_pct']:
        result['favorite'] = max((a,b), key=lambda t:t['win_rate_pct'])['team']
    result['note'] = 'Indikasi form memakai maksimal 20 game terakhir dalam 90 hari dari respons OpenDota; minimal 5 game per tim. Win rate historis bukan peluang menang seri Bo3/Bo5. Kekuatan lawan, roster, draft, dan patch belum dikoreksi.'
    result['fetched_at'] = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# Batch form lean for a whole schedule
# ---------------------------------------------------------------------------

# Elo's standard scale: a 400-point gap implies a 10:1 expectation. OpenDota
# publishes its team ratings on this scale, so the conversion is the published
# formula, not a fitted one.
ELO_SCALE = 400.0

# Below this the rating is dominated by a handful of games and says little.
MIN_GAMES = 20

# Name-match bar for tying a schedule row to an OpenDota team. Higher than the
# market matcher's, because a wrong join here invents a favorite rather than
# just attaching the wrong odds next to visible team names.
NAME_MATCH_MIN = 0.8


def _elo_expectation(rating_a, rating_b):
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / ELO_SCALE))


def form_lean(game, fixtures, team_a_key='team_a', team_b_key='team_b'):
    """Annotate a whole schedule with a ratings-based lean, in one API call.

    `compare()` above answers one fixture properly, with real match history; it
    costs three OpenDota calls per fixture, which a thirty-row schedule cannot
    afford. This is the cheap companion: one `teams()` call, then an Elo
    expectation from the ratings OpenDota already publishes.

    It is a weaker claim than `compare()` and is labelled as such. Rating gaps
    ignore roster changes, patch, draft and the Bo3/Bo5 format — a per-game
    expectation is not a series probability. Teams OpenDota does not know, or
    knows only from a handful of games, are left blank rather than guessed.
    """
    rows = list(fixtures)
    if game != 'dota2':
        for row in rows:
            row['form'] = {'known': False,
                           'reason': 'Sumber rating tim untuk game ini belum tersambung, '
                                     'jadi tidak ada indikasi form yang bisa dihitung.'}
        return rows

    from src.sports.esports import Dota2

    try:
        catalog = Dota2().teams()
    except Exception as exc:
        reason = getattr(exc, 'message', None) or str(exc)[:140]
        for row in rows:
            row['form'] = {'known': False, 'reason': f'OpenDota tidak menjawab: {reason}'}
        return rows

    # Liquipedia and OpenDota spell the same org differently often enough that
    # exact matching found one fixture in twenty. The token matcher built for
    # market linking handles the same problem, so it is reused here — with a
    # higher bar, because a wrong team here produces a confident wrong favorite.
    from src.markets.fixtures import _similarity

    known_teams = [t for t in catalog
                   if t.get('name') and isinstance(t.get('rating'), (int, float))
                   and ((t.get('wins') or 0) + (t.get('losses') or 0)) >= MIN_GAMES]

    flat = lambda s: re.sub(r'\s+', ' ', str(s or '').strip().casefold())

    def _describe(team, score):
        played = (team.get('wins') or 0) + (team.get('losses') or 0)
        return {'name': team.get('name'), 'rating': round(team['rating'], 1),
                'wins': team.get('wins'), 'losses': team.get('losses'), 'played': played,
                'match_confidence': round(score, 2),
                'win_rate_pct': round((team.get('wins') or 0) / played * 100, 1)}

    def lookup(name):
        if not str(name or '').strip():
            return None

        # Exact name first. Token overlap is measured over the shorter name and
        # `US` is a filler word, so `Natus Vincere` and `Natus Vincere US` are
        # indistinguishable to the fuzzy scorer — it abstained on one of the
        # best-known teams in the game. The literal name breaks that tie.
        exact = [t for t in known_teams if flat(t['name']) == flat(name)]
        if len(exact) == 1:
            return _describe(exact[0], 1.0)
        if len(exact) > 1:
            return None

        scored = sorted(((_similarity(name, t['name']), t) for t in known_teams),
                        key=lambda row: row[0], reverse=True)
        if not scored or scored[0][0] < NAME_MATCH_MIN:
            return None
        # Still ambiguous: abstain rather than pick whichever row sorted first.
        if len(scored) > 1 and scored[1][0] >= scored[0][0]:
            return None
        return _describe(scored[0][1], scored[0][0])

    for row in rows:
        left, right = lookup(row.get(team_a_key)), lookup(row.get(team_b_key))
        if not left or not right:
            missing = [n for n, t in ((row.get(team_a_key), left), (row.get(team_b_key), right))
                       if not t]
            row['form'] = {
                'known': False,
                'reason': ('Tidak ada rating OpenDota yang cocok unik dan cukup matang untuk: '
                           + ', '.join(str(m) for m in missing)
                           + f'. Minimal {MIN_GAMES} pertandingan.'),
            }
            continue
        edge = _elo_expectation(left['rating'], right['rating'])
        favorite = left if edge >= .5 else right
        row['form'] = {
            'known': True,
            'favorite': favorite['name'],
            'favorite_pct': round(max(edge, 1 - edge) * 100, 1),
            'rating_gap': round(abs(left['rating'] - right['rating']), 1),
            'teams': [left, right],
            'method': 'Elo dari rating OpenDota',
            'caveat': ('Ini harapan per pertandingan dari selisih rating, bukan peluang '
                       'memenangkan seri Bo3/Bo5, dan belum mengoreksi roster, patch, '
                       'draft, maupun kualitas lawan terakhir.'),
        }
    return rows
