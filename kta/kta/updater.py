import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from django.shortcuts import render

from .decorators import log_io
from .models import Stock, Valuation, GrowthEstimate, CompanyTerm
from django.http import request
from .analysis import estimate_growth_rate, get_primary_terms
from .analysis import valuation as val
import datetime
from django.http import HttpResponse
from .analysis import get_exchange_assets
from django.db import models

@log_io
def update_stock(stock):
    # Do we have terms for the stock?
    terms = CompanyTerm.objects.filter(stock=stock)

    # If not make some
    if not terms:
        terms = get_primary_terms(stock.ticker)
        for term in terms:
            term = CompanyTerm(stock=stock, term=term)
            term.save()
    else:
        terms = [term.term for term in terms]

    # Do we have a growth rate for today?
    growth_estimate = GrowthEstimate.objects.filter(stock=stock, date=datetime.date.today()).first()

    # If not make one
    if not growth_estimate:
        growth_rate = estimate_growth_rate(stock.ticker, terms)
        growth_estimate = GrowthEstimate(stock=stock, date=datetime.date.today(), **growth_rate)
        growth_estimate.save()

    # Do we have a valuation for today?
    valuation = Valuation.objects.filter(stock=stock, date=datetime.date.today()).first()

    # If not make one
    if not valuation:
        data = val(stock.ticker, growth_estimate.growth_rate)
        # Remove ticker from data
        data.pop('ticker')

        valuation = Valuation(stock=stock, date=datetime.date.today(), **data)
        valuation.save()

    return {
        'stock': stock,
        'terms': terms,
        'growth_estimate': growth_estimate,
        'valuation': valuation
    }

@log_io
def update_all_stocks():
    logging.info("Updating all stocks")
    stocks = Stock.objects.filter(status='Active', asset_type='Stock', exchange__in=['NASDAQ', 'NYSE'])
    for stock in stocks:
        update_stock(stock)


@log_io
def add_or_update_stock_list():
    assets = get_exchange_assets()

    def i_not_null(i): return i if i != 'null' else None

    for item in assets:
        try:
            print(f'Adding stock with data: {item}')
            stock, created = Stock.objects.update_or_create(
                ticker=item['symbol'],
                defaults={
                    'name': item['name'],
                    'exchange': item['exchange'],
                    'asset_type': i_not_null(item['assetType']),
                    'ipo_date': i_not_null(item['ipoDate']),
                    'delisting_date': i_not_null(item['delistingDate']),
                    'status': i_not_null(item['status']),
                }
            )
            if created:
                print(f"Added new stock: {stock}")
            else:
                print(f"Updated stock: {stock}")
        except Exception as e:
            print(f"Error adding stock {item['symbol']}: {e}")


def remove_duplicate_tickers():
    tickers = Stock.objects.values('ticker').annotate(count_ticker=models.Count('ticker')).filter(
        count_ticker__gt=1)
    for ticker in tickers:
        stocks = Stock.objects.filter(ticker=ticker['ticker'])
        for stock in stocks[1:]:
            stock.delete()

def remove_all_terms():
    CompanyTerm.objects.all().delete()


def start_scheduler():
    print("Starting scheduler")
    # remove_all_terms()
    # remove_duplicate_tickers()
    # add_or_update_stock_list()

    scheduler = BackgroundScheduler()
    scheduler.add_job(add_or_update_stock_list, 'interval', hours=24)
    # scheduler.add_job(update_all_stocks, 'interval', hours=24)
    scheduler.start()
