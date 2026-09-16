"""Full-product test dataset for FactoryManager AI.

Generates every table in the schema — masters, the Job Order spine, shop-floor
capture, the Actions/PDCA engine, Recovered Profit, Coach, Copilot, onboarding
and the Layer 5 plugins — for several plants over several months, then exports
it as PostgreSQL, CSV and Excel, plus the filled T1-T7 onboarding forms.

Every rupee figure is produced by ``app.kernel`` (the same pure functions the
API and the worker call), so the dataset cannot disagree with the product.

Run: ``python scripts/generate_dataset.py`` from ``backend/``.
"""
