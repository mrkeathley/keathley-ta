import datetime

import matplotlib.patches as mpatches
import matplotlib.dates
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import logging


def plot_backtest(results, account_values):
    # Calculate the total cumulative return percentage
    initial_value = account_values[0]
    final_value = account_values[-1]
    cumulative_return_percent = ((final_value - initial_value) / initial_value) * 100

    # Get the latest ticker symbol
    latest_ticker = results[-1][1]

    # Start with creating a single figure for plotting
    plt.figure(figsize=(14, 8))

    # Plot ticker selections as colored bars
    dates, tickers = zip(*results)  # Unzip the results into separate lists
    dates_num = matplotlib.dates.date2num(dates)  # Convert dates to numerical format for plotting

    # Generate a unique color for each ticker
    unique_tickers = list(set(tickers))
    colors = plt.cm.tab10(np.linspace(0, 1, len(unique_tickers)))
    color_map = dict(zip(unique_tickers, colors))

    # Plot each date as a vertical bar with color based on the ticker
    for i, ticker in enumerate(tickers):
        plt.bar(dates_num[i], 1, width=1, color=color_map[ticker], align='center')

    # Adjust x-axis to show dates
    plt.gca().xaxis_date()
    plt.xticks(rotation=45)

    # Hide the y-axis labels and ticks for the ticker selection
    plt.yticks([])

    # Use twinx() to create a secondary y-axis for account values
    ax2 = plt.twinx()

    # Plot account values on the secondary y-axis
    dates_num_account_values = np.append(dates_num, dates_num[-1] + 1)  # Ensure alignment with account_values length
    ax2.plot_date(dates_num_account_values, account_values, '-', label='Account Value', color='k')

    # Set the label for the secondary y-axis
    ax2.set_ylabel('Account Value')

    # Adjust here to add custom legend items
    legend_tickers = [mpatches.Patch(color=color_map[ticker], label=ticker) for ticker in unique_tickers]
    legend_account_value = [mpatches.Patch(color='black', label='Account Value')]

    # Additional legend items for the latest ticker and total return
    legend_latest_ticker = [mpatches.Patch(color='none', label=f'Latest: {latest_ticker}')]
    legend_cumulative_return = [mpatches.Patch(color='none', label=f'Return: {cumulative_return_percent:.2f}%')]

    # Combine all legend items
    all_legend_items = legend_tickers + legend_account_value + legend_latest_ticker + legend_cumulative_return

    # Create a combined legend
    plt.legend(handles=all_legend_items, bbox_to_anchor=(1.05, 1), loc='upper left', title="Legend")

    plt.title('Ticker Selection and Account Value Over Time')
    plt.tight_layout()
    plt.show()
