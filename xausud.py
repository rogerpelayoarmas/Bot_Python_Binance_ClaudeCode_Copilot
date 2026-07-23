"""
Trading Bot Interface — BTCUSDT Real-Time Chart + PanedWindow
=============================================================
Dependencias:
    pip install requests pandas matplotlib

Novedades v3:
  • PanedWindow horizontal principal → arrastra el separador entre
    panel de órdenes | gráfico | panel derecho
  • PanedWindow vertical dentro del panel derecho → arrastra entre
    sección de cuenta/stats y lista de usuarios
  • Gráfico Matplotlib redibuja al redimensionar cualquier panel
  • Hilo de fondo refresca BTCUSDT cada 5 s sin bloquear la UI
"""

import logging
import json
import tkinter as tk
import threading
import time
import queue
from pathlib import Path
from datetime import datetime

import requests
import pandas as pd
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Optional Pillow for image resizing (used for avatar)
try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

# MongoDB local (opcional)
try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_SOURCE_MESSAGE = "Datos cargados desde demo"

# ── Binance ────────────────────────────────────────────────────
BINANCE_KLINES      = "https://api.binance.com/api/v3/klines"
BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
SYMBOL              = "BTCUSDT"
REFRESH_SEC         = 5
CANDLE_LIMIT        = 80

# ── Paleta ─────────────────────────────────────────────────────
BG_DARK     = "#0d0f14"
BG_PANEL    = "#13161e"
BG_CARD     = "#1a1e2a"
BG_ROW_ALT  = "#161922"
ACCENT_BLUE = "#1a8fe3"
ACCENT_CYAN = "#00e5ff"
BTN_TOP     = "#1c2133"
BTN_HOVER   = "#253050"
RED         = "#e84040"
GREEN       = "#00c896"
TEXT_WHITE  = "#e8eaf0"
TEXT_GRAY   = "#7a7f94"
TEXT_DIM    = "#4a4f62"
BORDER      = "#252a3a"
SASH_COLOR  = "#2a3050"          # color del separador PanedWindow
CANDLE_UP   = "#00c896"
CANDLE_DOWN = "#e84040"
MPL_BG      = "#0a0c12"
MPL_GRID    = "#1e2436"
TRADE_STATE_FILE = Path(__file__).resolve().parent / "trade_state.json"

# ── Demo data ──────────────────────────────────────────────────
ORDERS_DEMO = [
    ("Buy",  "300$",  "130,620"), ("Buy",  "200$",  "131,220"),
    ("Buy",  "500$",  "131,220"), ("Sell", "-100$", "101,120"),
    ("Buy",  "400$",  "101,520"), ("Buy",  "100$",  "101,620"),
    ("Buy",  "300$",  "130,420"), ("Buy",  "300$",  "130,820"),
    ("Sell", "-50$",  "100,720"), ("Buy",  "500$",  "131,220"),
    ("Sell", "-100$", "101,120"), ("Sell", "-200$", "101,020"),
    ("Buy",  "100$",  "101,420"), ("Buy",  "300$",  "101,520"),
    ("Sell", "-50$",  "100,720"), ("Buy",  "400$",  "101,520"),
    ("Buy",  "500$",  "131,220"), ("Buy",  "100$",  "101,620"),
    ("Sell", "-100$", "101,120"), ("Buy",  "300$",  "101,520"),
]

