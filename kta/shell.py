import datetime
from kta.analysis.schemes import inverted_yield_curve_alpha
from kta.analysis.backtest import determine_pick_for_day, execute
from kta.analysis.valuation import valuation, estimate_growth_rate, scanner, split_string_into_tickers
import pandas as pd
import matplotlib.pyplot as plt


def print_scanner_results(undervalued, overvalued):
    print(">>>> Undervalued Stocks:")
    for stock in undervalued:
        print(
            f"{stock['ticker']} with {stock['growth_rate']:.0%} growth rate is undervalued at {stock['current_price']:.2f} USD. Calculated at {stock['per_share_value']:.2f} USD.")

    print("\n>>>> Overvalued Stocks:")
    for stock in overvalued:
        print(
            f"{stock['ticker']} with {stock['growth_rate']:.0%} growth rate is overvalued at {stock['current_price']:.2f} USD. Calculated at {stock['per_share_value']:.2f} USD.")


def graph_scanner(undervalued, overvalued):
    # Convert lists to DataFrame
    df_undervalued = pd.DataFrame(undervalued)
    df_overvalued = pd.DataFrame(overvalued)

    # Concatenate both DataFrames
    df = pd.concat([df_undervalued, df_overvalued])
    df['type'] = ['Undervalued'] * len(df_undervalued) + ['Overvalued'] * len(df_overvalued)

    # Plotting
    fig, ax = plt.subplots(figsize=(10, 6))
    width = 0.35  # the width of the bars

    # Positions for the groups
    ind = range(len(df))

    # Bar for per_share_value
    bars1 = ax.bar(ind, df['per_share_value'], width, label='Per Share Value')

    # Bar for current_price, adjusted for position to appear next to per_share_value bars
    bars2 = ax.bar([p + width for p in ind], df['current_price'], width, label='Current Price')

    # Add some text for labels, title and custom x-axis tick labels, etc.
    ax.set_xlabel('Ticker')
    ax.set_title('Comparison of Per Share Value and Current Price by Stock')
    ax.set_xticks([p + width / 2 for p in ind])
    ax.set_xticklabels(df['ticker'])
    ax.legend()

    # Adding the type of valuation to the graph
    for i, rect in enumerate(bars1):
        ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height(), df['type'].iloc[i],
                ha='center', va='bottom')

    # Show the plot
    plt.show()


def interactive():
    while True:
        ticker = input("Enter a ticker symbol (or 'exit' to quit): ")
        if ticker.lower() == 'exit':
            break

        try:
            growth_or_estimate = input("Enter growth rate (.15 for 15%) or 'e' to estimate: ")
            if growth_or_estimate.lower() == 'e':
                growth_rate = estimate_growth_rate(ticker)
            else:
                growth_rate = float(growth_or_estimate)

            val = valuation(ticker, growth_rate)

            ticker = val['ticker']
            growth_rate = val['growth_rate']
            per_share_value = val['per_share_value']
            implied_pe_ratio = val['implied_pe_ratio']
            pe_ratio = val['pe_ratio']
            current_market_cap = val['current_market_cap']
            implied_market_cap = val['implied_market_cap']
            current_price = val['current_price']

            # Outputs
            print('---')
            print(f'Ticker: {ticker} with {growth_rate:.0%} growth rate')
            if current_price < per_share_value:
                print(f'>>>>{ticker} is undervalued.<<<<')
            print(f'Per Share Value: {per_share_value:.2f} USD')
            print(f'Current Price: {current_price:.2f} USD')
            print(f'Implied P/E Ratio from DCF: {implied_pe_ratio:.2f}')
            print(f'Trailing P/E Ratio: {pe_ratio:.2f}')
            print(f'Current Market Cap: {current_market_cap:.2f} Billion USD')
            print(f'Implied Market Cap from DCF: {implied_market_cap:.2f} Billion USD')
            print('---')

        except ValueError:
            print("Invalid input. Please try again.")


def command_line_interface():
    scheme = input('Select Scheme: 1) Inverted Yield Curve Alpha ')
    if scheme == '1':
        scheme = inverted_yield_curve_alpha
    else:
        print('Invalid scheme')
        exit()

    selection = input("Enter command: 1) Pick for day 2) Backtest 3) Exit: ")
    if selection == '1':
        day = input("Enter date (YYYY-MM-DD) or now for today: ")
        if day == 'now':
            determine_pick_for_day(datetime.datetime.now().strftime('%Y-%m-%d'), scheme)
        else:
            determine_pick_for_day(day, scheme)
    elif selection == '2':
        num_years = input("Enter number of years: ")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=(365 * int(num_years)) + 1)).strftime(
            '%Y-%m-%d')
        end_date = datetime.datetime.now().strftime('%Y-%m-%d')
        execute(start_date, end_date, scheme)
    elif selection == '3':
        print("Exit")
    else:
        print("Invalid command")


if __name__ == '__main__':
    action = input("Select action: 1) Scanner 2) Valuation 3) Backtest 4) Exit: ")
    if action == '1':
        if input("Run scanner with custom tickers? (y/n): ").lower() == 'y':
            stocks_to_check = split_string_into_tickers(input("Enter tickers separated by commas: "))
            undervalued, overvalued = scanner(stocks_to_check)
        else:
            undervalued, overvalued = scanner([
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'BRK-B', 'JPM', 'JNJ', 'V', 'WMT', 'PG', 'MA', 'DIS',
                'NVDA'
            ])

        if input("Graph results? (y/n): ").lower() == 'y':
            graph_scanner(undervalued, overvalued)
        else:
            print_scanner_results(undervalued, overvalued)
    elif action == '2':
        interactive()
    elif action == '3':
        command_line_interface()
    elif action == '4':
        exit()
    else:
        print("Invalid action")
        exit()




