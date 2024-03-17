import datetime

import pandas as pd
import yfinance as yf
from schemes import inverted_yield_curve_alpha
from graphing import plot_backtest
from utils import print_log


def create_data(start_date, end_date):
    tickers = ['SPY', 'TQQQ', 'SPXL', 'QQQ', 'UVXY', 'SQQQ', 'TLT']
    data_dict = {}

    # Adjust the start_date to fetch data 200 days earlier than the original start_date
    adjusted_start_date = pd.to_datetime(start_date) - pd.DateOffset(days=300)  # Fetch more data than needed

    # Fetch historical data for all tickers, starting from the adjusted start date
    for ticker in tickers:
        data_dict[ticker] = yf.download(ticker, start=adjusted_start_date, end=end_date)

    return data_dict


def evaluate_tickers_over_period(data_dict, scheme):
    results = []
    dates = pd.date_range(start=start_date, end=end_date, freq='D')

    for single_date in dates:
        decision = scheme(single_date, data_dict)
        results.append((single_date, decision))

    return results


def calculate_daily_return(open_price, close_price):
    """Calculate the daily return from open to close price."""
    return (close_price - open_price) / open_price


def backtest_strategy(results, data_dict, starting_account_value):
    account_value = starting_account_value
    account_values = [account_value]

    for date, ticker in results:
        # Check if the date exists in the data_dict for the ticker
        if date in data_dict[ticker].index:
            open_price = data_dict[ticker].loc[date, 'Open']
            close_price = data_dict[ticker].loc[date, 'Close']

            # Calculate the day's return
            daily_return = calculate_daily_return(open_price, close_price)

            print_log(f"Date: {date}, Ticker: {ticker}, Daily Return: {daily_return:.2%}")

            # Update account value
            account_value *= (1 + daily_return)
        else:
            # If the date is not available, use the previous day's return (effectively 0% return for this day)
            print_log(f"Data for {date} not available for {ticker}, assuming 0% return for this day.")

        account_values.append(account_value)

    return account_values


def execute(start_date, end_date, scheme, starting_account_value = 5000):
    # Create a dictionary to store the data for each ticker
    data_dict = create_data(start_date, end_date)

    # Evaluate tickers over the specified period
    results = evaluate_tickers_over_period(data_dict, scheme)

    # Perform backtesting
    account_values = backtest_strategy(results, data_dict, starting_account_value)

    # Plot the backtest results
    plot_backtest(results, account_values)


if __name__ == '__main__':
    starting_account_value = 5000
    end_date = datetime.datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.datetime.now() - datetime.timedelta(days=1095)).strftime('%Y-%m-%d')
    execute(start_date, end_date, inverted_yield_curve_alpha, starting_account_value)



