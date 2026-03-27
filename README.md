# Sgt Surge

> **DISCLAIMER: This project is an experiment for educational and research purposes only. It is not financial advice. Trading stocks, options, and other securities involves substantial risk of loss and is not suitable for every investor. Past performance, whether actual or indicated by historical backtests, is not indicative of future results. You could lose some or all of your invested capital. Do not trade with money you cannot afford to lose. The authors and contributors of this project make no representations or warranties regarding the accuracy or completeness of the strategies, code, or information provided. By using this software, you acknowledge that you are solely responsible for your own trading decisions and any resulting financial outcomes. Always consult a qualified financial advisor before making investment decisions.**

Algorithmic momentum day-trading bot targeting low-float stocks on tastytrade. Built for small accounts with aggressive risk management.

## Strategy

**Momentum Surge** -- Ross Cameron-style breakout trading on 5-minute bars.

- **Scanner**: TradingView screener for top gainers, enriched with relative volume and float data
- **Entry**: Price near recent high, above VWAP, RSI 55-90, volume surge > 1.5x average, strong bar close
- **Exit**: 2 consecutive closes below VWAP, RSI collapse (< 40), or price below 10-bar low
- **Stop**: ATR x 2.0 below entry
- **Target**: 3:1 risk/reward
- **Schedule**: Scanning 6:00 AM - 4:00 PM ET, safety net close at 3:55 PM ET
- **Position sizing**: Up to 90% of buying power, max 2% account risk per trade
- **Max trades/day**: 10 (max 2 per symbol)

## Prerequisites

- **Python 3.11+**
- **tastytrade account** -- [Sign up here](https://tastytrade.com/welcome/?referralCode=5VEAT9PR62) (referral link -- helps me out if you use it!)
- **Financial Modeling Prep API key** (free tier, optional) -- for float data enrichment
- **Podman** or **Docker** (for containerized deployment)

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/jacisjake/sgt-surge.git
cd sgt-surge

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# tastytrade OAuth (create an app at https://developer.tastytrade.com)
TT_CLIENT_ID=your_oauth_client_id
TT_CLIENT_SECRET=your_oauth_client_secret
TT_REFRESH_TOKEN=your_refresh_token
TT_ACCOUNT_NUMBER=your_account_number

TRADING_MODE=paper  # or 'live'
```

### 3. Run

```bash
python scripts/run_bot.py
```

Dashboard available at http://localhost:8080

### 4. Deploy to remote server

```bash
cd deploy
./deploy-remote.sh user@host --build
```

## License

MIT
