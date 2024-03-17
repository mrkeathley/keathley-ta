import logging
import pandas as pd
import numpy as np

def print_log(message):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.info(message)


def moving_average(data, window):
    # Check if there's enough data to calculate the moving average
    if len(data) >= window:
        return data.rolling(window=window).mean()
    else:
        # Optionally, handle cases where there's insufficient data
        print(f"Warning: Not enough data to calculate {window}-day moving average.")
        return pd.Series([np.nan] * len(data), index=data.index)


def relative_strength_idx(data, window=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    # Use EMA for average gain and loss
    avg_gain = gain.ewm(com=window - 1, min_periods=window).mean()
    avg_loss = loss.ewm(com=window - 1, min_periods=window).mean()

    RS = avg_gain / avg_loss
    RSI = 100 - (100 / (1 + RS))

    return RSI


def cumulative_return(data, days=1):
    """
    Calculate the cumulative return over 'days' period.

    Parameters:
    - data: Pandas Series with the price data.
    - days: Integer representing the period over which to calculate the return.

    Returns:
    - Cumulative return as a percentage.
    """
    if len(data) >= days:
        initial_value = data.iloc[-days]
        final_value = data.iloc[-1]
        cumulative_return_pct = ((final_value / initial_value) - 1) * 100
        return cumulative_return_pct
    else:
        return None  # Insufficient data


def std_dev_of_return(data, window):
    """
    Calculate the standard deviation of daily returns over the last N days manually.

    Parameters:
    - data: Pandas Series with the price data.
    - window: Integer, the number of days to consider for the calculation.

    Returns:
    - Standard deviation of the returns over the specified window.
    """
    # Step 1: Compute daily percent returns and select the last N days
    daily_returns = data.pct_change().dropna()[-window:]

    # Calculate the average (mean) return
    mean_return = daily_returns.mean()

    # Step 2: Find the square of the difference between the return and the mean
    differences_squared = (daily_returns - mean_return) ** 2

    # Step 3: Sum these squared differences
    sum_of_squares = differences_squared.sum()

    # Step 4: Divide by the number of observations minus one to get variance
    variance = sum_of_squares / (len(daily_returns) - 1)

    # Step 5: Take the square root to get standard deviation
    std_dev = variance ** 0.5

    # No annualization step here, as you didn't specify it in your steps, but it can be added if needed
    return std_dev

