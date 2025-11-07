import dash
from dash import dcc, html
from dash.dependencies import Input, Output, State
import plotly.graph_objs as go
import requests
from datetime import datetime, timedelta
import pytz
from collections import defaultdict

# Constants for IP and port (STH)
IP_ADDRESS = "46.17.108.113"
PORT_STH = 8666
DASH_HOST = "0.0.0.0"  # set to 0.0.0.0 to access externally

# Set lastN value (how many points to request from STH each interval)
lastN = 100

def get_response_data(lastN):
    """
    Fetch lastN values of the attribute 'resultado' for entities of type 'Resposta'.
    Returns list of dicts { 'value': 'Correto'|'Incorreto', 'recvTime': '<timestamp>' }
    """
    url = f"http://{IP_ADDRESS}:{PORT_STH}/STH/v1/contextEntities/type/Resposta/attributes/resultado?lastN={lastN}"
    headers = {
        'fiware-service': 'smart',
        'fiware-servicepath': '/'
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        print(f"Error requesting STH: {e}")
        return []
    if r.status_code != 200:
        print(f"Error accessing {url}: {r.status_code} - {r.text}")
        return []

    data = r.json()
    try:
        values = data['contextResponses'][0]['contextElement']['attributes'][0]['values']
    except Exception as e:
        print(f"Key error parsing STH response: {e}")
        return []

    # STH 'values' items are often arrays like: [{ "attrValue": "Correto", "recvTime": "2025-..." }, ...]
    normalized = []
    for entry in values:
        # sometimes STH may return as [value, recvTime] or object; handle both
        if isinstance(entry, dict):
            val = entry.get('attrValue') or entry.get('value') or entry.get('attrValue')
            recv = entry.get('recvTime') or entry.get('recvtime') or entry.get('time')
            if val is None or recv is None:
                # try older format: [value, recvTime]
                continue
            normalized.append({'value': val, 'recvTime': recv})
        elif isinstance(entry, list) and len(entry) >= 2:
            normalized.append({'value': entry[0], 'recvTime': entry[1]})
    return normalized

def convert_to_lisbon_time_str(timestr):
    """
    Convert ISO UTC string to timezone-aware datetime in Lisbon.
    Returns a datetime object.
    """
    utc = pytz.utc
    lisbon = pytz.timezone('Europe/Lisbon')
    # Normalize common formats
    t = timestr.replace('T', ' ').replace('Z', '')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(t, fmt)
            dt = utc.localize(dt).astimezone(lisbon)
            return dt
        except ValueError:
            continue
    # fallback: parse until seconds
    try:
        dt = datetime.fromisoformat(timestr.replace('Z', '+00:00'))
        return dt.astimezone(pytz.timezone('Europe/Lisbon'))
    except Exception:
        return None

def floor_to_minute(dt):
    return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, tzinfo=dt.tzinfo)

app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1('Quiz — Acertos vs Erros (por minuto)'),
    dcc.Graph(id='result-graph'),
    # store aggregated series
    dcc.Store(id='result-store', data={'timestamps': [], 'correct': [], 'incorrect': []}),
    dcc.Interval(id='interval-component', interval=10*1000, n_intervals=0)  # 10s
])

@app.callback(
    Output('result-store', 'data'),
    Input('interval-component', 'n_intervals'),
    State('result-store', 'data')
)
def update_store(n, stored_data):
    raw = get_response_data(lastN)
    if not raw:
        return stored_data

    # Aggregate counts per minute
    counts = defaultdict(lambda: {'correct': 0, 'incorrect': 0})
    for entry in raw:
        val = str(entry['value']).strip()
        recv = entry['recvTime']
        dt = convert_to_lisbon_time_str(recv)
        if not dt:
            continue
        minute = floor_to_minute(dt)
        if val.lower().startswith('c'):  # Correto / Correct
            counts[minute]['correct'] += 1
        else:
            counts[minute]['incorrect'] += 1

    # Merge aggregated counts into stored_data (keep chronological order)
    # Convert existing stored_data timestamps back to datetime objects (with tzinfo)
    existing = {}
    for i, ts in enumerate(stored_data.get('timestamps', [])):
        try:
            dt = datetime.fromisoformat(ts)
            existing[dt] = {'correct': stored_data['correct'][i], 'incorrect': stored_data['incorrect'][i]}
        except Exception:
            continue

    # Update/merge
    for minute_dt, cnt in counts.items():
        if minute_dt in existing:
            existing[minute_dt]['correct'] = existing[minute_dt]['correct'] + cnt['correct']
            existing[minute_dt]['incorrect'] = existing[minute_dt]['incorrect'] + cnt['incorrect']
        else:
            existing[minute_dt] = {'correct': cnt['correct'], 'incorrect': cnt['incorrect']}

    # Keep only last 120 minutes to avoid infinite growth
    sorted_minutes = sorted(existing.keys())
    if len(sorted_minutes) > 120:
        sorted_minutes = sorted_minutes[-120:]

    out_ts = [dt.isoformat() for dt in sorted_minutes]
    out_correct = [existing[dt]['correct'] for dt in sorted_minutes]
    out_incorrect = [existing[dt]['incorrect'] for dt in sorted_minutes]

    return {'timestamps': out_ts, 'correct': out_correct, 'incorrect': out_incorrect}

@app.callback(
    Output('result-graph', 'figure'),
    Input('result-store', 'data')
)
def update_graph(store):
    if not store or not store.get('timestamps'):
        return go.Figure()

    x = [datetime.fromisoformat(ts) for ts in store['timestamps']]
    trace_correct = go.Bar(
        x=x,
        y=store['correct'],
        name='Correto',
        marker=dict(color='green')
    )
    trace_incorrect = go.Bar(
        x=x,
        y=store['incorrect'],
        name='Incorreto',
        marker=dict(color='red')
    )

    fig = go.Figure(data=[trace_correct, trace_incorrect])
    fig.update_layout(title='Acertos e Erros por Minuto (Lisbon Time)',
                      xaxis_title='Minuto',
                      yaxis_title='Contagem',
                      barmode='group')
    return fig

if __name__ == '__main__':
    app.run_server(debug=True, host=DASH_HOST, port=8050)