def _format_value(value):
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _load_trade_state():
    if not TRADE_STATE_FILE.exists():
        return {"pending_trade": None, "history": []}
    try:
        with open(TRADE_STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
            if not isinstance(state, dict):
                return {"pending_trade": None, "history": []}
            state.setdefault("pending_trade", None)
            state.setdefault("history", [])
            return state
    except Exception as exc:
        logger.warning("No se pudo cargar %s: %s", TRADE_STATE_FILE, exc)
        return {"pending_trade": None, "history": []}


def _save_trade_state(state: dict):
    try:
        with open(TRADE_STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("No se pudo guardar %s: %s", TRADE_STATE_FILE, exc)


def get_current_market_price(symbol: str = SYMBOL, interval: str = "1m"):
    try:
        response = requests.get(
            BINANCE_KLINES,
            params={"symbol": symbol, "interval": interval, "limit": 1},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return None
        return float(payload[0][4])
    except Exception as exc:
        logger.warning("No se pudo obtener el precio actual del mercado: %s", exc)
        return None


def _coerce_numeric(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def _get_last_bot_balance():
    if MongoClient is None:
        return None
    client = None
    try:
        client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=1500)
        db = client["Damian"]
        available_collections = db.list_collection_names()
        collection = None
        for candidate in ["BotBalance", "botbalance", "botBalance"]:
            if candidate in available_collections:
                collection = db[candidate]
                break
        if collection is None:
            return None
        doc = collection.find_one({}, sort=[("_id", -1)])
        if not doc:
            return None
        balance = _coerce_numeric(doc.get("Balance"))
        return balance if balance is not None else None
    except Exception as exc:
        logger.warning("No se pudo leer el último balance de MongoDB: %s", exc)
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _save_trade_result_to_mongodb(side: str, profit: float, balance: float):
    if MongoClient is None:
        logger.info("pymongo no disponible, no se pudo guardar la operación en MongoDB")
        return False
    client = None
    try:
        client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=1500)
        db = client["Damian"]
        available_collections = db.list_collection_names()
        collection = None
        for candidate in ["BotBalance", "botbalance", "botBalance"]:
            if candidate in available_collections:
                collection = db[candidate]
                break
        if collection is None:
            logger.warning("La colección BotBalance no existe en MongoDB")
            return False
        collection.insert_one({
            "Order Type": side,
            "Profit": profit,
            "Balance": balance,
        })
        return True
    except Exception as exc:
        logger.warning("No se pudo insertar el cierre de orden en MongoDB: %s", exc)
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _read_mongo_documents(collection_name: str, projection: dict):
    if MongoClient is None:
        logger.info("pymongo no disponible, usando datos demo")
        return [], False

    client = None
    try:
        client = MongoClient("mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=1500)
        db = client["Damian"]
        available_dbs = client.list_database_names()
        if "Damian" not in available_dbs:
            logger.warning("La base de datos 'Damian' no existe. Bases disponibles: %s", available_dbs)
            return [], False

        candidate_names = [collection_name, collection_name.lower(), collection_name.capitalize()]
        available_collections = db.list_collection_names()
        collection = None
        for candidate in candidate_names:
            if candidate in available_collections:
                collection = db[candidate]
                break

        if collection is None:
            logger.warning(
                "La colección '%s' no existe en 'Damian'. Colecciones disponibles: %s",
                collection_name,
                available_collections,
            )
            return [], False

        docs = list(collection.find({}, projection).limit(50))
        if not docs:
            logger.warning("La colección '%s' existe pero no devolvió documentos", collection_name)
            return [], False
        return docs, True
    except Exception as exc:
        logger.warning("MongoDB no disponible para %s: %s", collection_name, exc)
        return [], False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def load_orders_from_mongodb():
    """Cargar órdenes desde MongoDB local y devolverlas como una lista de tuplas."""
    docs, loaded_from_mongo = _read_mongo_documents(
        "BotBalance",
        {"Order Type": 1, "Profit": 1, "Balance": 1, "_id": 0},
    )
    if not docs:
        logger.info("MongoDB no devolvió datos para ORDERS, usando ORDERS_DEMO")
        return ORDERS_DEMO, False

    orders = []
    for doc in docs:
        order_type = _format_value(doc.get("Order Type"))
        profit = _format_value(doc.get("Profit"))
        balance = _format_value(doc.get("Balance"))
        if order_type and profit and balance:
            orders.append((order_type, profit, balance))

    return (orders if orders else ORDERS_DEMO), loaded_from_mongo


ACCOUNTS_DEMO = [
    ("Nicolle Pelayo", "*****363", "62,985"),
    ("Enrique Pelayo",    "*****282", "62,985"),
    ("Roger Pelayo",       "*****171", "61,470"),
    ("Maria Chanot",        "*****939", "62,985"),
    ("Daniela Echeverri",   "*****626", "62,985"),
]

def load_accounts_from_mongodb():
    """Cargar cuentas desde MongoDB local y devolverlas como una lista de tuplas."""
    docs, loaded_from_mongo = _read_mongo_documents(
        "Accounts",
        {"User Name": 1, "Accounts": 1, "Total": 1, "_id": 0},
    )
    if not docs:
        logger.info("MongoDB no devolvió datos para ACCOUNTS, usando ACCOUNTS_DEMO")
        return ACCOUNTS_DEMO, False

    accounts = []
    for doc in docs:
        user_name = _format_value(doc.get("User Name"))
        accounts_value = _format_value(doc.get("Accounts"))
        total = _format_value(doc.get("Total"))
        if user_name and accounts_value and total:
            accounts.append((user_name, accounts_value, total))

    return (accounts if accounts else ACCOUNTS_DEMO), loaded_from_mongo


def _update_data_source_message(orders_loaded_from_mongo: bool, accounts_loaded_from_mongo: bool):
    global DATA_SOURCE_MESSAGE
    DATA_SOURCE_MESSAGE = (
        "Datos cargados desde MongoDB"
        if orders_loaded_from_mongo and accounts_loaded_from_mongo
        else "Datos cargados desde demo"
    )


def _initialize_mongo_data():
    global ORDERS, ACCOUNTS
    orders, orders_loaded_from_mongo = load_orders_from_mongodb()
    accounts, accounts_loaded_from_mongo = load_accounts_from_mongodb()
    _update_data_source_message(orders_loaded_from_mongo, accounts_loaded_from_mongo)
    ORDERS = orders
    ACCOUNTS = accounts
    return ORDERS, ACCOUNTS


STATS = [
    ("DAY",   "300",    "50",    "250"),
    ("WEEK",  "1,500",  "500",   "1,000"),
    ("MONTH", "7,820",  "950",   "6,870"),
    ("YEAR",  "64,020", "2,550", "61,470"),
]
ORDERS, ACCOUNTS = _initialize_mongo_data()


# ══════════════════════════════════════════════════════════════
#  Binance fetcher (hilo de fondo)
# ══════════════════════════════════════════════════════════════
def fetch_klines(interval: str, limit: int = CANDLE_LIMIT):
    r = None
    try:
        r = requests.get(
            BINANCE_KLINES,
            params={"symbol": SYMBOL, "interval": interval, "limit": limit},
            timeout=8,
        )
        r.raise_for_status()
        df = pd.DataFrame(r.json(), columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
        ])
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df[["open_time", "open", "high", "low", "close"]]
    except requests.exceptions.HTTPError as exc:
        try:
            data = r.json()
            message = data.get("msg") or data.get("message") or r.text
        except Exception:
            message = r.text
        error_msg = f"Binance HTTP {r.status_code}: {message}"
        logger.exception(error_msg)
        return {"error": error_msg}
    except requests.exceptions.RequestException as exc:
        error_msg = f"Binance request failed: {exc}"
        logger.warning(error_msg)
        return {"error": error_msg}
    except ValueError as exc:
        error_msg = f"Response parsing failed: {exc}"
        logger.exception(error_msg)
        return {"error": error_msg}
    except Exception as exc:
        error_msg = f"Unexpected error: {exc}"
        logger.exception(error_msg)
        return {"error": error_msg}


def get_symbol_info(symbol: str):
    r = None
    try:
        r = requests.get(
            BINANCE_EXCHANGE_INFO,
            params={"symbol": symbol},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        symbols = data.get("symbols")
        if not symbols:
            raise ValueError("No symbol metadata returned")
        return symbols[0]
    except requests.exceptions.HTTPError as exc:
        try:
            data = r.json()
            message = data.get("msg") or data.get("message") or r.text
        except Exception:
            message = r.text
        error_msg = f"Binance symbol check failed: HTTP {r.status_code}: {message}"
        logger.exception(error_msg)
        return {"error": error_msg}
    except requests.exceptions.RequestException as exc:
        error_msg = f"Binance symbol check request failed: {exc}"
        logger.warning(error_msg)
        return {"error": error_msg}
    except ValueError as exc:
        error_msg = f"Symbol metadata parsing failed: {exc}"
        logger.exception(error_msg)
        return {"error": error_msg}
    except Exception as exc:
        error_msg = f"Unexpected symbol check error: {exc}"
        logger.exception(error_msg)
        return {"error": error_msg}


# ══════════════════════════════════════════════════════════════
#  Aplicación principal
# ══════════════════════════════════════════════════════════════
class TradingBotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Trading Bot  —  {SYMBOL}  Tiempo Real")
        self.configure(bg=BG_DARK)
        self.geometry("1440x900")
        self.minsize(1100, 700)

        # Estado
        self.interval_var = tk.StringVar(value="1m")
        self.last_price   = tk.StringVar(value="—")
        self.pct_var      = tk.StringVar(value="")
        self.status_var   = tk.StringVar(value="Conectando con Binance…")
        self.data_source_var = tk.StringVar(value=DATA_SOURCE_MESSAGE)
        self._queue       = queue.Queue()
        self._running     = True
        self.df_candles   = None
        self._pct_label   = None   # se asigna en _top_bar
        self.symbol_info  = None

        _initialize_mongo_data()
        self.data_source_var.set(DATA_SOURCE_MESSAGE)
        self._build_ui()
        self._validate_symbol()
        if self.symbol_info is not None:
            self._start_feed()
        self._poll_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Ciclo de vida ────────────────────────────────────────────
    def _on_close(self):
        self._running = False
        self.destroy()

    def _start_feed(self):
        threading.Thread(target=self._feed_loop, daemon=True).start()

    def _fetch_once(self):
        if self.symbol_info is None:
            self._queue.put({"error": "Símbolo no validado o no soportado."})
            return
        df = fetch_klines(self.interval_var.get())
        self._queue.put(df)

    def _feed_loop(self):
        while self._running:
            df = fetch_klines(self.interval_var.get())
            self._queue.put(df)
            time.sleep(REFRESH_SEC)

    def _poll_queue(self):
        try:
            while True:
                df = self._queue.get_nowait()
                if isinstance(df, dict) and df.get("error"):
                    self.status_var.set(f"⚠ {df['error']}")
                    self._show_error_on_chart(df["error"])
                    continue
                if df is not None and not df.empty:
                    self.df_candles = df
                    try:
                        self._draw_chart(df)
                        self._refresh_ticker(df)
                    except Exception as exc:
                        error_msg = f"Error al dibujar velas: {exc}"
                        logger.exception(error_msg)
                        self.status_var.set(f"⚠ {error_msg}")
                        self._show_error_on_chart(error_msg)
                        continue
                    self.status_var.set(
                        f"✓ {datetime.now().strftime('%H:%M:%S')}  |  {len(df)} velas")
                else:
                    self.status_var.set("⚠  Sin respuesta — reintentando…")
                    self._show_error_on_chart("No se recibieron velas válidas. Reintentando…")
        except queue.Empty:
            pass
        if self._running:
            self.after(400, self._poll_queue)

    # ══════════════════════════════════════════════════════════
    #  Construcción de UI
    # ══════════════════════════════════════════════════════════
    def _build_ui(self):
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)
        self.columnconfigure(0, weight=1)

        self._top_bar()

        # ── PanedWindow HORIZONTAL principal ──────────────────
        self.h_pane = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            bg=SASH_COLOR,
            sashwidth=6,
            sashrelief="flat",
            handlesize=0,
        )
        self.h_pane.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))

        # Panel izquierdo — Órdenes
        left_frame = tk.Frame(self.h_pane, bg=BG_PANEL)
        self.h_pane.add(left_frame, minsize=120, width=175, stretch="never")
        self._left_panel(left_frame)

        # Panel central — Gráfico
        center_frame = tk.Frame(self.h_pane, bg=BG_PANEL)
        self.h_pane.add(center_frame, minsize=300, stretch="always")
        self._center_panel(center_frame)

        # Panel derecho — Cuenta + Estadísticas + Usuarios
        right_frame = tk.Frame(self.h_pane, bg=BG_PANEL)
        self.h_pane.add(right_frame, minsize=180, width=230, stretch="never")
        self._right_panel(right_frame)

        self._bottom_bar()

    # ── Top bar ──────────────────────────────────────────────────
    def _top_bar(self):
        bar = tk.Frame(self, bg=BG_PANEL, height=44)
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        bar.grid_propagate(False)

        for lbl in ["GENERATE STATISTICS", "UPDATE GRAPH", "MARK SING",
                    "SHOW RESULTS", "CLEAN GRAPH"]:
            tk.Button(
                bar, text=lbl, bg=BTN_TOP, fg=TEXT_WHITE,
                activebackground=BTN_HOVER, activeforeground=TEXT_WHITE,
                relief="flat", bd=0, padx=12, pady=6,
                font=("Segoe UI", 8, "bold"), cursor="hand2",
            ).pack(side="left", padx=2, pady=6)

        tf = tk.Frame(bar, bg=BG_PANEL)
        tf.pack(side="right", padx=14)
        tk.Label(tf, text="BTC/USDT", bg=BG_PANEL, fg=TEXT_GRAY,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 6))
        tk.Label(tf, textvariable=self.last_price, bg=BG_PANEL,
                 fg=ACCENT_CYAN, font=("Segoe UI", 11, "bold")).pack(side="left")
        self._pct_label = tk.Label(tf, textvariable=self.pct_var,
                                   bg=BG_PANEL, fg=GREEN, font=("Segoe UI", 9))
        self._pct_label.pack(side="left", padx=8)
        tk.Label(tf, textvariable=self.status_var, bg=BG_PANEL,
                 fg=TEXT_DIM, font=("Segoe UI", 7)).pack(side="left")
        tk.Label(
            tf,
            textvariable=self.data_source_var,
            bg=BG_PANEL,
            fg=GREEN if self.data_source_var.get() == "Datos cargados desde MongoDB" else TEXT_GRAY,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(8, 0))

    # ── Panel izquierdo — Historial de órdenes ──────────────────
    def _left_panel(self, parent):
        # Cabecera fija
        hdr = tk.Frame(parent, bg=BG_CARD)
        hdr.pack(fill="x")
        for txt, w in [("Order Type", 8), ("Profit", 6), ("Balance", 9)]:
            tk.Label(hdr, text=txt, bg=BG_CARD, fg=TEXT_GRAY,
                     font=("Segoe UI", 7, "bold"), width=w,
                     anchor="w", padx=4, pady=5).pack(side="left")

        # Área scrollable con Canvas
        wrap = tk.Frame(parent, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True)

        cvs = tk.Canvas(wrap, bg=BG_PANEL, highlightthickness=0)
        sb  = tk.Scrollbar(wrap, orient="vertical", command=cvs.yview)
        cvs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(cvs, bg=BG_PANEL)
        win_id = cvs.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(e):
            cvs.configure(scrollregion=cvs.bbox("all"))
        def _on_canvas_configure(e):
            cvs.itemconfig(win_id, width=e.width)

        inner.bind("<Configure>", _on_inner_configure)
        cvs.bind("<Configure>",  _on_canvas_configure)

        for i, (ot, pr, bal) in enumerate(ORDERS):
            rbg   = BG_ROW_ALT if i % 2 == 0 else BG_PANEL
            fc    = RED if pr.startswith("-") else TEXT_WHITE
            row   = tk.Frame(inner, bg=rbg)
            row.pack(fill="x")
            tk.Label(row, text=ot,  bg=rbg, fg=TEXT_WHITE,
                     font=("Segoe UI", 7), anchor="w",
                     padx=4, pady=3, width=8).pack(side="left")
            tk.Label(row, text=pr,  bg=rbg, fg=fc,
                     font=("Segoe UI", 7), anchor="w",
                     padx=2, pady=3, width=6).pack(side="left")
            tk.Label(row, text=bal, bg=rbg, fg=TEXT_GRAY,
                     font=("Segoe UI", 7), anchor="w",
                     padx=2, pady=3, width=9).pack(side="left")

        # Scroll con rueda del ratón
        def _mousewheel(e):
            cvs.yview_scroll(int(-1 * (e.delta / 120)), "units")
        cvs.bind_all("<MouseWheel>", _mousewheel)

    # ── Panel central — Gráfico Matplotlib ──────────────────────
    def _center_panel(self, parent):
        # Sub-header
        hdr = tk.Frame(parent, bg=BG_PANEL)
        hdr.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(hdr, text="BTCUSDT  ·  Bitcoin / Dólar  ·  Tiempo Real",
                 bg=BG_PANEL, fg=TEXT_WHITE,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(hdr, text="Fuente: Binance API (público)", bg=BG_PANEL,
                 fg=TEXT_DIM, font=("Segoe UI", 7)).pack(side="right")

        # Tooltip de ayuda para PanedWindow
        tk.Label(hdr, text="← arrastra bordes para redimensionar →",
                 bg=BG_PANEL, fg=SASH_COLOR,
                 font=("Segoe UI", 7, "italic")).pack(side="right", padx=8)

        # Figura Matplotlib
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.fig.patch.set_facecolor(MPL_BG)
        self.ax  = self.fig.add_subplot(111)
        self._style_ax()

        self.ax.text(0.5, 0.5, "Conectando con Binance…",
                     transform=self.ax.transAxes,
                     ha="center", va="center",
                     color=TEXT_GRAY, fontsize=12)

        self.mpl_canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.mpl_canvas.draw()
        widget = self.mpl_canvas.get_tk_widget()
        # Asegurar que el widget Tk tenga el mismo fondo que la figura
        try:
            widget.configure(bg=MPL_BG)
        except Exception:
            pass
        widget.pack(fill="both", expand=True, padx=2, pady=2)

        # Redibujar cuando el panel cambia de tamaño (por PanedWindow)
        widget.bind("<Configure>", self._on_chart_resize)

    def _on_chart_resize(self, event):
        """Ajusta la figura al nuevo tamaño del widget."""
        w = event.width  / self.fig.dpi
        h = event.height / self.fig.dpi
        if w > 1 and h > 1:
            # Forzar que la figura ocupe totalmente el widget y redibujar
            self.fig.set_size_inches(w, h, forward=True)
            if self.df_candles is not None:
                self._draw_chart(self.df_candles)
            else:
                self.mpl_canvas.draw_idle()

    def _style_ax(self):
        self.ax.set_facecolor(MPL_BG)
        self.ax.tick_params(colors=TEXT_GRAY, labelsize=7)
        self.ax.yaxis.tick_right()
        self.ax.yaxis.set_label_position("right")
        for sp in self.ax.spines.values():
            sp.set_edgecolor(MPL_GRID)
        self.ax.grid(True, color=MPL_GRID, linestyle="--",
                     linewidth=0.5, alpha=0.6)
        self.fig.subplots_adjust(left=0.01, right=0.87,
                                 top=0.95,  bottom=0.11)

    def _draw_chart(self, df: pd.DataFrame):
        self.ax.cla()
        self._style_ax()
        n = len(df)

        for i, row in enumerate(df.itertuples()):
            bull  = row.close >= row.open
            color = CANDLE_UP if bull else CANDLE_DOWN
            # Mecha
            self.ax.plot([i, i], [row.low, row.high],
                         color=color, linewidth=0.8)
            # Cuerpo
            lo = min(row.open, row.close)
            hi = max(row.open, row.close)
            h  = max(hi - lo, 0.05)
            rect = mpatches.FancyBboxPatch(
                (i - 0.38, lo), 0.76, h,
                boxstyle="square,pad=0",
                facecolor=color, edgecolor=color, linewidth=0.4,
            )
            self.ax.add_patch(rect)

        # Línea de precio actual
        last = df["close"].iloc[-1]
        self.ax.axhline(last, color=ACCENT_CYAN, linewidth=0.9,
                        linestyle="--", alpha=0.85)
        self.ax.text(n + 0.5, last, f" {last:,.2f}",
                     color=ACCENT_CYAN, fontsize=7.5,
                     va="center", fontweight="bold")

        # Eje X — timestamps
        step = max(1, n // 8)
        ticks = list(range(0, n, step))
        self.ax.set_xticks(ticks)
        self.ax.set_xticklabels(
            [df["open_time"].iloc[i].strftime("%H:%M") for i in ticks],
            color=TEXT_GRAY, fontsize=7,
        )
        self.ax.set_xlim(-1, n + 4)

        # Eje Y
        self.ax.yaxis.set_major_formatter(
            mticker.FormatStrFormatter("%.2f"))
        self.ax.tick_params(axis="y", colors=TEXT_GRAY, labelsize=7)

        # Título dinámico
        self.ax.set_title(
            f"BTCUSDT  |  {self.interval_var.get()}  |  "
            f"{datetime.now().strftime('%H:%M:%S')}",
            color=TEXT_GRAY, fontsize=8, pad=3,
        )
        self.mpl_canvas.draw_idle()

    def _refresh_ticker(self, df: pd.DataFrame):
        last  = df["close"].iloc[-1]
        first = df["open"].iloc[0]
        pct   = (last - first) / first * 100
        self.last_price.set(f"${last:,.3f}")
        sign  = "▲" if pct >= 0 else "▼"
        self.pct_var.set(f"{sign} {abs(pct):.2f}%")
        if self._pct_label:
            self._pct_label.config(fg=GREEN if pct >= 0 else RED)

    def _show_error_on_chart(self, message: str):
        self.ax.cla()
        self._style_ax()
        self.ax.text(
            0.5, 0.5, message,
            transform=self.ax.transAxes,
            ha="center", va="center",
            color=RED, fontsize=10,
            wrap=True,
        )
        self.mpl_canvas.draw_idle()

    def _validate_symbol(self):
        symbol_data = get_symbol_info(SYMBOL)
        if isinstance(symbol_data, dict) and symbol_data.get("error"):
            self.symbol_info = None
            self.status_var.set(f"⚠ {symbol_data['error']}")
            self._show_error_on_chart(symbol_data["error"])
            return
        self.symbol_info = symbol_data
        self.status_var.set(f"✓ Símbolo soportado: {SYMBOL}")
        if self.interval_var.get() not in self.symbol_info.get("intervals", []):
            error_msg = f"Intervalo no soportado para {SYMBOL}: {self.interval_var.get()}"
            self.status_var.set(f"⚠ {error_msg}")
            self._show_error_on_chart(error_msg)

    # ── Panel derecho — con PanedWindow VERTICAL interno ────────
    def _right_panel(self, parent):
        # ── PanedWindow VERTICAL dentro del panel derecho ─────
        v_pane = tk.PanedWindow(
            parent,
            orient=tk.VERTICAL,
            bg=SASH_COLOR,
            sashwidth=5,
            sashrelief="flat",
            handlesize=0,
        )
        v_pane.pack(fill="both", expand=True)

        # Sección superior: cuenta + stats + detalles
        top_frame = tk.Frame(v_pane, bg=BG_PANEL)
        v_pane.add(top_frame, minsize=180, stretch="always")
        self._account_header(top_frame)
        self._stats_table(top_frame)
        self._account_details(top_frame)

        # Sección inferior: lista de cuentas
        bot_frame = tk.Frame(v_pane, bg=BG_PANEL)
        v_pane.add(bot_frame, minsize=80, stretch="always")
        self._accounts_list(bot_frame)

    def _account_header(self, parent):
        hdr = tk.Frame(parent, bg=BG_CARD)
        hdr.pack(fill="x", padx=4, pady=(4, 2))

        av = tk.Canvas(hdr, width=44, height=44,
                       bg=ACCENT_BLUE, highlightthickness=0)
        av.pack(side="left", padx=6, pady=6)
        av.create_oval(4, 4, 40, 40, fill="#1060a0", outline="")
        # Try to load a custom avatar image (rp.png) placed in the
        # application's folder. Use Pillow to resize if available,
        # otherwise attempt a subsample with Tk's PhotoImage. Store
        # the PhotoImage on `self` to avoid GC.
        try:
            if not hasattr(self, "_rp_photo"):
                if Image is not None and ImageTk is not None:
                    img = Image.open("rp.png").convert("RGBA")
                    img = img.resize((36, 36), Image.LANCZOS)
                    self._rp_photo = ImageTk.PhotoImage(img)
                else:
                    # Fallback: load with Tk and subsample if too large
                    p = tk.PhotoImage(file="rp.png")
                    w, h = p.width(), p.height()
                    if max(w, h) > 36:
                        factor = max(1, int(max(w, h) / 36))
                        p = p.subsample(factor, factor)
                    self._rp_photo = p
            av.create_image(22, 22, image=self._rp_photo)
        except Exception:
            # Fallback to text if image can't be loaded
            av.create_text(22, 22, text="RP", fill=TEXT_WHITE,
                           font=("Segoe UI", 11, "bold"))

        info = tk.Frame(hdr, bg=BG_CARD)
        info.pack(side="left", pady=6)
        tk.Label(info, text="Account:", bg=BG_CARD, fg=RED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(info, text="Roger Pelayo", bg=BG_CARD,
                 fg=TEXT_WHITE, font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(info, text="Balance:", bg=BG_CARD, fg=RED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(info, text="61,470", bg=BG_CARD, fg=TEXT_WHITE,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")

    def _stats_table(self, parent):
        frame = tk.Frame(parent, bg=BG_CARD)
        frame.pack(fill="x", padx=4, pady=2)

        hdr = tk.Frame(frame, bg=BG_CARD)
        hdr.pack(fill="x", pady=(4, 0))
        for txt, w in [("PERIOD", 6), ("PROFIT", 6), ("LOST", 5), ("BALANCE", 7)]:
            tk.Label(hdr, text=txt, bg=BG_CARD, fg=ACCENT_BLUE,
                     font=("Segoe UI", 7, "bold"),
                     width=w, anchor="w").pack(side="left", padx=3)

        for period, profit, lost, bal in STATS:
            row = tk.Frame(frame, bg=BG_CARD)
            row.pack(fill="x")
            tk.Label(row, text=period, bg=BG_CARD, fg=TEXT_GRAY,
                     font=("Segoe UI", 7), width=6, anchor="w").pack(side="left", padx=3, pady=1)
            tk.Label(row, text=profit, bg=BG_CARD, fg=GREEN,
                     font=("Segoe UI", 7), width=6, anchor="w").pack(side="left", padx=3)
            tk.Label(row, text=lost,   bg=BG_CARD, fg=RED,
                     font=("Segoe UI", 7), width=5, anchor="w").pack(side="left", padx=3)
            tk.Label(row, text=bal,    bg=BG_CARD, fg=TEXT_WHITE,
                     font=("Segoe UI", 7), width=7, anchor="w").pack(side="left", padx=3)

    def _account_details(self, parent):
        frame = tk.Frame(parent, bg=BG_CARD)
        frame.pack(fill="x", padx=4, pady=4)

        tk.Label(frame, text="ACCOUNTS DETAILS", bg=BG_CARD, fg=TEXT_WHITE,
                 font=("Segoe UI", 8, "bold")).pack(pady=(6, 4))

        for label in ("Email:", "Password:", "API KEY:"):
            row = tk.Frame(frame, bg=BG_CARD)
            row.pack(fill="x", padx=6, pady=2)
            tk.Label(row, text=label, bg=BG_CARD, fg=TEXT_GRAY,
                     font=("Segoe UI", 8), width=9, anchor="w").pack(side="left")
            show = "*" if "Password" in label else None
            tk.Entry(row, bg="#0d0f14", fg=TEXT_WHITE,
                     insertbackground=TEXT_WHITE,
                     relief="flat", font=("Segoe UI", 8), show=show,
                     ).pack(side="left", fill="x", expand=True, ipady=3)

        tk.Button(frame, text="ADD", bg=ACCENT_BLUE, fg=TEXT_WHITE,
                  activebackground="#1060a0", relief="flat", bd=0,
                  font=("Segoe UI", 8, "bold"), padx=20, pady=4,
                  cursor="hand2").pack(pady=(6, 8))

    def _accounts_list(self, parent):
        # Cabecera
        hdr = tk.Frame(parent, bg=BG_CARD)
        hdr.pack(fill="x")
        for txt, w in [("Username", 11), ("Account", 9), ("Total", 7)]:
            tk.Label(hdr, text=txt, bg=BG_CARD, fg=TEXT_WHITE,
                     font=("Segoe UI", 7, "bold"), width=w,
                     anchor="w", padx=4, pady=4).pack(side="left")

        # Lista scrollable
        wrap = tk.Frame(parent, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True)

        cvs = tk.Canvas(wrap, bg=BG_PANEL, highlightthickness=0)
        sb  = tk.Scrollbar(wrap, orient="vertical", command=cvs.yview)
        cvs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(cvs, bg=BG_PANEL)
        win_id = cvs.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner(e):
            cvs.configure(scrollregion=cvs.bbox("all"))
        def _on_cvs(e):
            cvs.itemconfig(win_id, width=e.width)

        inner.bind("<Configure>", _on_inner)
        cvs.bind("<Configure>",  _on_cvs)

        for i, (name, acct, total) in enumerate(ACCOUNTS):
            bg = BG_ROW_ALT if i % 2 == 0 else BG_PANEL
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x")
            tk.Label(row, text=name,  bg=bg, fg=ACCENT_BLUE,
                     font=("Segoe UI", 7), width=11,
                     anchor="w", padx=4, pady=3).pack(side="left")
            tk.Label(row, text=acct,  bg=bg, fg=TEXT_GRAY,
                     font=("Segoe UI", 7), width=9, anchor="w").pack(side="left")
            tk.Label(row, text=total, bg=bg, fg=GREEN,
                     font=("Segoe UI", 7), width=7, anchor="w").pack(side="left")

    # ── Bottom bar ───────────────────────────────────────────────
    def _bottom_bar(self):
        bar = tk.Frame(self, bg=BG_PANEL)
        bar.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))

        canvas = tk.Canvas(bar, bg=BG_PANEL, highlightthickness=0, height=140)
        x_scroll = tk.Scrollbar(bar, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=x_scroll.set)

        canvas.grid(row=0, column=0, sticky="ew")
        x_scroll.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        inner = tk.Frame(canvas, bg=BG_PANEL)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _on_configure)

        self._bottom_section(inner, "ACCOUNT SETTINGS",
                             ["A single account", "Multiple accounts"])
        self._divider(inner)
        self._bottom_section(inner, "BOT CONFIGURATION",
                             ["Automatic", "Manual"])
        self._divider(inner)
        self._bottom_section(inner, "COLOR", ["DARK", "LIGHT"])
        self._divider(inner)

        # TIME — controla el intervalo del gráfico
        tf = tk.Frame(inner, bg=BG_PANEL)
        tf.pack(side="left", padx=8, pady=4, anchor="nw")
        tk.Label(tf, text="TIME (intervalo gráfico)", bg=BG_PANEL,
                 fg=TEXT_WHITE, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        br = tk.Frame(tf, bg=BG_PANEL)
        br.pack(anchor="w")
        for opt in ["1m", "5m", "15m", "30m", "1h"]:
            tk.Radiobutton(
                br, text=opt, variable=self.interval_var, value=opt,
                bg=BG_PANEL, fg=TEXT_GRAY, selectcolor=BG_DARK,
                activebackground=BG_PANEL, activeforeground=TEXT_WHITE,
                font=("Segoe UI", 7),
                command=self._on_interval_change,
            ).pack(side="left", padx=3)

        self._divider(inner)

        # TRADE NOW
        trade_f = tk.Frame(inner, bg=BG_PANEL)
        trade_f.pack(side="left", padx=12, pady=10, anchor="nw")
        tk.Button(
            trade_f, text="  TRADE NOW  ▼", bg=ACCENT_BLUE, fg=TEXT_WHITE,
            activebackground="#1060a0", activeforeground=TEXT_WHITE,
            relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
            padx=10, pady=8, cursor="hand2",
            command=self._open_trade_modal,
        ).pack(side="left")
        tk.Button(
            trade_f, text="CLOSE ORDER", bg="#d9534f", fg=TEXT_WHITE,
            activebackground="#b52f2a", activeforeground=TEXT_WHITE,
            relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
            padx=10, pady=8, cursor="hand2",
            command=self._close_order_action,
        ).pack(side="left", padx=(8, 0))

    def _open_trade_modal(self):
        if hasattr(self, "_trade_modal") and self._trade_modal.winfo_exists():
            self._trade_modal.lift()
            return

        modal = tk.Toplevel(self)
        modal.title("TRADE NOW")
        modal.configure(bg=BG_PANEL)
        modal.transient(self)
        modal.grab_set()
        modal.resizable(False, False)
        modal.geometry("380x300")
        self._trade_modal = modal

        header = tk.Frame(modal, bg=BG_PANEL)
        header.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(header, text="Select a trading option:",
                 bg=BG_PANEL, fg=TEXT_WHITE,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Button(header, text="Cerrar", bg=BG_CARD, fg=TEXT_WHITE,
                  activebackground=BTN_HOVER, activeforeground=TEXT_WHITE,
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2", command=modal.destroy).pack(side="right")

        option_bar = tk.Frame(modal, bg=BG_PANEL)
        option_bar.pack(fill="x", padx=12, pady=(0, 10))
        options = [
            ("Market Execution", "market"),
            ("Buy Stop", "buy_stop"),
            ("Sell Stop", "sell_stop"),
        ]
        for label, value in options:
            tk.Button(
                option_bar, text=label, bg=BG_CARD, fg=TEXT_WHITE,
                activebackground=BTN_HOVER, activeforeground=TEXT_WHITE,
                relief="flat", bd=0, font=("Segoe UI", 8),
                padx=10, pady=8, cursor="hand2",
                command=lambda v=value: self._render_trade_option(v, content_frame),
            ).pack(side="left", expand=True, fill="x", padx=3)

        content_frame = tk.Frame(modal, bg=BG_PANEL)
        content_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._render_trade_option("market", content_frame)

    def _render_trade_option(self, option: str, container):
        for widget in container.winfo_children():
            widget.destroy()

        if option == "market":
            tk.Label(container, text="Ejecución por mercado:",
                     bg=BG_PANEL, fg=TEXT_WHITE,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))

            btn_bar = tk.Frame(container, bg=BG_PANEL)
            btn_bar.pack(fill="x")
            for side in ["Buy", "Sell"]:
                tk.Button(
                    btn_bar, text=side, bg=ACCENT_BLUE, fg=TEXT_WHITE,
                    activebackground="#1060a0", activeforeground=TEXT_WHITE,
                    relief="flat", bd=0, font=("Segoe UI", 8, "bold"),
                    padx=10, pady=8, cursor="hand2",
                    command=lambda s=side: self._place_trade_action("market", s, {}),
                ).pack(side="left", expand=True, fill="x", padx=4)
        else:
            title = "Buy Stop" if option == "buy_stop" else "Sell Stop"
            tk.Label(container, text=title,
                     bg=BG_PANEL, fg=TEXT_WHITE,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))

            fields = {}
            for field in ["Price", "Stop Loss", "Take Profit"]:
                row = tk.Frame(container, bg=BG_PANEL)
                row.pack(fill="x", pady=4)
                tk.Label(row, text=f"{field}", bg=BG_PANEL, fg=TEXT_GRAY,
                         font=("Segoe UI", 8), width=12, anchor="w").pack(side="left")
                entry = tk.Entry(row, bg=BG_CARD, fg=TEXT_WHITE,
                                 insertbackground=TEXT_WHITE,
                                 relief="flat", font=("Segoe UI", 8))
                entry.pack(side="left", fill="x", expand=True, ipady=4)
                fields[field] = entry

            tk.Button(
                container, text="Place", bg=ACCENT_BLUE, fg=TEXT_WHITE,
                activebackground="#1060a0", activeforeground=TEXT_WHITE,
                relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
                padx=10, pady=8, cursor="hand2",
                command=lambda: self._place_trade_action(option, None, fields),
            ).pack(pady=12)

    def _place_trade_action(self, option: str, side, fields: dict):
        if option == "market":
            price = get_current_market_price()
            if price is None:
                self.status_var.set("⚠ No se pudo obtener el precio del mercado")
            else:
                state = _load_trade_state()
                state["pending_trade"] = {
                    "side": side,
                    "entry_price": round(price, 2),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                _save_trade_state(state)
                self.status_var.set(f"✓ Market {side} registrado a ${price:,.2f}")
        else:
            price = fields.get("Price").get().strip()
            stop_loss = fields.get("Stop Loss").get().strip()
            take_profit = fields.get("Take Profit").get().strip()
            self.status_var.set(
                f"✓ {option.replace('_', ' ').title()} | Price={price} | SL={stop_loss} | TP={take_profit}"
            )
        if hasattr(self, "_trade_modal") and self._trade_modal.winfo_exists():
            self._trade_modal.destroy()

    def _close_order_action(self):
        state = _load_trade_state()
        pending_trade = state.get("pending_trade")
        if not pending_trade:
            self.status_var.set("⚠ No hay una orden abierta para cerrar")
            return

        close_price = get_current_market_price()
        if close_price is None:
            self.status_var.set("⚠ No se pudo obtener el precio del mercado al cerrar")
            return

        entry_price = float(pending_trade.get("entry_price", 0.0))
        side = pending_trade.get("side", "Buy")

        if side == "Buy":
            if close_price > entry_price:
                outcome = "ganancia"
            else:
                outcome = "pérdida"
        else:
            if close_price < entry_price:
                outcome = "ganancia"
            else:
                outcome = "pérdida"

        profit = abs(close_price - entry_price)

        last_balance = _get_last_bot_balance()
        if last_balance is None:
            last_balance = 0.0
        new_balance = last_balance + profit if outcome == "ganancia" else last_balance - profit

        saved = _save_trade_result_to_mongodb(side, profit if outcome == "ganancia" else -profit, new_balance)
        if saved:
            global ORDERS
            ORDERS.append((side, f"{profit if outcome == 'ganancia' else -profit:+.2f}$", f"{new_balance:,.2f}"))
            state["history"].append({
                "side": side,
                "entry_price": round(entry_price, 2),
                "close_price": round(close_price, 2),
                "result": outcome,
                "profit": round(profit if outcome == "ganancia" else -profit, 2),
                "new_balance": round(new_balance, 2),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            state["pending_trade"] = None
            _save_trade_state(state)
            self.status_var.set(
                f"✓ Cierre {side} | Precio cierre=${close_price:,.2f} | {outcome.title()} ${profit:,.2f} | Balance ${new_balance:,.2f}"
            )
        else:
            self.status_var.set("⚠ No se pudo guardar el cierre en MongoDB")

    def _on_interval_change(self):
        self.ax.cla()
        self._style_ax()
        iv = self.interval_var.get()
        self.ax.text(0.5, 0.5, f"Cambiando a intervalo {iv}…",
                     transform=self.ax.transAxes,
                     ha="center", va="center",
                     color=TEXT_GRAY, fontsize=11)
        self.mpl_canvas.draw_idle()
        if self.symbol_info is None:
            self.status_var.set("⚠ Símbolo no validado o no soportado.")
            self._show_error_on_chart("Símbolo no soportado o no validado.")
            return
        if iv not in self.symbol_info.get("intervals", []):
            error_msg = f"Intervalo no soportado para {SYMBOL}: {iv}"
            self.status_var.set(f"⚠ {error_msg}")
            self._show_error_on_chart(error_msg)
            return
        self.status_var.set(f"Cargando {iv}…")
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        threading.Thread(target=self._fetch_once, daemon=True).start()

    def _bottom_section(self, parent, title, options):
        f = tk.Frame(parent, bg=BG_PANEL)
        f.pack(side="left", padx=8, pady=4)
        tk.Label(f, text=title, bg=BG_PANEL, fg=TEXT_WHITE,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        var = tk.StringVar(value=options[0])
        for opt in options:
            tk.Radiobutton(
                f, text=opt, variable=var, value=opt,
                bg=BG_PANEL, fg=TEXT_GRAY, selectcolor=BG_DARK,
                activebackground=BG_PANEL, activeforeground=TEXT_WHITE,
                font=("Segoe UI", 7),
            ).pack(anchor="w")

    def _divider(self, parent):
        tk.Frame(parent, bg=BORDER, width=1).pack(
            side="left", fill="y", pady=6, padx=2)


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = TradingBotApp()
    app.mainloop()