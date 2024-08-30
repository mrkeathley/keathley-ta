import yfinance as yf
import numpy as np
from scipy.stats import linregress
import pandas as pd
import matplotlib.pyplot as plt
from pytrends.request import TrendReq
import requests
import re
from openai import OpenAI
import json
import warnings
from ..settings import OPENAI_API_KEY


def fetch_primary_terms(ticker):
    chatGPT = OpenAI(api_key=OPENAI_API_KEY)

    messages = [
        {
            "role": "system",
            "content": """
                You are a data analyst API capable of fetching primary terms for a given stock ticker. 
                You will be given a single ticker symbol and should return the products, services, or features that the company is most known for. Ideally these are products with trademarked names.
                Return the top 3-5 most relevant.
                These cannot be general terms. For example, "technology" is too broad for Apple. Apple could include: iPhone, iPad, Mac, MacBook, etc.

                Please respond with you analysis directly in JSON format. (without the use of Markdown code blocks or any other formatting) ONLY include JSON. No other text.
                The JSON schema should be as follows:
                {
                    "ticker": "string",
                    "primary_terms": ["string"]
                }
                """
        },
        {
            "role": "user",
            "content": ticker
        }
    ]

    response = chatGPT.chat.completions.create(
        messages=messages,
        model="gpt-4"
    )
    return response.choices[0].message.content


def yahoo_growth_rate(ticker):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
        'Referer': f'https://finance.yahoo.com/quote/{ticker}?p={ticker}',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'DNT': '1',  # Do Not Track Requests header
        'Upgrade-Insecure-Requests': '1'
    })

    # Get the page and cookies
    r = session.get(f'https://finance.yahoo.com/quote/{ticker}?p={ticker}')
    html_content = r.text

    # Extract crumb from the page
    pattern = r'"crumb":"(.*?)"'
    match = re.search(pattern, html_content)
    if match:
        crumb = match.group(1)
    else:
        raise ValueError("Crumb not found in the page.")

    # Now make a request to the API with the crumb
    url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=earningsTrend&lang=en-US&region=US&crumb={crumb}'
    response = session.get(url)
    if response.status_code == 200:
        data = response.json()
        earnings_trend = data['quoteSummary']['result'][0]['earningsTrend']
        if earnings_trend['trend'][4]['period'] == '+5y':
            five_year_growth = earnings_trend['trend'][4]['growth']
        elif earnings_trend['trend'][1]['period'] == '+1q':
            one_quarter_growth = earnings_trend['trend'][1]['growth']
            five_year_growth = one_quarter_growth * 4
        else:
            return None
        return five_year_growth
    return None


def zacks_rank(ticker):
    url = f"https://quote-feed.zacks.com/index?t={ticker}"

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json().get(ticker, {})
        if data:
            zacks_rank = data.get('zacks_rank', -1)
            return zacks_rank
    return -1  # Default to -1 if data is missing


def google_trends_growth_rate(keyword):
    print(f"Fetching Google Trends data for {keyword}")
    pytrends = TrendReq(hl='en-US', tz=360)
    pytrends.build_payload([keyword], cat=0, timeframe='today 5-y', geo='', gprop='')

    try:
        trends_data = pytrends.interest_over_time()
        # Simplistic growth estimate: change in interest
        if len(trends_data) > 1:
            trend_growth = (trends_data[keyword].iloc[-1] - trends_data[keyword].iloc[0]) / trends_data[keyword].iloc[0]
            return trend_growth
        return -1
    except Exception as e:
        print(f"Error fetching Google Trends data: {e}")
        return -1


def historical_growth_rate(ticker):
    # Fetch stock historical data
    stock = yf.Ticker(ticker)
    data = stock.history(period="5y")  # Fetch 5 years of stock price data

    if data.empty:
        print("No historical price data available.")
        return None

    # Use 'Close' prices to estimate growth
    data = data['Close'].resample('YE').last()  # Annual resampling by taking the last price each year

    # Prepare data for regression
    years = np.arange(len(data))
    log_prices = np.log(data.values)

    # Perform linear regression on time and log(prices)
    slope, intercept, r_value, p_value, std_err = linregress(years, log_prices)

    # Convert slope to annual growth rate
    annual_growth_rate = np.exp(slope) - 1  # Convert log slope back to normal scale

    # Print the growth rate as a percentage
    return annual_growth_rate


def get_primary_terms(ticker):
    terms = fetch_primary_terms(ticker)
    # Convert to dictionary
    terms = json.loads(terms)['primary_terms']

    print(f'Primary terms for {ticker}: {terms}')
    return terms


