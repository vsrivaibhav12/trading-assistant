"""What the scanner looks at. Edit freely. Tickers that Yahoo cannot find are skipped, not fatal."""
from __future__ import annotations

# base NSE symbol -> lowercase aliases used by the news tagger
EQUITIES = {
    "RELIANCE": ["reliance industries", "reliance"], "TCS": ["tcs", "tata consultancy"],
    "HDFCBANK": ["hdfc bank"], "ICICIBANK": ["icici bank"], "INFY": ["infosys"],
    "HINDUNILVR": ["hindustan unilever", "hul"], "ITC": ["itc"], "SBIN": ["sbi", "state bank of india"],
    "BHARTIARTL": ["bharti airtel", "airtel"], "KOTAKBANK": ["kotak mahindra bank", "kotak bank"],
    "LT": ["larsen", "l&t"], "AXISBANK": ["axis bank"], "ASIANPAINT": ["asian paints"],
    "MARUTI": ["maruti suzuki", "maruti"], "SUNPHARMA": ["sun pharma"], "TITAN": ["titan company", "titan"],
    "BAJFINANCE": ["bajaj finance"], "NESTLEIND": ["nestle india"], "ULTRACEMCO": ["ultratech"],
    "WIPRO": ["wipro"], "HCLTECH": ["hcl tech", "hcltech"], "NTPC": ["ntpc"], "POWERGRID": ["power grid"],
    "ONGC": ["ongc"], "M&M": ["mahindra & mahindra", "m&m"], "TATASTEEL": ["tata steel"],
    "JSWSTEEL": ["jsw steel"], "ADANIPORTS": ["adani ports"], "ADANIENT": ["adani enterprises"],
    "COALINDIA": ["coal india"], "BAJAJFINSV": ["bajaj finserv"], "TECHM": ["tech mahindra"],
    "INDUSINDBK": ["indusind bank"], "HINDALCO": ["hindalco"], "DRREDDY": ["dr reddy"],
    "CIPLA": ["cipla"], "EICHERMOT": ["eicher motors"], "GRASIM": ["grasim"], "BRITANNIA": ["britannia"],
    "APOLLOHOSP": ["apollo hospitals"], "SBILIFE": ["sbi life"], "HDFCLIFE": ["hdfc life"],
    "BPCL": ["bpcl", "bharat petroleum"], "TATACONSUM": ["tata consumer"], "HEROMOTOCO": ["hero motocorp"],
    "DIVISLAB": ["divi's", "divis lab"], "BAJAJ-AUTO": ["bajaj auto"], "SHRIRAMFIN": ["shriram finance"],
    "TRENT": ["trent"], "BEL": ["bharat electronics"], "ETERNAL": ["eternal", "zomato"],
    "JIOFIN": ["jio financial"],
}

# Index / commodity underlyings used for the options mode. step = strike spacing (verify on your broker).
INSTRUMENTS = {
    "NIFTY": dict(name="NIFTY", yf="^NSEI", kind="index", session="NSE", step=50),
    "BANKNIFTY": dict(name="BANKNIFTY", yf="^NSEBANK", kind="index", session="NSE", step=100),
    "CRUDE": dict(name="CRUDEOIL", yf="CL=F", kind="commodity", session="MCX", step=50, fx="INR=X"),
}

TYPING_ALIASES = {"NIFTY50": "NIFTY", "NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "NIFTYBANK": "BANKNIFTY",
                  "CRUDE": "CRUDE", "CRUDEOIL": "CRUDE", "WTI": "CRUDE"}


def equity_inst(base: str) -> dict:
    return dict(name=base, yf=base + ".NS", kind="equity", session="NSE")


def all_equity_insts() -> list[dict]:
    return [equity_inst(b) for b in EQUITIES]


def inst_for(yf_symbol: str) -> dict:
    for i in INSTRUMENTS.values():
        if i["yf"] == yf_symbol:
            return i
    base = yf_symbol[:-3] if yf_symbol.endswith(".NS") else yf_symbol
    return dict(name=base, yf=yf_symbol, kind="equity", session="NSE")


def normalize_symbol(text: str) -> str:
    """'reliance' -> 'RELIANCE.NS', 'nifty' -> '^NSEI', 'crude' -> 'CL=F'. Pass-through for explicit Yahoo tickers."""
    s = text.strip().upper()
    if s in TYPING_ALIASES:
        return INSTRUMENTS[TYPING_ALIASES[s]]["yf"]
    if s.startswith("^") or s.endswith(".NS") or s.endswith("=F") or s.endswith("=X"):
        return s
    return s + ".NS"


def display_name(yf_symbol: str) -> str:
    return inst_for(yf_symbol)["name"]


# Extra liquid names so the monthly momentum list has a broad pool (~150 stocks). Today's survivors, so tests on it
# are flattered (survivorship bias). Tickers Yahoo cannot find are skipped automatically.
ROTATION_EXTRA = """ABB ADANIENSOL ADANIGREEN ADANIPOWER AMBUJACEM BAJAJHLDNG BANKBARODA BERGEPAINT BOSCHLTD CANBK CHOLAFIN COLPAL
DABUR DLF DMART GAIL GODREJCP HAVELLS ICICIGI ICICIPRULI INDHOTEL INDIGO IOC IRCTC IRFC JINDALSTEL LICI LODHA MARICO
MOTHERSON NAUKRI NHPC PIDILITIND PNB POLYCAB RECLTD PFC SHREECEM SIEMENS SRF TATAPOWER TORNTPHARM TVSMOTOR UNITDSPR VBL
VEDL ZYDUSLIFE HINDPETRO MUTHOOTFIN PERSISTENT MPHASIS COFORGE DIXON MAXHEALTH CUMMINSIND ABCAPITAL AUROPHARMA LUPIN ALKEM
OBEROIRLTY PAGEIND BHARATFORG ASHOKLEY TIINDIA IDFCFIRSTB FEDERALBNK AUBANK BANDHANBNK BIOCON CGPOWER CONCOR CROMPTON
GLENMARK GMRAIRPORT HDFCAMC HINDZINC IDEA JSWENERGY JUBLFOOD KPITTECH LAURUSLABS MANKIND MAZDOCK NMDC OFSS PATANJALI
PHOENIXLTD PIIND POLICYBZR PRESTIGE RVNL SAIL SOLARINDS SUPREMEIND SUZLON SYNGENE TATACOMM TATAELXSI TATATECH UPL VOLTAS
YESBANK""".split()


def rotation_symbols() -> list[str]:
    return [b + ".NS" for b in list(EQUITIES) + [x for x in ROTATION_EXTRA if x not in EQUITIES]]
