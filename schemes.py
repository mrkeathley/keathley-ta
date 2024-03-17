from utils import print_log, moving_average, relative_strength_idx, cumulative_return, std_dev_of_return


# Decision tree for the inverted yield curve alpha strategy
def inverted_yield_curve_alpha(date, data_dict):
    spy_data = data_dict['SPY'].loc[:date]
    tqqq_data = data_dict['TQQQ'].loc[:date]
    spxl_data = data_dict['SPXL'].loc[:date]
    qqq_data = data_dict['QQQ'].loc[:date]
    uvxy_data = data_dict['UVXY'].loc[:date]
    sqqq_data = data_dict['SQQQ'].loc[:date]
    tlt_data = data_dict['TLT'].loc[:date]

    # Current prices (assuming the last price in the dataset is the current price)
    current_price_spy = spy_data['Open'].iloc[-1]
    current_price_tqqq = tqqq_data['Open'].iloc[-1]

    # Calculating indicators
    spy_200d_ma = moving_average(spy_data['Open'], 200).iloc[-1]
    tqqq_10d_rsi = relative_strength_idx(tqqq_data['Open'], 10).iloc[-1]
    spxl_10d_rsi = relative_strength_idx(spxl_data['Open'], 10).iloc[-1]
    qqq_5d_cum_return = cumulative_return(qqq_data['Open'], 5)
    tqqq_1d_cum_return = cumulative_return(tqqq_data['Open'], 1)
    qqq_10d_rsi = relative_strength_idx(qqq_data['Open'], 10).iloc[-1]
    tqqq_10d_std_dev = std_dev_of_return(tqqq_data['Open'], 10)
    tqqq_20d_ma = moving_average(tqqq_data['Open'], 20).iloc[-1]
    sqqq_10d_rsi = relative_strength_idx(sqqq_data['Open'], 10).iloc[-1]
    spy_10d_rsi = relative_strength_idx(spy_data['Open'], 10).iloc[-1]
    uvxy_10d_rsi = relative_strength_idx(uvxy_data['Open'], 10).iloc[-1]
    tlt_10d_rsi = relative_strength_idx(tlt_data['Open'], 10).iloc[-1]

    # Decision tree
    print_log(f'Is current price of SPY ({current_price_spy}) greater than 200-day moving average ({spy_200d_ma})? ')
    if current_price_spy > spy_200d_ma:
        print_log(f'Is 10-day RSI of TQQQ ({tqqq_10d_rsi}) greater than 79? ')
        if tqqq_10d_rsi > 79:
            return 'UVXY'
        else:
            print_log(f'Is 10-day RSI of SPXL ({spxl_10d_rsi}) greater than 80? ')
            if spxl_10d_rsi > 80:
                return 'UVXY'
            else:
                print_log(f'Is 5-day cumulative return of QQQ ({qqq_5d_cum_return}) less than -6? ')
                if qqq_5d_cum_return < -6:
                    print_log(f'Is 1-day cumulative return of TQQQ ({tqqq_1d_cum_return}) greater than 5? ')
                    if tqqq_1d_cum_return > 5:
                        return 'SQQQ'
                    else:
                        print_log(f'Is 10-day RSI of TQQQ ({qqq_10d_rsi}) greater than 31? ')
                        if tqqq_10d_rsi > 31:
                            return 'SQQQ'
                        else:
                            return 'TQQQ'
                else:
                    print_log(f'Is 10-day RSI of QQQ ({qqq_10d_rsi}) greater than 80? ')
                    if qqq_10d_rsi > 80:
                        return 'SQQQ'
                    else:
                        print_log(f'Is 10-day standard deviation of TQQQ return ({tqqq_10d_std_dev}) greater than 0.05? ')
                        if tqqq_10d_std_dev > 0.05:
                            return 'TLT'
                        else:
                            return 'TQQQ'
    else:
        print_log(f'Is 10-day RSI of TQQQ ({tqqq_10d_rsi}) less than 31? ')
        if tqqq_10d_rsi < 31:
            return 'TQQQ'
        else:
            print_log(f'Is 10-day RSI of SPY ({spy_10d_rsi}) less than 30? ')
            if spy_10d_rsi < 30:
                return 'TQQQ'
            else:
                print_log(f'Is 10-day RSI of UVXY ({uvxy_10d_rsi}) greater than 74? ')
                if uvxy_10d_rsi > 74:
                    print_log(f'Is 10-day RSI of UVXY ({uvxy_10d_rsi}) greater than 84? ')
                    if uvxy_10d_rsi > 84:
                        # Sorting by RSI and returning the top ticker
                        print_log(f'Is 10-day RSI of SQQQ ({sqqq_10d_rsi}) greater than 10-day RSI of TLT ({tlt_10d_rsi})? ')
                        if sqqq_10d_rsi > tlt_10d_rsi:
                            return 'SQQQ'
                        else:
                            return 'TLT'
                    else:
                        return 'UVXY'
                else:
                    print_log(f'Is current price of TQQQ ({current_price_tqqq}) greater than 20-day moving average ({tqqq_20d_ma})? ')
                    if current_price_tqqq > tqqq_20d_ma:
                        print_log(f'Is 10-day RSI of SQQQ ({sqqq_10d_rsi}) less than 31? ')
                        if sqqq_10d_rsi < 31:
                            return 'SQQQ'
                        else:
                            return 'TQQQ'
                    else:
                        # Sorting by RSI and returning the top ticker
                        print_log(f'Is 10-day RSI of SQQQ ({sqqq_10d_rsi}) greater than 10-day RSI of TLT ({tlt_10d_rsi})? ')
                        if sqqq_10d_rsi > tlt_10d_rsi:  # Assuming tlt_10d_rsi calculation
                            return 'SQQQ'
                        else:
                            return 'TLT'