def estimate_growth_rate(ticker, terms=None):
    if terms is None:
        terms = get_primary_terms(ticker)

    google_growth = []
    for term in terms:
        print(f'Estimating growth rate for {term}')
        rate = google_trends_growth_rate(term)

        # Filter -1 and NaN
        if rate == -1 or np.isnan(rate) or np.isinf(rate):
            continue

        google_growth.append(rate)
    print(f'Google Trends Growth Rates for {ticker}: {google_growth}')
    if len(google_growth) == 0:
        google_growth = [.05]
    # Average Google Trends
    average_growth = sum(google_growth) / len(google_growth)
    print(f'Average Google Trends Growth Rate for {ticker}: {average_growth}')

    # Get Yahoo Finance growth rate
    yahoo_growth = yahoo_growth_rate(ticker)
    print(f'Yahoo Finance object for {ticker}: {yahoo_growth}')
    # Check if yahoo is empty dictionary
    if not yahoo_growth:
        yahoo_growth = .05  # Default to 5% if data is missing
    else:
        yahoo_growth = yahoo_growth['raw']
    print(f'Yahoo Finance Growth Rate for {ticker}: {yahoo_growth}')

    # Get Zacks Rank
    # zacks = zacks_rank(ticker)
    # print(f'Zacks Rank for {ticker}: {zacks}')

    # Get Historical Growth Rate
    historical = historical_growth_rate(ticker)
    print(f'Historical Growth Rate for {ticker}: {historical}')

    # Estimate Growth with Weights
    trend_weight = 0.3
    yahoo_weight = 0.5
    historical_weight = 0.2

    # Weighted average of growth rates
    estimated_growth = (trend_weight * average_growth) + (yahoo_weight * yahoo_growth) + (
                historical_weight * historical)
    return {
        'growth_rate': estimated_growth,
        'historical_rate': historical,
        'yahoo_estimate': yahoo_growth,
        'google_trend_rate': average_growth
    }


def valuation(ticker, growth_rate=0.15, terminal_growth_rate=0.04, discount_rate=0.12, years_forecast=5):
    stock = yf.Ticker(ticker)

    # Check if stock is valid ticker
    if stock.history(period="1d").empty:
        print(f"Invalid ticker: {ticker}")
        return None

    current_price = stock.history(period="1d")['Close'].iloc[0]

    # Get stock info and financials
    info = stock.info
    financials = stock.financials.T

    # Safe retrieval of data with default values
    eps = info.get('trailingEps', 0)  # Default EPS to 0 if not found
    pe_ratio = info.get('trailingPE', 0)  # Default P/E to 0 if not found
    shares_outstanding = info.get('sharesOutstanding', 1)

    # Normalize shares as percent of company per million shares
    normalized_shares_outstanding = shares_outstanding / 1e6  # Convert to millions
    normalized_shares_outstanding = (1 / normalized_shares_outstanding) * 100

    shares_outstanding /= 1e9  # Convert to billions

    # Current Market Cap
    current_market_cap = eps * pe_ratio * shares_outstanding

    ebitda = 0
    interest_expense = 0
    tax_provision = 0
    # Attempt to fetch Free Cash Flow
    try:
        ebitda = financials.iloc[0]['EBITDA']
        interest_expense = financials.iloc[0]['Interest Expense']
        tax_provision = financials.iloc[0]['Tax Provision']
        fcf_initial = ebitda - interest_expense - tax_provision
        fcf_initial /= 1e9  # Convert to billion dollars
    except KeyError:
        fcf_initial = eps * shares_outstanding  # Default to EPS * Shares if FCF data is missing
        print(f"Key data for FCF calculation is missing for {ticker}. Using default value of 0.")

    # Calculations as before
    FCFs = [fcf_initial * (1 + growth_rate) ** t for t in range(1, years_forecast + 1)]
    PV_FCFs = [FCF / ((1 + discount_rate) ** t) for t, FCF in enumerate(FCFs, start=1)]
    sum_PV_FCFs = sum(PV_FCFs)
    terminal_value = (FCFs[-1] * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate))
    PV_terminal_value = terminal_value / ((1 + discount_rate) ** years_forecast)
    total_DCF_value = sum_PV_FCFs + PV_terminal_value

    # Let's Check if we had any issues
    if np.isnan(total_DCF_value) or np.isinf(total_DCF_value):
        print(f"Error calculating DCF value for {ticker}. Check data and parameters.")
        total_DCF_value = current_market_cap + (current_market_cap * growth_rate)

    per_share_value = total_DCF_value / shares_outstanding
    # Implied market cap from DCF and compare with current market cap
    implied_market_cap = per_share_value * shares_outstanding
    implied_pe_ratio = implied_market_cap / (eps * shares_outstanding)

    return {
        'ticker': ticker,
        'growth_rate': growth_rate,
        'per_share_value': per_share_value,
        'current_price': current_price,
        'implied_pe_ratio': implied_pe_ratio,
        'pe_ratio': pe_ratio,
        'current_market_cap': current_market_cap,
        'implied_market_cap': implied_market_cap,
        'shares_outstanding': shares_outstanding,
        'normalized_shares_outstanding': normalized_shares_outstanding,
        'terminal_growth_rate': terminal_growth_rate,
        'discount_rate': discount_rate,
        'ebitda': ebitda,
        'interest_expense': interest_expense,
        'tax_provision': tax_provision,
        'eps': eps,
        'free_cash_flow': fcf_initial,
        'dcf': total_DCF_value
    }


def split_string_into_tickers(string):
    return [ticker.strip() for ticker in string.split(',')]


def scanner(stocks_to_check=None):
    if stocks_to_check is None:
        stocks_to_check = ['AAPL']

    overvalued = []
    undervalued = []

    for stock in stocks_to_check:
        growth_rate = yahoo_growth_rate(stock)
        val = valuation(stock, growth_rate)
        if not val:
            continue
        ticker = val['ticker']
        growth_rate = val['growth_rate']
        per_share_value = val['per_share_value']
        current_price = val['current_price']

        if current_price < per_share_value:
            undervalued.append({
                'ticker': ticker,
                'growth_rate': growth_rate,
                'per_share_value': per_share_value,
                'current_price': current_price
            })
        else:
            overvalued.append({
                'ticker': ticker,
                'growth_rate': growth_rate,
                'per_share_value': per_share_value,
                'current_price': current_price
            })

    return undervalued, overvalued

