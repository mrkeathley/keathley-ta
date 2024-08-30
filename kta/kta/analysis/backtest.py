import datetime

import pandas as pd
import yfinance as yf
from .graphing import plot_backtest
from logging import info as print_log


def create_data(start_date, end_date):
    tickers = ['SPY', 'TQQQ', 'SPXL', 'QQQ', 'UVXY', 'SQQQ', 'TLT']
    data_dict = {}

    # Adjust the start_date to fetch data 200 days earlier than the original start_date
    adjusted_start_date = pd.to_datetime(start_date) - pd.DateOffset(days=300)  # Fetch more data than needed

    # Fetch historical data for all tickers, starting from the adjusted start date
    for ticker in tickers:
        data_dict[ticker] = yf.download(ticker, start=adjusted_start_date, end=end_date)

    return data_dict


def evaluate_tickers_over_period(start_date, end_date, data_dict, scheme):
    results = []
    dates = pd.date_range(start=start_date, end=end_date, freq='D')

    for single_date in dates:
        decision = scheme(single_date, data_dict)
        results.append((single_date, decision))

    return results


def calculate_daily_return(previous_close, current_close):
    """Calculate the daily return based on the previous day's close and the current day's close."""
    return (current_close - previous_close) / previous_close


def backtest_strategy(results, data_dict, starting_account_value, holdings_info):
    account_value = starting_account_value
    account_values = [account_value]
    previous_close_prices = {}  # To store the previous close price for each ticker
    last_date_used = {}  # To store the last date a ticker was used

    holdings_metrics = []  # To track holdings metrics

    holding_ticker, shares, purchase_price, purchase_date = holdings_info
    holdings_value = shares * purchase_price  # Initial value of holdings

    for date, ticker in results:
        current_date = pd.to_datetime(date)

        # Check if ticker needs re-initialization (first occurrence or more than 3 days old)
        if ticker not in last_date_used or (current_date - last_date_used[ticker]).days > 3:
            # Find the most recent close price strictly before this date
            recent_data_prior = data_dict[ticker].loc[:date].iloc[:-1]  # Exclude the current date
            if not recent_data_prior.empty:
                previous_close_prices[ticker] = recent_data_prior['Close'].iloc[-1]
            else:
                # If there's absolutely no data before this date, skip this ticker for now
                continue

        if date in data_dict[ticker].index:
            if date >= purchase_date:
                current_price = data_dict[ticker].loc[date, 'Close']
                holdings_value = shares * current_price  # Update value of holdings

            # Calculate growth percent of holdings from purchase
            growth_percent = ((holdings_value - (shares * purchase_price)) / (shares * purchase_price)) * 100

            # Determine if it's time to sell based on the scheme
            sell_signal = ticker != holding_ticker  # Assuming a sell signal if the selected ticker is different

            holdings_metrics.append((date, holdings_value, growth_percent, sell_signal))

            close_price = data_dict[ticker].loc[date, 'Close']
            previous_close = previous_close_prices[ticker]

            # Calculate the day's return based on the previous close
            daily_return = (close_price - previous_close) / previous_close

            print_log(f"Date: {date}, Ticker: {ticker}, Previous: {previous_close}, Close: {close_price}, Daily Return: {daily_return:.2%}")

            # Update the account value
            account_value *= (1 + daily_return)

            # Update tracking information
            previous_close_prices[ticker] = close_price
            last_date_used[ticker] = current_date
        else:
            # If the date is not available (e.g., weekend), assume 0% return for this day
            print(f"Data for {date} not available for {ticker}, assuming 0% return for this day.")

        account_values.append(account_value)

    return account_values, holdings_metrics


def execute(start_date, end_date, scheme):
    # Create a dictionary to store the data for each ticker
    data_dict = create_data(start_date, end_date)

    # Evaluate tickers over the specified period
    results = evaluate_tickers_over_period(start_date, end_date, data_dict, scheme)

    # Perform backtesting
    account_values, holdings_metrics = backtest_strategy(results, data_dict, 1000, ('TQQQ', 40, 59.77, datetime.datetime.strptime('2024-03-19', '%Y-%m-%d')))

    # Plot the backtest results
    plot_backtest(results, account_values)


def determine_pick_for_day(date, scheme):
    # Create a dictionary to store the data for each ticker
    data_dict = create_data(date, date)

    # Evaluate tickers over the specified period
    results = evaluate_tickers_over_period(date, date, data_dict, scheme)

    for d, ticker in results:
        print(f'The ticker for {d} is {ticker}')





