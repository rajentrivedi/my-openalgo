"""
Documentation data for all 15 Quantix analytical tools.

Provides structured ToolDoc dataclass instances with overviews, features,
usage instructions, API endpoint references, practical examples, and
configuration notes for each tool listed on the /tools route.
"""

from dataclasses import dataclass, field


@dataclass
class ToolDoc:
    """Structured documentation for a single Quantix analytical tool."""

    title: str
    route: str
    color_hex: str
    overview: str
    key_features: list[str]
    how_to_use: list[str]
    api_endpoints: list[dict]
    example_scenario: str
    configuration: str | None = None
    related_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Strategy Builder
# ---------------------------------------------------------------------------
Strategy_Builder = ToolDoc(
    title="Strategy Builder",
    route="/strategybuilder",
    color_hex="#D946EF",
    overview=(
        "The Strategy Builder is a powerful tool for constructing multi-leg option strategies "
        "with live Greeks, payoff diagrams, and what-if simulators. It enables traders to design, "
        "visualise, and execute complex option combinations — from basic vertical spreads to "
        "multi-leg Iron Condors and Butterflies — all from a single interface."
    ),
    key_features=[
        "Pre-built strategy templates (Iron Condor, Strangle, Bull Put Spread, Bear Call Spread, etc.)",
        "Manual leg entry with full control over strike, expiry, option type, and quantity",
        "Live Greeks display (Delta, Gamma, Theta, Vega) for the constructed strategy",
        "Interactive payoff diagram showing profit/loss at expiry across the strike range",
        "What-if analysis by adjusting spot price, volatility, and time to expiry",
        "Basket execution — place all legs as a single multi-leg order",
        "Save strategies to portfolio for future monitoring and re-use",
    ],
    how_to_use=[
        "Navigate to the Strategy Builder from the Tools page or use the /strategybuilder route.",
        "Select a strategy template from the dropdown, or build your own using Manual Entry mode.",
        "For each leg, choose the instrument, strike price, expiry date, option type (CE/PE), and quantity.",
        "Review the payoff diagram and Greeks panel to assess the risk/reward profile.",
        "Use the What-If sliders to simulate changes in spot price, volatility, and time to expiry.",
        "Adjust strikes or quantities until the desired risk profile is achieved.",
        "Click 'Save to Portfolio' to store the strategy for later monitoring.",
        "Click 'Place Order' to execute all legs as a basket order through your broker.",
    ],
    api_endpoints=[
        {"path": "/api/v1/strategy-chart", "method": "POST", "purpose": "Fetch strategy payoff chart data"},
        {"path": "/api/v1/optionchain", "method": "POST", "purpose": "Look up option chain for leg selection"},
        {"path": "/api/v1/strategy/portfolio", "method": "GET", "purpose": "List saved strategies in portfolio"},
        {"path": "/api/v1/trading/placeorder", "method": "POST", "purpose": "Place basket order for strategy legs"},
    ],
    example_scenario=(
        "A trader expects moderate upside in NIFTY and wants limited risk. They select the 'Bull Put Spread' "
        "template, choose weekly expiry, set the short put at 24200 and long put at 24000, and see the "
        "payoff diagram show max profit of ₹1,200 with max risk of ₹800. The what-if analysis confirms "
        "the strategy remains profitable even if NIFTY stays flat. They save it to their portfolio and "
        "monitor it through expiry."
    ),
    configuration=None,
    related_tools=["Strategy Portfolio", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 2. Strategy Portfolio
# ---------------------------------------------------------------------------
Strategy_Portfolio = ToolDoc(
    title="Strategy Portfolio",
    route="/strategybuilder/portfolio",
    color_hex="#8B5CF6",
    overview=(
        "The Strategy Portfolio displays all saved option strategies across MyTrades and Simulation "
        "watchlists. It provides a consolidated view of your constructed strategies, their current "
        "profit/loss status, and quick access to monitoring or execution. Strategies created in the "
        "Strategy Builder are automatically tracked here."
    ),
    key_features=[
        "Unified view of all saved strategies across MyTrades and Simulation watchlists",
        "Real-time P&L tracking for each strategy based on live market prices",
        "Filter strategies by watchlist type (MyTrades vs Simulation)",
        "Quick action buttons to edit, duplicate, or delete strategies",
        "Detailed strategy view showing leg-wise positions and current market status",
    ],
    how_to_use=[
        "Open the Strategy Portfolio from the Strategy Builder page or navigate directly to /strategybuilder/portfolio.",
        "Strategies are organised by watchlist — MyTrades (live) and Simulation (paper trading).",
        "Select a strategy to view its detailed breakdown including leg positions and current P&L.",
        "Use the action buttons to edit the strategy, create a duplicate, or remove it.",
        "Monitor strategy performance over time from this consolidated dashboard.",
    ],
    api_endpoints=[
        {"path": "/api/v1/strategy/portfolio", "method": "GET", "purpose": "Fetch all saved strategies"},
        {"path": "/api/v1/strategy/portfolio", "method": "POST", "purpose": "Create a new strategy entry"},
        {"path": "/api/v1/strategy/portfolio", "method": "DELETE", "purpose": "Delete a saved strategy"},
    ],
    example_scenario=(
        "A trader has constructed five strategies over the past week: three Iron Condors in MyTrades "
        "and two Bull Put Spreads in Simulation. Opening the Strategy Portfolio shows all five with "
        "their current P&L, delta exposure, and days to expiry. The trader spots that one Iron Condor "
        "is approaching max loss and decides to close it early."
    ),
    configuration=None,
    related_tools=["Strategy Builder"],
)

# ---------------------------------------------------------------------------
# 3. Option Chain
# ---------------------------------------------------------------------------
Option_Chain = ToolDoc(
    title="Option Chain",
    route="/optionchain",
    color_hex="#10B981",
    overview=(
        "The Option Chain provides real-time streaming option chain data with live Greeks, Open Interest "
        "visualisation, and quick order placement. It is the central hub for options trading on Quantix, "
        "offering configurable column layouts, ATM strike highlighting, and WebSocket-powered live updates "
        "so traders never miss a price change."
    ),
    key_features=[
        "Real-time WebSocket streaming of option chain data with live bid/ask and last price",
        "Configurable column layout — choose which Greeks and fields to display",
        "Open Interest bars showing CE/PE OI distribution across strikes",
        "ATM strike auto-highlighted for quick reference",
        "One-click quick order placement from any strike row",
        "Symbol search with autocomplete for fast instrument switching",
        "Multiple expiry selection with easy toggle between weekly and monthly expiries",
    ],
    how_to_use=[
        "Navigate to /optionchain and search for your desired underlying symbol (e.g., NIFTY, BANKNIFTY).",
        "Select the expiry date from the available weekly or monthly series.",
        "The option chain loads with all strikes, bid/ask, LTP, IV, and Greeks displayed.",
        "Customise your view using the column configuration menu — show/hide Greeks and calculations.",
        "Use the OI bar chart to identify high-interest strikes where large positions are concentrated.",
        "Click on any strike row to open the quick order dialog and place a trade.",
        "Data updates automatically via WebSocket — no manual refresh needed.",
    ],
    api_endpoints=[
        {"path": "/api/v1/optionchain", "method": "POST", "purpose": "Fetch option chain data with Greeks"},
        {"path": "/api/v1/oiprofile", "method": "POST", "purpose": "Fetch OI profile data for bar chart"},
        {"path": "/api/search", "method": "GET", "purpose": "Symbol search autocomplete"},
    ],
    example_scenario=(
        "A trader monitoring NIFTY options sees the chain update live as the index moves. They spot that "
        "the 24500 CE strike has unusually high Open Interest compared to adjacent strikes. The OI bar "
        "chart confirms a resistance wall at 24500. The trader uses the column config to add Delta and "
        "Gamma columns, confirms high gamma exposure at that strike, and decides to sell the 24500 CE "
        "for a credit."
    ),
    configuration="Symbol search requires the master contract database to be loaded. WebSocket feed requires an active broker session.",
    related_tools=["Strategy Builder", "OI Tracker", "IV Smile"],
)

# ---------------------------------------------------------------------------
# 4. Option Greeks (IV Chart)
# ---------------------------------------------------------------------------
IV_Chart = ToolDoc(
    title="Option Greeks (IV Chart)",
    route="/ivchart",
    color_hex="#8B5CF6",
    overview=(
        "The Option Greeks (IV Chart) tool displays historical charts of Implied Volatility and the "
        "full set of option Greeks — Delta, Gamma, Theta, Vega — for ATM options. These charts "
        "help traders understand how option pricing parameters have evolved over time and identify "
        "trends in volatility and Greek exposure."
    ),
    key_features=[
        "Historical IV chart with CE/PE overlay for comparison",
        "Individual charts for Delta, Gamma, Theta, and Vega",
        "Selectable time ranges (1D, 1W, 1M, 3M, 6M, 1Y)",
        "ATM strike selection with dynamic switching",
        "Lightweight Chart integration for smooth rendering and interaction",
    ],
    how_to_use=[
        "Navigate to /ivchart and select the underlying symbol (e.g., NIFTY, BANKNIFTY).",
        "View the IV chart showing historical implied volatility — toggle between CE and PE overlay.",
        "Switch to individual Greeks (Delta, Gamma, Theta, Vega) using the tab selector.",
        "Adjust the time range using the preset buttons (1W, 1M, 3M, etc.).",
        "Hover over any point on the chart to see the exact value and timestamp.",
        "Use the chart to identify IV trends, volatility spikes, or regime changes.",
    ],
    api_endpoints=[
        {"path": "/api/v1/iv-chart", "method": "POST", "purpose": "Fetch IV and Greeks history data"},
    ],
    example_scenario=(
        "Ahead of a major economic announcement, a trader checks the IV chart for NIFTY. The chart "
        "shows IV has climbed from 12% to 18% over the past week — a typical pre-event volatility "
        "expansion. Theta has also become more negative as expiry approaches. The trader decides to "
        "sell options to capture the elevated IV premium, expecting IV to collapse post-announcement."
    ),
    configuration=None,
    related_tools=["Option Chain", "IV Smile", "Vol Surface"],
)

# ---------------------------------------------------------------------------
# 5. OI Tracker
# ---------------------------------------------------------------------------
OI_Tracker = ToolDoc(
    title="OI Tracker",
    route="/oitracker",
    color_hex="#3B82F6",
    overview=(
        "The OI Tracker provides a focused view of Open Interest across strikes for both Call and "
        "Put options. It displays CE/PE OI bars alongside a Put-Call Ratio (PCR) overlay and marks "
        "the ATM strike, making it easy to identify key support and resistance levels based on "
        "options market positioning."
    ),
    key_features=[
        "CE OI and PE OI displayed as side-by-side bar charts across strikes",
        "Put-Call Ratio (PCR) overlay line for sentiment analysis",
        "ATM strike marker for quick reference",
        "Multi-expiry view support",
        "Automatic data refresh with WebSocket updates",
    ],
    how_to_use=[
        "Open /oitracker and search for your underlying symbol.",
        "Select the expiry date to view OI distribution across strikes.",
        "Blue bars show Call OI (bearish positioning), orange bars show Put OI (bullish positioning).",
        "The PCR line overlays the chart — values above 1 indicate put-heavy sentiment, below 1 indicates call-heavy.",
        "Identify high OI strikes — these act as support (high Put OI) or resistance (high Call OI).",
        "Use the ATM marker to locate the current underlying price relative to OI concentrations.",
    ],
    api_endpoints=[
        {"path": "/api/v1/oi-tracker", "method": "POST", "purpose": "Fetch OI tracker data"},
    ],
    example_scenario=(
        "A trader analysing BANKNIFTY sees that the 48500 Call strike has accumulated massive OI — "
        "far exceeding adjacent strikes. The PCR overlay shows a reading of 0.7, indicating bearish "
        "sentiment. The ATM marker is at 48200, well below the 48500 resistance wall. The trader "
        "concludes that 48500 is a strong resistance and plans to sell calls at that level."
    ),
    configuration=None,
    related_tools=["Option Chain", "OI Range", "OI Profile"],
)

# ---------------------------------------------------------------------------
# 6. OI Range
# ---------------------------------------------------------------------------
OI_Range = ToolDoc(
    title="OI Range",
    route="/oirange",
    color_hex="#0EA5E9",
    overview=(
        "The OI Range tool displays Open Interest data filtered by a customisable strike range. "
        "It provides ATM-relative quick selectors (e.g., ±200 points, ±500 points) and an optional "
        "1-minute auto-refresh mode, making it ideal for monitoring OI changes in a focused "
        "range around the current price during active trading."
    ),
    key_features=[
        "OI by strike within a user-defined range (min/max strike sliders)",
        "ATM-relative quick selectors for instant range adjustment",
        "Optional 1-minute auto-refresh for real-time monitoring",
        "CE/PE OI bar comparison within the selected range",
        "Strike range display with current ATM reference",
    ],
    how_to_use=[
        "Navigate to /oirange and select your underlying symbol and expiry.",
        "Use the range sliders to narrow the view to a specific strike band (e.g., 24000–25000).",
        "Or use the ATM-relative quick buttons — ±100, ±200, ±500 points from ATM.",
        "Enable auto-refresh (1-minute interval) for continuous monitoring during trading.",
        "Observe OI build-up or decay within the selected range in real time.",
    ],
    api_endpoints=[
        {"path": "/api/v1/oiprofile", "method": "POST", "purpose": "Fetch OI profile data for selected range"},
    ],
    example_scenario=(
        "A day trader watching NIFTY near 24200 sets the OI Range to ±200 points from ATM. They "
        "notice Put OI rapidly increasing at 24100 while Call OI is steady at 24300. With auto-refresh "
        "enabled, they see the shift happen in real time and interpret it as put wall building, "
        "suggesting support at 24100. They plan a long position with a stop below 24100."
    ),
    configuration=None,
    related_tools=["OI Tracker", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 7. Max Pain
# ---------------------------------------------------------------------------
Max_Pain = ToolDoc(
    title="Max Pain",
    route="/maxpain",
    color_hex="#F59E0B",
    overview=(
        "The Max Pain tool calculates the strike price at which option buyers collectively experience "
        "maximum financial loss (and option sellers maximum gain) at expiry — known as the Max Pain "
        "strike. It displays the pain distribution chart across all strikes, helping traders "
        "understand where the options market might pin the underlying at expiry."
    ),
    key_features=[
        "Max Pain strike calculation using CE/PE OI across all strikes",
        "Visual pain distribution chart showing total premium decay at each strike",
        "Comparison of current spot price vs Max Pain strike",
        "Multi-expiry view to see how Max Pain shifts across weeks",
        "Historical Max Pain values for context",
    ],
    how_to_use=[
        "Open /maxpain and select the underlying symbol.",
        "Choose the target expiry — Max Pain is most relevant near weekly expiry.",
        "The pain distribution chart displays the total premium loss for option buyers at each strike.",
        "The strike with the highest total premium loss (peak of the curve) is the Max Pain level.",
        "Compare current spot price to Max Pain — convergence suggests pin action.",
        "Monitor how Max Pain shifts intraday as OI changes.",
    ],
    api_endpoints=[
        {"path": "/api/v1/oi-tracker/maxpain", "method": "POST", "purpose": "Fetch Max Pain calculation data"},
    ],
    example_scenario=(
        "On weekly expiry day, a trader checks NIFTY Max Pain and finds it at 24300 with the spot "
        "trading at 24280 — very close to Max Pain. The distribution chart shows sharp pain spikes "
        "at 24200 and 24400. The trader expects the market to pin near 24300 through expiry and "
        "sells strangles at strikes beyond the pain spikes to collect premium with minimal risk."
    ),
    configuration=None,
    related_tools=["OI Tracker", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 8. Straddle Chart
# ---------------------------------------------------------------------------
Straddle_Chart = ToolDoc(
    title="Straddle Chart",
    route="/straddle",
    color_hex="#14B8A6",
    overview=(
        "The Straddle Chart provides a dynamic visualisation of the ATM Straddle price over time, "
        "with rolling strike adjustment, spot price overlay, and Synthetic Futures comparison. "
        "It is designed for traders who actively trade straddles and need to visualise premium "
        "decay, volatility impacts, and synthetic arbitrage opportunities."
    ),
    key_features=[
        "Dynamic ATM Straddle price chart with rolling strike selection",
        "Spot price overlay for direct comparison between straddle value and underlying",
        "Synthetic Futures calculation and overlay",
        "Multi-timeframe view (1D, 1W, 1M, 3M)",
        "Interactive crosshair and zoom for detailed analysis",
    ],
    how_to_use=[
        "Navigate to /straddle and select your underlying symbol.",
        "Choose the expiry for the ATM straddle calculation.",
        "The main chart displays the ATM straddle price history (Call ATM + Put ATM).",
        "Toggle the spot overlay to compare straddle price movement with the underlying.",
        "Enable Synthetic Futures overlay to see the synthetic long/short position value.",
        "Use timeframe selectors to zoom in/out on specific periods.",
    ],
    api_endpoints=[
        {"path": "/api/v1/straddle-chart", "method": "POST", "purpose": "Fetch straddle chart history data"},
    ],
    example_scenario=(
        "A straddle trader notices the ATM straddle on NIFTY has compressed from ₹350 to ₹180 over "
        "five days as expiry approaches. The spot price has been range-bound between 24200 and 24300. "
        "The trader suspects a breakout is imminent given the tight range and cheap straddle premium. "
        "They buy the straddle ahead of the expected move."
    ),
    configuration=None,
    related_tools=["Straddle PnL", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 9. Straddle PnL
# ---------------------------------------------------------------------------
Straddle_PnL = ToolDoc(
    title="Straddle PnL",
    route="/straddlepnl",
    color_hex="#F97316",
    overview=(
        "The Straddle PnL tool simulates intraday profit and loss for an ATM straddle position "
        "with automated N-point adjustments and a trade log. It allows traders to see how a "
        "straddle position would have performed historically on an intraday basis and track "
        "adjustments made to the position over time."
    ),
    key_features=[
        "Intraday P&L simulation for ATM straddle positions",
        "Automated N-point adjustment tracking",
        "Trade log recording entry, adjustments, and exit",
        "Configurable strike selection and position size",
        "Historical replay with date range selection",
    ],
    how_to_use=[
        "Open /straddlepnl and select the underlying symbol.",
        "Set the entry parameters — strike (ATM), position size (lots), and entry date.",
        "The tool simulates the intraday P&L from entry onwards.",
        "N-point adjustments are automatically logged when the straddle is rolled to a new ATM strike.",
        "Review the trade log for timestamps, prices, and P&L at each adjustment point.",
        "Use date range selectors to analyse straddle performance over different market conditions.",
    ],
    api_endpoints=[
        {"path": "/api/v1/custom-straddle", "method": "POST", "purpose": "Fetch straddle P&L simulation data"},
    ],
    example_scenario=(
        "A trader enters a NIFTY ATM straddle at 9:15 AM at ₹280 premium. By 11:00 AM, NIFTY has "
        "moved 100 points and the straddle is worth ₹340. The tool logs this adjustment point. By "
        "1:30 PM, the market reverses and the straddle compresses to ₹220. The P&L chart shows "
        "the full intraday journey, helping the trader decide whether to hold through the afternoon "
        "session or cut the position."
    ),
    configuration=None,
    related_tools=["Straddle Chart", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 10. Vol Surface
# ---------------------------------------------------------------------------
Vol_Surface = ToolDoc(
    title="Vol Surface",
    route="/volsurface",
    color_hex="#F43F5E",
    overview=(
        "The Vol Surface tool presents a three-dimensional visualisation of Implied Volatility "
        "across strikes and expiries. Using live option chain data, it renders an interactive 3D "
        "surface that reveals volatility patterns, term structure, and skew — information that is "
        "invisible in flat option chain views."
    ),
    key_features=[
        "3D IV surface rendered across strikes (X-axis) and expiries (Y-axis)",
        "Interactive rotation, zoom, and hover for data point inspection",
        "Live data from the option chain — updates with market changes",
        "Colour gradient mapping for quick identification of high/low IV regions",
        "Term structure view by slicing the surface at a fixed strike",
    ],
    how_to_use=[
        "Navigate to /volsurface and select your underlying symbol.",
        "The 3D surface renders automatically using live option chain data.",
        "Rotate the view using click-and-drag to examine the surface from different angles.",
        "Hover over any point on the surface to see the exact strike, expiry, and IV value.",
        "Use the strike slice selector to view the IV term structure at a specific strike.",
        "Look for surface distortions — these indicate pricing anomalies or market dislocations.",
    ],
    api_endpoints=[
        {"path": "/api/v1/vol-surface", "method": "POST", "purpose": "Fetch 3D IV surface data"},
    ],
    example_scenario=(
        "During a market event, a volatility trader opens the Vol Surface for BANKNIFTY. The 3D "
        "surface shows a sharp IV spike at near-term expiries for OTM puts — skew is extremely "
        "steep. The surface also shows contango in the term structure for ATM strikes. The trader "
        "uses this information to structure a calendar spread that benefits from the steep "
        "near-term put skew."
    ),
    configuration=None,
    related_tools=["Option Greeks (IV Chart)", "IV Smile", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 11. GEX Dashboard
# ---------------------------------------------------------------------------
GEX_Dashboard = ToolDoc(
    title="GEX Dashboard",
    route="/gex",
    color_hex="#6366F1",
    overview=(
        "The GEX (Gamma Exposure) Dashboard provides comprehensive gamma exposure analysis for "
        "options on an underlying. It displays OI Walls, Net GEX per strike, top gamma strikes, "
        "and aggregated exposure metrics. GEX analysis helps traders understand how market makers' "
        "hedging activity might influence price movement."
    ),
    key_features=[
        "Net Gamma Exposure chart across strikes with positive/negative zones",
        "OI Walls visualisation showing concentrated option interest",
        "Top gamma strikes ranking for quick identification of key levels",
        "Aggregated total GEX with directional breakdown (Call GEX vs Put GEX)",
        "Real-time data from live option chain",
    ],
    how_to_use=[
        "Open /gex and select the underlying symbol (e.g., NIFTY, SENSEX).",
        "The Net GEX chart displays gamma exposure at each strike — green bars (positive), red bars (negative).",
        "Identify strikes with extreme gamma values — these are levels where dealer hedging is strongest.",
        "Review the top gamma strikes table for a ranked list of key levels.",
        "Use the aggregate GEX figure to gauge overall market maker positioning.",
        "Monitor changes intraday as OI shifts and the spot price moves.",
    ],
    api_endpoints=[
        {"path": "/api/v1/gex", "method": "POST", "purpose": "Fetch gamma exposure data"},
    ],
    example_scenario=(
        "A trader checks the GEX Dashboard for NIFTY and sees a massive positive gamma wall at 24400 "
        "(2.5x the next highest level). The aggregate GEX is positive, indicating dealers are long "
        "gamma and will buy dips / sell rips to stay neutral. The trader concludes that the market "
        "is likely to be magnetically drawn toward 24400 and plans trades accordingly."
    ),
    configuration=None,
    related_tools=["Gamma Density", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 12. Gamma Density
# ---------------------------------------------------------------------------
Gamma_Density = ToolDoc(
    title="Gamma Density",
    route="/gammadensity",
    color_hex="#84CC16",
    overview=(
        "The Gamma Density tool visualises Gamma × Open Interest density across strikes, highlighting "
        "convexity zones where gamma exposure is concentrated. It provides both intraday and to-expiry "
        "views, displays the ATM IV, and draws ±1σ and ±2σ expected-move bands based on options "
        "market pricing."
    ),
    key_features=[
        "Gamma × OI density chart showing gamma concentration across strikes",
        "Intraday view and to-expiry view toggle",
        "ATM IV display with historical context",
        "±1σ and ±2σ expected-move bands derived from options market",
        "Convexity zone highlighting for identifying high-gamma regions",
    ],
    how_to_use=[
        "Navigate to /gammadensity and select the underlying symbol and expiry.",
        "The main chart shows Gamma × OI density — peaks indicate high gamma concentration levels.",
        "Toggle between Intraday and To-Expiry views using the selector.",
        "The ATM IV is displayed prominently — note it for implied move calculations.",
        "Expected-move bands (±1σ, ±2σ) are drawn automatically based on ATM IV and time to expiry.",
        "Identify convexity zones where gamma is highest — these are critical hedging levels.",
    ],
    api_endpoints=[
        {"path": "/api/v1/gamma-density", "method": "POST", "purpose": "Fetch gamma density data"},
    ],
    example_scenario=(
        "Ahead of weekly expiry, a trader opens Gamma Density for NIFTY. The density chart shows "
        "a sharp gamma peak at 24300 — the highest Gamma×OI concentration. The ±1σ band spans "
        "24150 to 24450. The trader recognises 24300 as a magnet level where dealer hedging will "
        "be most intense and positions accordingly."
    ),
    configuration=None,
    related_tools=["GEX Dashboard", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 13. IV Smile
# ---------------------------------------------------------------------------
IV_Smile = ToolDoc(
    title="IV Smile",
    route="/ivsmile",
    color_hex="#06B6D4",
    overview=(
        "The IV Smile tool displays the Implied Volatility smile curve — plotting IV against strike "
        "prices for both Calls and Puts. It includes ATM IV marking and skew analysis to help "
        "options traders understand how the market is pricing tail risk and whether out-of-the-money "
        "options are relatively cheap or expensive."
    ),
    key_features=[
        "IV curve for Calls and Puts plotted across strikes",
        "ATM IV marked on the curve with a distinct indicator",
        "Skew analysis — compare Call IV vs Put IV at equivalent deltas",
        "Multiple expiry view for term structure of skew",
        "Interactive hover with exact IV values at each strike",
    ],
    how_to_use=[
        "Open /ivsmile and select the underlying symbol.",
        "The IV smile renders — Call IV (green line) and Put IV (red line) across strikes.",
        "ATM IV is marked with a dashed line for reference.",
        "Hover over any point to see the exact strike and IV value.",
        "Use the skew analysis toggle to view Call-Put IV differences at equivalent deltas.",
        "Select different expiries to see how the smile changes with time to expiry.",
    ],
    api_endpoints=[
        {"path": "/api/v1/iv-smile", "method": "POST", "purpose": "Fetch IV smile data"},
    ],
    example_scenario=(
        "After a sharp market drop, a trader checks the IV Smile for NIFTY. The Put side shows "
        "a steep skew — deep OTM puts have IV of 28% while ATM is at 14%. Calls show much flatter "
        "IV. The skew analysis confirms extreme put premium pricing. The trader sees this as an "
        "opportunity to sell OTM put spreads to capture the elevated skew premium."
    ),
    configuration=None,
    related_tools=["Option Greeks (IV Chart)", "Vol Surface", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 14. OI Profile
# ---------------------------------------------------------------------------
OI_Profile = ToolDoc(
    title="OI Profile",
    route="/oiprofile",
    color_hex="#F97316",
    overview=(
        "The OI Profile tool combines futures candlestick data with an Open Interest butterfly chart "
        "and daily OI change across strikes. This unique combination gives traders a multi-dimensional "
        "view of options market activity — price action, absolute OI levels, and the rate of OI "
        "change — all on a single screen."
    ),
    key_features=[
        "Futures candlestick chart for price context",
        "OI butterfly (CE/PE) distribution across strikes",
        "Daily OI change overlay showing where new positions are being added",
        "Strike range selection for focused analysis",
        "Colour-coded bars — green for OI increase, red for OI decrease",
    ],
    how_to_use=[
        "Navigate to /oiprofile and select the underlying symbol and expiry.",
        "The top section shows the futures candlestick chart for price context.",
        "The OI butterfly chart displays CE/PE OI distribution across strikes.",
        "The daily OI change bars show which strikes are gaining or losing OI — green (additions) and red (reductions).",
        "Use the strike range selector to zoom into specific areas of interest.",
        "Correlate price action on the candlestick chart with OI changes to gauge conviction.",
    ],
    api_endpoints=[
        {"path": "/api/v1/oiprofile", "method": "POST", "purpose": "Fetch OI profile and butterfly data"},
    ],
    example_scenario=(
        "A trader watching NIFTY futures sees a strong bullish candle breaking above 24250. Checking "
        "the OI Profile, they observe heavy Put OI build-up at 24200 (support), while Call OI at "
        "24400 is decreasing. The combination of bullish price action + increasing put support + "
        "call unwinding signals a potential short squeeze. The trader goes long with a target of 24400."
    ),
    configuration=None,
    related_tools=["OI Tracker", "OI Range", "Option Chain"],
)

# ---------------------------------------------------------------------------
# 15. Arbitrage
# ---------------------------------------------------------------------------
Arbitrage = ToolDoc(
    title="Arbitrage",
    route="/arbitrage",
    color_hex="#16A34A",
    overview=(
        "The Arbitrage tool is a real-time futures calendar-spread scanner covering NFO and MCX "
        "segments. It scans all available futures contracts, calculates the percentage spread "
        "between near-month and far-month contracts, and ranks opportunities by executable bid/ask "
        "spread %. Traders can execute two-leg basket orders directly from the scanner results."
    ),
    key_features=[
        "Real-time calendar-spread scanning across NFO and MCX futures",
        "Spreads ranked by percentage for quick opportunity identification",
        "Bid/ask spread filter — only show executable spreads",
        "One-click two-leg basket order placement",
        "Filter by segment (NFO equities, NFO indices, MCX commodities)",
        "Spread history chart for selected pairs",
    ],
    how_to_use=[
        "Open /arbitrage to view the live spread scanner results.",
        "The table lists all calendar-spread pairs ranked by spread percentage.",
        "Filter by segment — NFO (equity index/stock futures) or MCX (commodity futures).",
        "Click on any row to view the spread history chart for that pair.",
        "Set minimum and maximum spread % filters to narrow down opportunities.",
        "When a trade is identified, click 'Place Order' to open the basket order dialog.",
        "The two legs are automatically populated — review and submit the order.",
    ],
    api_endpoints=[
        {"path": "/api/v1/arbitrage", "method": "GET", "purpose": "Fetch arbitrage spread data"},
        {"path": "/api/v1/trading/placeorder", "method": "POST", "purpose": "Place two-leg basket order"},
    ],
    example_scenario=(
        "A trader scans NIFTY futures calendar spreads and finds that the Oct-Nov spread is trading "
        "at 2.1% (annualised ~8.5%), well above the 1.2% average. The bid-ask spread is tight at "
        "0.05%. The trader clicks the row to verify the spread history, confirms it's near the "
        "upper end of its 3-month range, and executes a two-leg basket order — short near-month, "
        "long far-month — to capture the convergence."
    ),
    configuration="Requires futures market data feed for NFO and/or MCX segments. Active broker session required for order placement.",
    related_tools=["Option Chain"],
)

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

TOOLS_DOCS: list[ToolDoc] = [
    Strategy_Builder,
    Strategy_Portfolio,
    Option_Chain,
    IV_Chart,
    OI_Tracker,
    OI_Range,
    Max_Pain,
    Straddle_Chart,
    Straddle_PnL,
    Vol_Surface,
    GEX_Dashboard,
    Gamma_Density,
    IV_Smile,
    OI_Profile,
    Arbitrage,
]

TOOL_NAMES: list[str] = [t.title for t in TOOLS_DOCS]

TOOLS_BY_ROUTE: dict[str, ToolDoc] = {t.route: t for t in TOOLS_DOCS}
