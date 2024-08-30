from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.shortcuts import render
from django.db import models
from .models import Stock, Valuation, GrowthEstimate, CompanyTerm
from .updater import update_stock
import datetime
from django.http import HttpResponse

def data_load(request):
    stock_list = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'BRK-B', 'JPM', 'JNJ', 'V', 'WMT', 'PG', 'MA', 'DIS',
        'NVDA'
    ]
    for stock in stock_list:
        stock = Stock(ticker=stock)
        stock.save()
        update_stock(stock)
    # Return text ok
    return HttpResponse('ok')


def undervalued_stock_list(request):
    initial_filters = {
        'stock__status': 'Active',
        'stock__asset_type': 'Stock',
        'stock__exchange__in': ['NASDAQ', 'NYSE'],
        # Filter for only valuations with per share value greater than current price
        'per_share_value__gt': models.F('current_price')
    }

    # Search handling
    query = request.GET.get('q')
    if query:
        initial_filters['stock__ticker__icontains'] = query

    valuations = Valuation.objects.filter(date=datetime.date.today(), **initial_filters).order_by('stock__ticker')
    stocks = [valuation.stock for valuation in valuations]

    # Pagination
    paginator = Paginator(stocks, 25)
    page = request.GET.get('page')
    try:
        stocks = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        stocks = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver last page of results.
        stocks = paginator.page(paginator.num_pages)

    return render(request, 'undervalued_list.html', {'stocks': stocks})


def stock_list(request):
    initial_filters = {
        'status': 'Active',
        'asset_type': 'Stock',
        'exchange__in': ['NASDAQ', 'NYSE'],
    }

    # Search handling
    query = request.GET.get('q')
    if query:
        initial_filters['ticker__icontains'] = query

    stocks = Stock.objects.filter(**initial_filters).order_by('ticker')

    # Pagination
    paginator = Paginator(stocks, 15)
    page = request.GET.get('page')
    try:
        stocks = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        stocks = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver last page of results.
        stocks = paginator.page(paginator.num_pages)

    return render(request, 'stock_list.html', {'stocks': stocks})


def stock_home(request, ticker):
    # Check if Stock exists
    stock = Stock.objects.filter(ticker=ticker).first()

    # Create if it doesn't exist
    if not stock:
        stock = Stock(ticker=ticker)
        stock.save()

    # Update stock
    update_stock(stock)

    # Check if valuations exist
    valuations = Valuation.objects.filter(stock=stock)

    # Get Terms and Growth Rates
    company_terms = CompanyTerm.objects.filter(stock=stock)
    growth_estimates = GrowthEstimate.objects.filter(stock=stock)

    return render(request, 'stock_home.html', {'stock': stock, 'valuations': valuations, 'company_terms': company_terms, 'growth_estimates': growth_estimates})
