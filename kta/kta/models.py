from django.db import models
import datetime


class Stock(models.Model):
    ticker = models.CharField(max_length=10, primary_key=True)
    name = models.CharField(max_length=100)
    exchange = models.CharField(max_length=10, default='NYSE')
    asset_type = models.CharField(max_length=10, default='Stock')
    ipo_date = models.DateField(blank=True, null=True)
    delisting_date = models.DateField(blank=True, null=True)
    industry = models.CharField(max_length=100, blank=True, null=True)
    ceo = models.CharField(max_length=100, blank=True, null=True)
    headquarters_location = models.CharField(max_length=150, blank=True, null=True)
    status = models.CharField(max_length=10, default='Active')

    def __str__(self):
        return self.ticker


class GrowthEstimate(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    date = models.DateField()
    growth_rate = models.FloatField()
    historical_rate = models.FloatField()
    yahoo_estimate = models.FloatField()
    google_trend_rate = models.FloatField()

    def __str__(self):
        return f'{self.stock} - {self.date} - {self.growth_rate:.2f}%'

    def save(self, *args, **kwargs):
        if not self.id:
            self.date = datetime.date.today()
        return super(GrowthEstimate, self).save(*args, **kwargs)


class Valuation(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    date = models.DateField(default=datetime.date.today)
    growth_rate = models.FloatField(default=0)
    per_share_value = models.FloatField(default=0)
    current_price = models.FloatField(default=0)
    implied_pe_ratio = models.FloatField(default=0)
    pe_ratio = models.FloatField(default=0)
    current_market_cap = models.FloatField(default=0)
    implied_market_cap = models.FloatField(default=0)
    shares_outstanding = models.FloatField(default=0)
    normalized_shares_outstanding = models.FloatField(default=0)
    terminal_growth_rate = models.FloatField(default=0)
    discount_rate = models.FloatField(default=0)
    ebitda = models.FloatField(default=0)
    interest_expense = models.FloatField(default=0)
    tax_provision = models.FloatField(default=0)
    eps = models.FloatField(default=0)
    free_cash_flow = models.FloatField(default=0)
    dcf = models.FloatField(default=0)

    def __str__(self):
        return f'{self.stock} - {self.date} - {self.per_share_value:.2f} USD'

    def save(self, *args, **kwargs):
        if not self.id:
            self.date = datetime.date.today()
        return super(Valuation, self).save(*args, **kwargs)


class CompanyTerm(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    term = models.CharField(max_length=100)

    def __str__(self):
        return f'{self.stock} - {self.term}'

