from django.apps import AppConfig, apps
from django.db.models.signals import pre_save


class KtaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'kta'

    def ready(self):
        # Load Models
        from .models import Stock, Valuation, GrowthEstimate, CompanyTerm
        print(f'Models: {Stock}, {Valuation}, {GrowthEstimate}, {CompanyTerm}')

        # Setup Scheduler
        from .updater import start_scheduler
        start_scheduler()

