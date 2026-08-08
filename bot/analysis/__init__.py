"""Capa 1: series temporales, salud financiera y valuación histórica.

Todo determinístico y calculado desde los estados contables. Nada de esto
estima supuestos de mercado: lo que no se puede verificar queda en None y se
declara faltante.
"""

from .profile import CompanyProfile, CostOfCapitalInputs, GrowthOutlook, build_profile
from .series import AnnualPeriod, FinancialHistory, build_history
from .valuation import Multiples, ValuationBand, ValuationProfile, build_valuation

__all__ = [
    "CompanyProfile",
    "CostOfCapitalInputs",
    "GrowthOutlook",
    "build_profile",
    "AnnualPeriod",
    "FinancialHistory",
    "build_history",
    "Multiples",
    "ValuationBand",
    "ValuationProfile",
    "build_valuation",
]
